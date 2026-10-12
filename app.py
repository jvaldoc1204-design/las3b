import streamlit as st
import pandas as pd
import json
import os
import uuid
import base64
import re
import time
import copy
import difflib
import unicodedata
import hashlib
from datetime import date, datetime, timedelta

try:
    from zoneinfo import ZoneInfo
except ImportError:  # sin zonas horarias se usa la fecha del servidor
    ZoneInfo = None

try:
    import requests
except ImportError:  # sin esta librería las pestañas con IA quedan desactivadas
    requests = None

try:
    import gspread
    from google.oauth2.service_account import Credentials
except ImportError:  # sin estas librerías la app funciona en modo local (JSON)
    gspread = None
    Credentials = None

# =====================================================
#  INVENTARIO DE BODEGA - DISTRIBUIDORA LAS 3B
# =====================================================

VERSION = "beta 1.4"
ARCHIVO_DATOS = "inventario_las3b.json"
CATEGORIAS = ["Aseo", "Higiene", "Abarrotes", "Lácteos", "Bebestibles", "Otros"]
DIAS_ALERTA = 30      # "Por vencer" si faltan 30 días o menos
DIAS_URGENTE = 7      # "Urgente" si faltan 7 días o menos

import os as _os
_LOGO = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "logo.jpg")
_HAY_LOGO = _os.path.exists(_LOGO)

st.set_page_config(page_title="Las 3B - Bodega", page_icon=_LOGO if _HAY_LOGO else "📦", layout="centered")


def encabezado(subtitulo):
    """Logo + título (si no está el archivo logo.jpg, usa solo el título)."""
    if _HAY_LOGO:
        c_logo, c_txt = st.columns([1, 4], vertical_alignment="center")
        c_logo.image(_LOGO, width=90)
        c_txt.title("Bodega")
        c_txt.caption(subtitulo)
    else:
        st.title("📦 Las 3B · Bodega")
        st.caption(subtitulo)


# -----------------------------------------------------
#  ACCESO (contraseña = nombre de cualquiera del equipo)
# -----------------------------------------------------
USUARIOS_POR_DEFECTO = ["oliver", "javier", "jorge", "jefa", "jefe"]


def usuarios_permitidos():
    """Lista de nombres que pueden entrar. Se puede cambiar con 'usuarios' en los Secrets."""
    try:
        extra = st.secrets["usuarios"]
        if isinstance(extra, str):
            extra = [extra]
        lista = [str(u).strip().lower() for u in extra if str(u).strip()]
        if lista:
            return lista
    except Exception:
        pass
    return USUARIOS_POR_DEFECTO


def pantalla_acceso():
    """Si no hay sesión iniciada, muestra el acceso y detiene la app (no carga datos)."""
    if st.session_state.get("usuario"):
        return
    encabezado(f"Versión {VERSION}")
    with st.form("form_acceso"):
        clave = st.text_input("Ingrese contraseña", type="password")
        entrar = st.form_submit_button("Entrar")
    if entrar:
        nombre = clave.strip().lower()
        if nombre and nombre in usuarios_permitidos():
            st.session_state.usuario = nombre.capitalize()
            st.rerun()
        else:
            time.sleep(1)  # frena un poco los intentos al azar
            st.error("Contraseña incorrecta. Sea serio 😐")
    st.stop()


pantalla_acceso()


# -----------------------------------------------------
#  ALMACENAMIENTO
# -----------------------------------------------------
CAMPOS = ["id", "nombre", "categoria", "cajas", "vencimiento", "proveedor", "ingreso"]


def usar_sheets():
    """True si están configurados los Secrets de Google Sheets."""
    try:
        return gspread is not None and "gcp_service_account" in st.secrets
    except Exception:
        return False


@st.cache_resource
def obtener_hoja():
    permisos = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(
        dict(st.secrets["gcp_service_account"]), scopes=permisos
    )
    cliente = gspread.authorize(creds)
    libro = cliente.open_by_key(st.secrets["sheet_id"])
    try:
        hoja = libro.worksheet("lotes")
    except gspread.WorksheetNotFound:
        hoja = libro.add_worksheet(title="lotes", rows=1000, cols=len(CAMPOS))
    return hoja


def cargar_lotes():
    # --- Modo nube: Google Sheets ---
    if usar_sheets():
        try:
            valores = obtener_hoja().get_all_values()
        except Exception as e:
            st.error(f"No pude leer Google Sheets: {e}")
            return st.session_state.get("lotes", [])
        lotes = []
        for fila in valores[1:]:
            fila = fila + [""] * (len(CAMPOS) - len(fila))
            d = dict(zip(CAMPOS, fila))
            if not d["id"].strip():
                continue
            try:
                d["cajas"] = int(float(d["cajas"]))
            except ValueError:
                continue
            lotes.append(d)
        return lotes

    # --- Modo local: archivo JSON ---
    if os.path.exists(ARCHIVO_DATOS):
        try:
            with open(ARCHIVO_DATOS, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
    return []


def guardar_lotes(lotes):
    if usar_sheets():
        filas = [CAMPOS] + [[l.get(c, "") for c in CAMPOS] for l in lotes]
        try:
            hoja = obtener_hoja()
            hoja.clear()
            hoja.update(values=filas, range_name="A1", value_input_option="RAW")
        except Exception as e:
            st.error(f"No pude guardar en Google Sheets: {e}")
            return False
        return True

    with open(ARCHIVO_DATOS, "w", encoding="utf-8") as f:
        json.dump(lotes, f, ensure_ascii=False, indent=4)
    return True


# En modo nube se recarga la planilla en cada interacción, así todos ven datos frescos.
if usar_sheets() or "lotes" not in st.session_state:
    st.session_state.lotes = cargar_lotes()


def guardar():
    """Devuelve True solo si los datos realmente se guardaron."""
    return guardar_lotes(st.session_state.lotes)


def avisar(tipo, texto):
    """Guarda un mensaje para mostrarlo después del st.rerun()."""
    st.session_state.aviso = (tipo, texto)


# -----------------------------------------------------
#  FUNCIONES DE APOYO
# -----------------------------------------------------
SIN_FECHA = ""                 # un lote sin vencimiento guarda texto vacío
FECHA_LEJANA = "9999-12-31"    # solo para ordenar: los lotes sin fecha van al final
SENTINELA = date(9999, 12, 31)


def a_fecha(texto):
    """'YYYY-MM-DD' -> date. Devuelve None si el lote no tiene vencimiento."""
    texto = str(texto or "").strip()
    if not texto:
        return None
    return datetime.strptime(texto[:10], "%Y-%m-%d").date()


def clave_venc(lote):
    """Para ordenar lotes: primero los que vencen antes, al final los sin fecha."""
    return str(lote.get("vencimiento") or "").strip() or FECHA_LEJANA


def dias_restantes(lote):
    f = a_fecha(lote.get("vencimiento"))
    return None if f is None else (f - date.today()).days


def estado(dias):
    if dias is None or pd.isna(dias):
        return "⚪ Sin vencimiento"
    if dias < 0:
        return "🔴 Vencido"
    if dias <= DIAS_URGENTE:
        return "🟠 Urgente"
    if dias <= DIAS_ALERTA:
        return "🟡 Por vencer"
    return "🟢 OK"


def txt_venc(iso):
    """'vence el 15/03/2027' o 'sin vencimiento'."""
    f = a_fecha(iso)
    return "sin vencimiento" if f is None else f"que vence el {f.strftime('%d/%m/%Y')}"


def tabla_lotes(lotes):
    filas = []
    for l in lotes:
        d = dias_restantes(l)
        filas.append({
            "Producto": l["nombre"],
            "Categoría": l["categoria"],
            "Cajas": l["cajas"],
            "Vencimiento": a_fecha(l["vencimiento"]),
            "Días restantes": d,
            "Estado": estado(d),
            "Proveedor": l.get("proveedor", ""),
            "Ingreso": l.get("ingreso", ""),
            "_orden": a_fecha(l["vencimiento"]) or SENTINELA,
        })
    df = pd.DataFrame(filas)
    if not df.empty:
        df["Días restantes"] = pd.array(df["Días restantes"].tolist(), dtype="Int64")
    return df


def nombres_productos():
    return sorted({l["nombre"] for l in st.session_state.lotes}, key=str.lower)


def descontar_fifo(nombre, cantidad, lotes=None):
    """Saca cajas de un producto, primero las que vencen antes. Devuelve el detalle.

    Trabaja sobre la lista 'lotes' (por defecto el inventario real), modificándola en el lugar.
    """
    if lotes is None:
        lotes = st.session_state.lotes
    lotes_prod = sorted(
        [l for l in lotes if l["nombre"] == nombre],
        key=clave_venc,
    )
    faltan = int(cantidad)
    detalle = []
    for l in lotes_prod:
        if faltan == 0:
            break
        sacar = min(l["cajas"], faltan)
        l["cajas"] -= sacar
        faltan -= sacar
        detalle.append(f"{sacar} del lote {txt_venc(l['vencimiento'])}")
    lotes[:] = [l for l in lotes if l["cajas"] > 0]
    return detalle


# -----------------------------------------------------
#  IA (GEMINI): LECTURA DE FACTURAS Y RECONOCIMIENTO DE PRODUCTOS
# -----------------------------------------------------
def modelo_ia():
    """Modelo de Gemini. Se puede cambiar con 'gemini_model' en los Secrets."""
    try:
        return st.secrets["gemini_model"]
    except Exception:
        return "gemini-flash-latest"


def ia_disponible():
    try:
        return requests is not None and "gemini_api_key" in st.secrets
    except Exception:
        return False


def aviso_ia_no_disponible(clave="gemini_api_key", donde="aistudio.google.com/apikey"):
    st.warning(
        "Falta configurar la clave de la IA. En Streamlit Cloud entra a "
        f"Settings → Secrets y agrega una línea: {clave} = \"tu_clave\" "
        f"(la clave se crea en {donde})."
    )
    # Diagnóstico: solo muestra NOMBRES de claves, nunca sus valores.
    try:
        claves = sorted(st.secrets.keys())
    except Exception:
        claves = []
    try:
        dentro = clave in st.secrets["gcp_service_account"]
    except Exception:
        dentro = False
    if dentro:
        st.error(
            f"Encontré {clave} pegada DEBAJO de [gcp_service_account]. "
            "Muévela a la primera línea de los Secrets, antes de cualquier línea "
            "que empiece con [ y guarda."
        )
    elif requests is None:
        st.error("Falta la librería 'requests': agrégala a requirements.txt en GitHub.")
    else:
        st.caption(
            "Claves detectadas en Secrets: "
            + (", ".join(claves) if claves else "ninguna (los Secrets están vacíos o no se guardaron)")
        )


def llamar_gemini(contenido, tipo, instrucciones):
    """Envía una foto/PDF + instrucciones a Gemini y devuelve la respuesta JSON como dict."""
    partes_envio = []
    if contenido is not None:  # sin archivo = solo texto (lo usa el asistente como respaldo)
        datos = base64.standard_b64encode(contenido).decode("utf-8")
        partes_envio.append({"inline_data": {"mime_type": tipo, "data": datos}})
    partes_envio.append({"text": instrucciones})
    cabeceras = {"x-goog-api-key": st.secrets["gemini_api_key"],
                 "Content-Type": "application/json"}
    cuerpo = {
        "contents": [{"parts": partes_envio}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
    }

    # Si un modelo está saturado, se reintenta y luego se prueba el siguiente.
    modelos = []
    for m in [modelo_ia(), "gemini-flash-latest", "gemini-flash-lite-latest"]:
        if m not in modelos:
            modelos.append(m)

    TEMPORALES = (429, 500, 503, 504)
    respuesta = None
    ultimo_error = "sin respuesta"
    for modelo in modelos:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
        for intento in range(3):
            respuesta = requests.post(url, headers=cabeceras, json=cuerpo, timeout=120)
            if respuesta.status_code == 200:
                break
            ultimo_error = f"{modelo} -> {respuesta.status_code}: {respuesta.text[:200]}"
            if respuesta.status_code in TEMPORALES:
                time.sleep(2 * (intento + 1))  # espera 2, 4 y 6 segundos
                continue
            break
        if respuesta.status_code == 200:
            break
        if respuesta.status_code not in (404,) + TEMPORALES:
            # Clave inválida, imagen rechazada, etc.: probar otro modelo no ayuda.
            raise RuntimeError(f"Gemini respondió {ultimo_error}")
    if respuesta is None or respuesta.status_code != 200:
        raise RuntimeError(
            "Gemini está saturado o no disponible en este momento. "
            "Intenta de nuevo en unos minutos. Último error: " + ultimo_error
        )
    try:
        partes = respuesta.json()["candidates"][0]["content"]["parts"]
        texto = "".join(p.get("text", "") for p in partes)
    except (KeyError, IndexError, TypeError, ValueError):
        raise ValueError("Gemini no devolvió texto (¿foto ilegible o bloqueada?).")
    encontrado = re.search(r"\{.*\}", texto, re.S)
    if not encontrado:
        raise ValueError("La IA no devolvió datos legibles.")
    try:
        return json.loads(encontrado.group(0))
    except json.JSONDecodeError:
        raise ValueError("La IA devolvió una respuesta que no pude entender. Prueba con otra foto.")


def leer_factura(contenido, tipo):
    instrucciones = (
        "Esta es una factura o guía de despacho de una distribuidora en Chile. "
        "Extrae el nombre del proveedor y cada línea de producto con su cantidad. "
        "Clasifica cada producto en una de estas categorías: "
        + ", ".join(CATEGORIAS) + ". "
        "Si no estás seguro de la categoría, usa 'Otros'. "
        "No inventes productos ni cantidades: si una línea no se lee bien, omítela. "
        "Responde SOLO con JSON válido, sin texto adicional, con este formato exacto: "
        '{"proveedor": "", "productos": [{"nombre": "", "cantidad": 0, "categoria": ""}]}'
    )
    return llamar_gemini(contenido, tipo, instrucciones)


# -----------------------------------------------------
#  ASISTENTE DE ÓRDENES (texto y voz; Groq, con respaldo en Gemini)
# -----------------------------------------------------
def hoy_chile():
    try:
        if ZoneInfo is not None:
            return datetime.now(ZoneInfo("America/Santiago")).date()
    except Exception:
        pass
    return date.today()


def normalizar(texto):
    """Minúsculas, sin tildes ni signos: 'Coca-Cola 3L' -> 'coca cola 3l'."""
    t = unicodedata.normalize("NFKD", str(texto or ""))
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def resolver_producto(nombre, existentes):
    """Busca el producto escrito entre los que ya existen. Devuelve (nombre, existe)."""
    n = normalizar(nombre)
    if not n:
        return "", False
    mapa = {}
    for e in existentes:
        mapa.setdefault(normalizar(e), e)
    if n in mapa:
        return mapa[n], True
    if len(n) >= 3:
        contiene = [orig for norm, orig in mapa.items() if n in norm]
        if len(contiene) == 1:
            return contiene[0], True
    cercanos = difflib.get_close_matches(n, list(mapa.keys()), n=1, cutoff=0.85)
    if cercanos:
        return mapa[cercanos[0]], True
    return str(nombre).strip(), False


def sugerencias(nombre, existentes, max_n=4):
    n = normalizar(nombre)
    if not n:
        return []
    salida = [e for e in existentes if n in normalizar(e) or normalizar(e) in n]
    for c in difflib.get_close_matches(n, [normalizar(e) for e in existentes], n=max_n, cutoff=0.5):
        for e in existentes:
            if normalizar(e) == c and e not in salida:
                salida.append(e)
    return salida[:max_n]


def parsear_fecha(valor):
    """Devuelve 'YYYY-MM-DD' o None si no se entiende."""
    if not valor:
        return None
    try:
        return datetime.strptime(str(valor).strip()[:10], "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None


def entero_positivo(valor):
    try:
        n = int(float(valor))
    except (TypeError, ValueError):
        return None
    return n if n >= 1 else None


def fmt_fecha(iso):
    f = a_fecha(iso)
    return "sin vencimiento" if f is None else f.strftime("%d/%m/%Y")


def unir_lotes_iguales(lotes, nombre):
    """Si un producto queda con dos lotes de la misma fecha, los junta en uno."""
    vistos = {}
    for l in list(lotes):
        if l["nombre"] != nombre:
            continue
        if l["vencimiento"] in vistos:
            vistos[l["vencimiento"]]["cajas"] += l["cajas"]
            lotes.remove(l)
        else:
            vistos[l["vencimiento"]] = l


MSG_FALTA_FECHA = "falta la fecha de vencimiento. Dila, o toca «Ingresar sin fecha» si este producto no vence."


def _numeros(nombre):
    return set(re.findall(r"\d+", normalizar(nombre)))


def _parecidos(a, b):
    """True si dos nombres de producto probablemente son el mismo producto."""
    na, nb = normalizar(a), normalizar(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    ca, cb = _numeros(a), _numeros(b)
    if ca and cb and ca != cb:      # 'Coca-Cola 3L' y 'Coca-Cola 1.5L' NO son iguales
        return False
    corto, largo = (na, nb) if len(na) <= len(nb) else (nb, na)
    if len(corto) >= 4 and (largo.startswith(corto + " ") or largo.endswith(" " + corto)):
        return True
    return difflib.SequenceMatcher(None, na, nb).ratio() >= 0.88


def grupos_duplicados(lotes, incluir_lotes_multiples=False):
    """Agrupa productos que parecen repetidos.

    Devuelve una lista de grupos; cada grupo es una lista de nombres. Con
    incluir_lotes_multiples también aparecen productos de un solo nombre con varios lotes.
    """
    nombres = sorted({l["nombre"] for l in lotes}, key=str.lower)
    padre = {n: n for n in nombres}

    def raiz(x):
        while padre[x] != x:
            padre[x] = padre[padre[x]]
            x = padre[x]
        return x

    for i, a in enumerate(nombres):
        for b in nombres[i + 1:]:
            if _parecidos(a, b):
                padre[raiz(a)] = raiz(b)
    grupos = {}
    for n in nombres:
        grupos.setdefault(raiz(n), []).append(n)
    salida = []
    for g in grupos.values():
        if len(g) > 1:
            salida.append(sorted(g, key=str.lower))
        elif incluir_lotes_multiples and sum(1 for l in lotes if l["nombre"] == g[0]) > 1:
            salida.append(g)
    return sorted(salida, key=lambda g: g[0].lower())


def unir_grupo(lotes, nombres, canonico):
    """Junta todos los lotes de 'nombres' en UN lote con el nombre 'canonico'.

    Suma las cajas y deja la fecha de vencimiento MÁS TEMPRANA (los lotes sin fecha no cuentan;
    si ninguno tiene fecha queda sin fecha). Modifica 'lotes' en el lugar. Devuelve el lote resultante.
    """
    miembros = [l for l in lotes if l["nombre"] in nombres]
    if not miembros:
        return None
    con_fecha = sorted([l for l in miembros if l["vencimiento"]], key=clave_venc)
    base = (con_fecha or miembros)[0]
    resultado = {
        "id": base["id"],
        "nombre": canonico,
        "categoria": next((l["categoria"] for l in miembros if l["nombre"] == canonico), base["categoria"]),
        "cajas": sum(int(l["cajas"]) for l in miembros),
        "vencimiento": base["vencimiento"] if con_fecha else SIN_FECHA,
        "proveedor": next((l.get("proveedor", "") for l in [base] + miembros if l.get("proveedor")), ""),
        "ingreso": min([l["ingreso"] for l in miembros if l.get("ingreso")] or [""]),
    }
    ids = {l["id"] for l in miembros}
    pos = min(i for i, l in enumerate(lotes) if l["id"] in ids)
    lotes[:] = [l for l in lotes if l["id"] not in ids]
    lotes.insert(min(pos, len(lotes)), resultado)
    return resultado


def aplicar_accion(lotes, accion, hoy):
    """Aplica UNA acción sobre la lista 'lotes' (la modifica en el lugar).

    Devuelve (ok, texto, aviso). Si ok es False no se tocó nada.
    """
    tipo = str(accion.get("tipo") or "").strip().lower()
    existentes = sorted({l["nombre"] for l in lotes}, key=str.lower)
    nombre_in = str(accion.get("producto") or "").strip()
    if not nombre_in:
        return False, "Falta el nombre del producto.", None
    nombre, existe = resolver_producto(nombre_in, existentes)

    def no_encontrado():
        sug = sugerencias(nombre_in, existentes)
        extra = f" ¿Quisiste decir: {', '.join(sug)}?" if sug else ""
        return False, f"No encontré '{nombre_in}' en bodega.{extra}", None

    if tipo == "ingresar":
        cajas = entero_positivo(accion.get("cajas"))
        if cajas is None:
            return False, f"{nombre}: faltan las cajas.", None
        venc = parsear_fecha(accion.get("vencimiento"))
        if venc is None:
            sin_fecha_ok = bool(accion.get("sin_vencimiento")) or (
                existe and all(not l["vencimiento"] for l in lotes if l["nombre"] == nombre))
            if not sin_fecha_ok:
                return False, (f"{nombre}: {MSG_FALTA_FECHA}"), None
            venc = SIN_FECHA
        if existe:
            cat = next(l["categoria"] for l in lotes if l["nombre"] == nombre)
        else:
            cat = accion.get("categoria") if accion.get("categoria") in CATEGORIAS else "Otros"
        lote = next((l for l in lotes if l["nombre"] == nombre and l["vencimiento"] == venc), None)
        if lote:
            lote["cajas"] += cajas
        else:
            lotes.append({
                "id": uuid.uuid4().hex[:8],
                "nombre": nombre,
                "categoria": cat,
                "cajas": cajas,
                "vencimiento": venc,
                "proveedor": str(accion.get("proveedor") or "").strip(),
                "ingreso": hoy.isoformat(),
            })
        marca = "" if existe else f" (producto nuevo, {cat})"
        aviso = "⚠️ Esa fecha ya pasó." if venc and venc < hoy.isoformat() else None
        cola = "sin vencimiento" if not venc else f"vence {fmt_fecha(venc)}"
        return True, f"Ingresar {cajas} cajas de {nombre}{marca}, {cola}", aviso

    if tipo == "retirar":
        cajas = entero_positivo(accion.get("cajas"))
        if cajas is None:
            return False, f"{nombre_in}: faltan las cajas a retirar.", None
        if not existe:
            return no_encontrado()
        total = sum(l["cajas"] for l in lotes if l["nombre"] == nombre)
        if cajas > total:
            return False, f"{nombre}: pides {cajas} y solo hay {total}. Dígale que no queda 🤷", None
        descontar_fifo(nombre, cajas, lotes)
        return True, f"Retirar {cajas} cajas de {nombre} (quedan {total - cajas})", None

    if tipo == "cambiar_vencimiento":
        if not existe:
            return no_encontrado()
        nuevo = parsear_fecha(accion.get("vencimiento"))
        if nuevo is None and accion.get("sin_vencimiento"):
            nuevo = SIN_FECHA
        if nuevo is None:
            return False, f"{nombre}: falta la nueva fecha de vencimiento.", None
        lotes_p = sorted([l for l in lotes if l["nombre"] == nombre], key=clave_venc)
        actual = parsear_fecha(accion.get("vencimiento_actual"))
        if actual:
            objetivo = next((l for l in lotes_p if l["vencimiento"] == actual), None)
            if objetivo is None:
                return False, f"{nombre}: no hay un lote que venza el {fmt_fecha(actual)}.", None
        elif len(lotes_p) == 1:
            objetivo = lotes_p[0]
        else:
            fechas = ", ".join(fmt_fecha(l["vencimiento"]) for l in lotes_p)
            return False, f"{nombre} tiene varios lotes ({fechas}): indica cuál quieres cambiar.", None
        anterior = objetivo["vencimiento"]
        if anterior == nuevo:
            return False, f"{nombre} ya vence el {fmt_fecha(nuevo)}.", None
        otro = next((l for l in lotes_p if l is not objetivo and l["vencimiento"] == nuevo), None)
        if otro:
            otro["cajas"] += objetivo["cajas"]
            lotes.remove(objetivo)
        else:
            objetivo["vencimiento"] = nuevo
        aviso = "⚠️ Esa fecha ya pasó." if nuevo and nuevo < hoy.isoformat() else None
        return True, f"Cambiar vencimiento de {nombre}: {fmt_fecha(anterior)} → {fmt_fecha(nuevo)}", aviso

    if tipo == "renombrar":
        if not existe:
            return no_encontrado()
        nuevo_nombre = str(accion.get("nuevo_nombre") or "").strip()
        if not nuevo_nombre:
            return False, f"{nombre}: falta el nombre nuevo.", None
        if nuevo_nombre == nombre:
            return False, f"'{nombre}' ya se llama así.", None
        destino = next(
            (e for e in existentes if e != nombre and normalizar(e) == normalizar(nuevo_nombre)), None
        )
        final = destino or nuevo_nombre
        for l in lotes:
            if l["nombre"] == nombre:
                l["nombre"] = final
        unir_lotes_iguales(lotes, final)
        union = " (se une al producto que ya existía)" if destino else ""
        return True, f"Renombrar '{nombre}' → '{final}'{union}", None

    return False, f"No entendí qué hacer con '{nombre_in}' (acción '{tipo}').", None


def planificar(acciones, lotes_reales, hoy):
    """Prueba cada acción sobre una COPIA del inventario. Aquí no se guarda nada."""
    copia = copy.deepcopy(lotes_reales)
    plan = []
    for a in acciones[:20]:
        if not isinstance(a, dict):
            continue
        ok, texto, aviso = aplicar_accion(copia, a, hoy)
        plan.append({"accion": a, "ok": ok, "texto": texto, "aviso": aviso})
    return plan


def resumen_inventario(lotes):
    por = {}
    for l in lotes:
        p = por.setdefault(l["nombre"], {"producto": l["nombre"], "categoria": l["categoria"],
                                         "cajas_total": 0, "lotes": []})
        p["cajas_total"] += l["cajas"]
        p["lotes"].append({"vence": l["vencimiento"] or "sin vencimiento", "cajas": l["cajas"]})
    for p in por.values():
        p["lotes"].sort(key=lambda x: x["vence"] if x["vence"] != "sin vencimiento" else FECHA_LEJANA)
    return sorted(por.values(), key=lambda p: p["producto"].lower())


def groq_disponible():
    try:
        return requests is not None and "groq_api_key" in st.secrets
    except Exception:
        return False


def asistente_disponible():
    return groq_disponible() or ia_disponible()


def modelo_groq():
    """Modelo preferido de Groq. Se puede cambiar con 'groq_model' en los Secrets."""
    try:
        return st.secrets["groq_model"]
    except Exception:
        return None


def extraer_json(texto):
    encontrado = re.search(r"\{.*\}", texto or "", re.S)
    if not encontrado:
        raise ValueError("La IA no devolvió datos legibles.")
    try:
        return json.loads(encontrado.group(0))
    except json.JSONDecodeError:
        raise ValueError("La IA devolvió una respuesta que no pude entender. Prueba con otras palabras.")


def llamar_groq(sistema, usuario):
    """Pide a Groq una respuesta en JSON. Reintenta y prueba otros modelos si hace falta."""
    cabeceras = {"Authorization": f"Bearer {st.secrets['groq_api_key']}",
                 "Content-Type": "application/json"}
    modelos = []
    for m in [modelo_groq(), "llama-3.3-70b-versatile", "openai/gpt-oss-120b", "llama-3.1-8b-instant"]:
        if m and m not in modelos:
            modelos.append(m)

    TEMPORALES = (429, 500, 502, 503, 504)
    url = "https://api.groq.com/openai/v1/chat/completions"
    respuesta = None
    ultimo_error = "sin respuesta"
    for modelo in modelos:
        cuerpo = {
            "model": modelo,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": sistema},
                         {"role": "user", "content": usuario}],
        }
        for intento in range(2):
            respuesta = requests.post(url, headers=cabeceras, json=cuerpo, timeout=60)
            if respuesta.status_code == 200:
                break
            ultimo_error = f"{modelo} -> {respuesta.status_code}: {respuesta.text[:200]}"
            if respuesta.status_code in TEMPORALES:
                time.sleep(2 * (intento + 1))
                continue
            break
        if respuesta.status_code == 200:
            break
        if respuesta.status_code in (401, 403):
            raise RuntimeError(f"Groq rechazó la clave ({respuesta.status_code}). Revisa groq_api_key en los Secrets.")
        # Otros errores (modelo no disponible, saturación...): se prueba el siguiente modelo.
    if respuesta is None or respuesta.status_code != 200:
        raise RuntimeError("Groq no respondió. Último error: " + ultimo_error)
    try:
        texto = respuesta.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError):
        raise ValueError("Groq no devolvió texto.")
    return extraer_json(texto)


def llamar_llm_texto(sistema, usuario):
    """Usa Groq si hay clave; si falla (o no hay), prueba Gemini. Devuelve (resultado, proveedor)."""
    errores = []
    if groq_disponible():
        try:
            return llamar_groq(sistema, usuario), "Groq"
        except Exception as e:
            errores.append(f"Groq: {e}")
    if ia_disponible():
        try:
            return llamar_gemini(None, None, sistema + "\n\n" + usuario), "Gemini"
        except Exception as e:
            errores.append(f"Gemini: {e}")
    raise RuntimeError(" | ".join(errores) or "No hay ninguna clave de IA configurada.")


EXTENSIONES_AUDIO = {
    "audio/wav": "wav", "audio/x-wav": "wav", "audio/wave": "wav", "audio/webm": "webm",
    "audio/ogg": "ogg", "audio/mpeg": "mp3", "audio/mp4": "m4a", "audio/x-m4a": "m4a",
}


def transcribir_groq(contenido, tipo, pista):
    """Voz a texto con Whisper de Groq. 'pista' ayuda a acertar los nombres de productos."""
    url = "https://api.groq.com/openai/v1/audio/transcriptions"
    cabeceras = {"Authorization": f"Bearer {st.secrets['groq_api_key']}"}
    nombre_archivo = "audio." + EXTENSIONES_AUDIO.get((tipo or "").lower(), "wav")
    TEMPORALES = (429, 500, 502, 503, 504)
    respuesta = None
    ultimo_error = "sin respuesta"
    for modelo in ["whisper-large-v3-turbo", "whisper-large-v3"]:
        for intento in range(2):
            respuesta = requests.post(
                url,
                headers=cabeceras,
                files={"file": (nombre_archivo, contenido, tipo or "audio/wav")},
                data={"model": modelo, "language": "es", "response_format": "json",
                      "temperature": "0", "prompt": pista},
                timeout=60,
            )
            if respuesta.status_code == 200:
                break
            ultimo_error = f"{modelo} -> {respuesta.status_code}: {respuesta.text[:200]}"
            if respuesta.status_code in TEMPORALES:
                time.sleep(2 * (intento + 1))
                continue
            break
        if respuesta.status_code == 200:
            break
        if respuesta.status_code in (401, 403):
            raise RuntimeError(f"Groq rechazó la clave ({respuesta.status_code}). Revisa groq_api_key en los Secrets.")
    if respuesta is None or respuesta.status_code != 200:
        raise RuntimeError("Groq no pudo transcribir. Último error: " + ultimo_error)
    try:
        texto = str(respuesta.json().get("text") or "").strip()
    except (ValueError, AttributeError):
        texto = ""
    if not texto:
        raise ValueError("No se escuchó nada.")
    return texto


def transcribir_audio(contenido, tipo, nombres):
    """Convierte un audio en texto. Devuelve (texto, proveedor): Groq primero, Gemini de respaldo."""
    if not contenido or len(contenido) < 6000:
        raise ValueError("El audio es muy corto o no se escuchó nada. Acerca el micrófono e inténtalo de nuevo.")
    pista = ("Bodega de una distribuidora en Chile. Productos: " + ", ".join(nombres))[:600]
    errores = []
    if groq_disponible():
        try:
            return transcribir_groq(contenido, tipo, pista), "Groq"
        except Exception as e:
            errores.append(f"Groq: {e}")
    if ia_disponible():
        try:
            r = llamar_gemini(
                contenido, tipo or "audio/wav",
                "Transcribe fielmente este audio en español de Chile. Es una orden hablada en una bodega "
                "de distribuidora (productos, cajas, fechas). " + pista + " "
                'Responde SOLO con JSON: {"texto": ""}. Si no se entiende nada, deja texto vacío.',
            )
            texto = str(r.get("texto") or "").strip()
            if texto:
                return texto, "Gemini"
            errores.append("Gemini: no entendió el audio")
        except Exception as e:
            errores.append(f"Gemini: {e}")
    raise RuntimeError(" | ".join(errores) or "No hay ninguna clave de IA configurada.")


def interpretar_orden(texto, lotes):
    hoy = hoy_chile()
    sistema = (
        "Eres el asistente de bodega de la distribuidora Las 3B (Chile). "
        "Conviertes órdenes escritas por el personal en acciones sobre el inventario. "
        "Responde SOLO con un objeto JSON, sin texto adicional, con esta forma exacta: "
        '{"acciones": [{"tipo": "", "producto": "", "cajas": 0, "vencimiento": null, '
        '"vencimiento_actual": null, "sin_vencimiento": false, "categoria": "", "proveedor": "", "nuevo_nombre": ""}], '
        '"respuesta": ""}\n\n'
        "Tipos de acción:\n"
        '- "ingresar": llegó mercadería (llegaron, entraron, ingresa, agrega, recibí). '
        "Campos: producto, cajas, vencimiento, categoria, proveedor.\n"
        '- "retirar": se saca mercadería de bodega (saca, bajé, descuenta, salieron, despaché). '
        "Campos: producto, cajas.\n"
        '- "cambiar_vencimiento": corregir la fecha de un lote que ya existe. Campos: producto, '
        "vencimiento (la fecha NUEVA) y vencimiento_actual (la fecha vieja, solo si la orden la dice; si no, null). "
        'Para quitarle la fecha a un lote ("ya no vence") usa sin_vencimiento: true y vencimiento null.\n'
        '- "renombrar": cambiar el nombre de un producto. Campos: producto (nombre actual), nuevo_nombre.\n\n'
        "Reglas:\n"
        "1. Si la orden se refiere a algo que ya existe, usa EXACTAMENTE el nombre del inventario, "
        "aunque el usuario lo escriba abreviado, sin tildes o con otras mayúsculas. "
        'Un nombre nuevo solo en "ingresar" y solo si el producto claramente no existe.\n'
        "2. Las cantidades son cajas.\n"
        f"3. Fechas en formato YYYY-MM-DD. Hoy es {hoy.isoformat()}. En Chile las fechas se escriben día/mes/año. "
        "Si solo dan mes y año (ej. 'marzo 2027' o '03/2027') usa el último día de ese mes. "
        "Si dicen 'en 6 meses' calcúlalo desde hoy.\n"
        '4. NUNCA inventes una fecha de vencimiento: si en un "ingresar" no la dicen, pon null. '
        'Pon "sin_vencimiento": true SOLO si dicen que no vence / sin fecha, o si el producto claramente no '
        'caduca (papel higiénico, toallas de papel, bolsas, escobas, pañales, esponjas). Ante la duda, false.\n'
        "5. categoria debe ser una de: " + ", ".join(CATEGORIAS) + ". Elige la más lógica para productos nuevos.\n"
        "6. Si la orden es una pregunta sobre el inventario (cuántas cajas hay, qué vence pronto, etc.), "
        'deja "acciones" vacío y responde en "respuesta" usando SOLO los datos del inventario entregado. '
        "Si el dato no está, dilo.\n"
        '7. Si la orden es confusa o falta información importante (producto o cantidad), deja "acciones" vacío '
        'y en "respuesta" pregunta qué falta.\n'
        "8. Una orden puede traer varias acciones: devuélvelas todas, en el orden en que se dijeron.\n"
        "9. No agregues acciones que no se pidieron."
    )
    usuario = json.dumps(
        {"hoy": hoy.isoformat(), "inventario": resumen_inventario(lotes), "orden": texto[:600]},
        ensure_ascii=False,
    )
    resultado, proveedor = llamar_llm_texto(sistema, usuario)
    if not isinstance(resultado, dict):
        raise ValueError("La IA devolvió algo inesperado.")
    return resultado, proveedor


def elegir_archivo(clave):
    """Pide una foto o PDF. En el celular, el selector ofrece sacar la foto con la cámara."""
    return st.file_uploader(
        "Foto o PDF de la factura", type=["jpg", "jpeg", "png", "webp", "pdf"], key=f"up_{clave}"
    )


# -----------------------------------------------------
#  BARRA LATERAL
# -----------------------------------------------------
with st.sidebar:
    st.header("📦 Las 3B")
    st.caption(f"Versión {VERSION}")
    st.caption(f"👤 {st.session_state.get('usuario', '')}")
    if st.button("🔒 Cerrar sesión", key="salir_lateral"):
        st.session_state.clear()
        st.rerun()
    if usar_sheets():
        st.success("☁️ Datos guardados en Google Sheets")
    else:
        st.warning("💾 Modo local: los datos se guardan en un archivo JSON")


# -----------------------------------------------------
#  ENCABEZADO
# -----------------------------------------------------
encabezado(f"Versión {VERSION} · Hola, {st.session_state.get('usuario', '')} 👋 · ¡Sea serio! 😐")

if "aviso" in st.session_state:
    tipo, texto = st.session_state.pop("aviso")
    getattr(st, tipo)(texto)

lotes = st.session_state.lotes
vencidos = sum(1 for l in lotes if (dias_restantes(l) is not None and dias_restantes(l) < 0))
por_vencer = sum(1 for l in lotes if (dias_restantes(l) is not None and 0 <= dias_restantes(l) <= DIAS_ALERTA))
st.markdown(
    f"**{sum(l['cajas'] for l in lotes)} cajas** · {len(nombres_productos())} productos · "
    f"🟡 {por_vencer} por vencer · 🔴 {vencidos} vencidos"
)

(tab_asistente, tab_inventario, tab_factura, tab_venc,
 tab_retirar, tab_ingresar, tab_editar, tab_dup) = st.tabs([
    "🤖 Asistente",
    "📦 Inventario",
    "🧾 Factura",
    "⏰ Vencimientos",
    "📤 Retirar",
    "➕ Ingresar",
    "✏️ Editar",
    "🧹 Duplicados",
])


# -----------------------------------------------------
#  1. INVENTARIO (simple, con botón para ver más)
# -----------------------------------------------------
with tab_inventario:
    if not lotes:
        st.info("La bodega está vacía. Ingresa mercadería hablándole al 🤖 Asistente o con 🧾 Factura.")
    else:
        busqueda = st.text_input("🔎 Buscar producto", key="inv_busqueda")
        df = tabla_lotes(lotes)
        if busqueda.strip():
            df = df[df["Producto"].str.contains(busqueda.strip(), case=False, na=False)]

        if df.empty:
            st.warning("No hay productos con ese nombre.")
        else:
            simple = (
                df.groupby("Producto", as_index=False)
                .agg(Cajas=("Cajas", "sum"), Vence=("_orden", "min"))
                .sort_values("Producto")
            )
            simple["Vence"] = simple["Vence"].map(lambda d: None if d == SENTINELA else d)
            st.dataframe(simple, hide_index=True)
            st.caption("Vence en blanco = producto sin fecha de vencimiento.")

        if st.toggle("➕ Ver más detalle", key="inv_detalle"):
            st.divider()
            filtro_cat = st.selectbox("Categoría:", ["Todas"] + CATEGORIAS, key="inv_cat")
            df_d = tabla_lotes(lotes)
            if filtro_cat != "Todas":
                df_d = df_d[df_d["Categoría"] == filtro_cat]

            if df_d.empty:
                st.warning("No hay productos en esa categoría.")
            else:
                detalle = (
                    df_d.groupby(["Producto", "Categoría"], as_index=False)
                    .agg(**{"Cajas": ("Cajas", "sum"),
                            "Lotes": ("Cajas", "count"),
                            "Próx. vence": ("_orden", "min")})
                    .sort_values(["Categoría", "Producto"])
                )
                detalle["Próx. vence"] = detalle["Próx. vence"].map(
                    lambda d: None if d == SENTINELA else d)
                st.dataframe(detalle, hide_index=True)
                with st.expander("Ver lote por lote"):
                    st.dataframe(
                        df_d.sort_values(["Producto", "_orden"])[
                            ["Producto", "Cajas", "Vencimiento", "Días restantes", "Estado", "Proveedor"]],
                        hide_index=True,
                    )

            st.write("**Cajas por categoría**")
            por_cat = (
                tabla_lotes(lotes)
                .groupby("Categoría")["Cajas"].sum()
                .reindex(CATEGORIAS, fill_value=0)
            )
            st.bar_chart(por_cat)


# -----------------------------------------------------
#  2. ESCANEAR FACTURA CON IA
# -----------------------------------------------------
with tab_factura:
    st.subheader("Escanear factura")

    if not ia_disponible():
        aviso_ia_no_disponible()
    else:
        st.caption(
            "Foto de la factura → la IA arma una tabla → tú la revisas, pones los "
            "vencimientos y confirmas. Nada entra hasta el botón final."
        )
        st.info(
            "Con la clave gratuita de Gemini, Google puede usar lo que envías para "
            "mejorar sus productos. No subas facturas con datos que no quieras compartir."
        )

        archivo = elegir_archivo("factura")

        if archivo is not None and st.button("🔍 Leer factura"):
            with st.spinner("Leyendo la factura..."):
                try:
                    tipo_arch = archivo.type or "image/jpeg"
                    st.session_state.factura = leer_factura(archivo.getvalue(), tipo_arch)
                    st.session_state.factura_n = st.session_state.get("factura_n", 0) + 1
                except Exception as e:
                    st.error(f"No pude leer la factura: {e}")

        if "factura" in st.session_state:
            fac = st.session_state.factura
            st.divider()
            st.write("**Revisa lo que leyó la IA** (puedes editar, agregar o borrar filas):")
            proveedor_f = st.text_input("Proveedor:", value=str(fac.get("proveedor") or ""))

            filas = []
            for p in fac.get("productos", []):
                cat = p.get("categoria")
                try:
                    cant = max(int(float(p.get("cantidad") or 1)), 1)
                except (TypeError, ValueError):
                    cant = 1
                filas.append({
                    "Incluir": True,
                    "Producto": str(p.get("nombre") or "").strip(),
                    "Categoría": cat if cat in CATEGORIAS else "Otros",
                    "Cajas": cant,
                    "Vencimiento": None,
                })
            df_f = pd.DataFrame(
                filas, columns=["Incluir", "Producto", "Categoría", "Cajas", "Vencimiento"]
            )

            editado = st.data_editor(
                df_f,
                num_rows="dynamic",
                hide_index=True,
                key=f"editor_factura_{st.session_state.get('factura_n', 0)}",
                column_config={
                    "Incluir": st.column_config.CheckboxColumn("Incluir"),
                    "Categoría": st.column_config.SelectboxColumn("Categoría", options=CATEGORIAS),
                    "Cajas": st.column_config.NumberColumn(
                        "Cajas", min_value=1, step=1,
                        help="Revisa que sean cajas y no unidades sueltas."),
                    "Vencimiento": st.column_config.DateColumn(
                        "Vencimiento", format="DD/MM/YYYY",
                        help="La factura casi nunca lo trae: complétalo tú."),
                },
            )

            sin_f_vista = [
                str(f["Producto"]) for _, f in editado.iterrows()
                if bool(f["Incluir"]) and pd.isna(f["Vencimiento"]) and str(f["Producto"] or "").strip()
            ]
            omitir_fecha = False
            if sin_f_vista:
                st.warning(
                    f"📅 Te falta la fecha de vencimiento en {len(sin_f_vista)} producto(s): "
                    + ", ".join(sin_f_vista[:6]) + ("…" if len(sin_f_vista) > 6 else "")
                )
                omitir_fecha = st.checkbox(
                    "Ingresar sin fecha (son productos que no vencen, como papel higiénico)",
                    key="fac_omitir_fecha",
                )

            c_ok, c_cancel = st.columns(2)
            confirmar_f = c_ok.button("✅ Ingresar", type="primary")
            cancelar_f = c_cancel.button("Descartar")

            if cancelar_f:
                del st.session_state.factura
                st.rerun()

            if confirmar_f:
                errores, nuevos, sin_fecha_f = [], [], []
                for i, fila in editado.iterrows():
                    if not bool(fila["Incluir"]):
                        continue
                    nombre_f = str(fila["Producto"] or "").strip()
                    if not nombre_f or nombre_f.lower() == "nan":
                        errores.append(f"Fila {i + 1}: falta el nombre del producto.")
                        continue
                    if pd.isna(fila["Cajas"]) or int(fila["Cajas"]) < 1:
                        errores.append(f"Fila {i + 1} ({nombre_f}): faltan las cajas.")
                        continue
                    if pd.isna(fila["Vencimiento"]):
                        sin_fecha_f.append(nombre_f)
                        venc_f = SIN_FECHA
                    else:
                        venc_f = pd.to_datetime(fila["Vencimiento"]).date().isoformat()
                    nuevos.append({
                        "nombre": nombre_f,
                        "categoria": fila["Categoría"] if fila["Categoría"] in CATEGORIAS else "Otros",
                        "cajas": int(fila["Cajas"]),
                        "vencimiento": venc_f,
                    })

                if sin_fecha_f and not omitir_fecha:
                    errores.append(
                        "Sin fecha de vencimiento: " + ", ".join(sin_fecha_f)
                        + ". Complétala en la tabla o marca «Ingresar sin fecha» arriba.")
                if errores:
                    st.error("Sea serio, faltan datos 😐\n\n" + "\n".join(f"- {e}" for e in errores))
                elif not nuevos:
                    st.warning("No hay filas marcadas para ingresar.")
                else:
                    for n in nuevos:
                        existente = next(
                            (l for l in st.session_state.lotes
                             if l["nombre"].lower() == n["nombre"].lower()
                             and l["vencimiento"] == n["vencimiento"]),
                            None,
                        )
                        if existente:
                            existente["cajas"] += n["cajas"]
                        else:
                            st.session_state.lotes.append({
                                "id": uuid.uuid4().hex[:8],
                                "nombre": n["nombre"],
                                "categoria": n["categoria"],
                                "cajas": n["cajas"],
                                "vencimiento": n["vencimiento"],
                                "proveedor": proveedor_f.strip(),
                                "ingreso": date.today().isoformat(),
                            })
                    if guardar():
                        del st.session_state.factura
                        avisar("success", f"Factura ingresada: {len(nuevos)} productos agregados 📦")
                        st.rerun()


# -----------------------------------------------------
#  3. RETIRAR / REPONER
# -----------------------------------------------------
with tab_retirar:
    st.subheader("Sacar cajas de bodega")
    st.caption("Se descuentan primero las cajas que vencen antes (FIFO).")

    productos = nombres_productos()
    if not productos:
        st.warning("No hay productos en bodega.")
    else:
        producto = st.selectbox("Producto:", productos, key="retirar_producto")
        total = sum(l["cajas"] for l in st.session_state.lotes if l["nombre"] == producto)
        st.write(f"En bodega hay **{total} cajas** de {producto}.")
        with st.expander("Ver lotes"):
            lotes_prod = [l for l in st.session_state.lotes if l["nombre"] == producto]
            st.dataframe(
                tabla_lotes(lotes_prod).sort_values("_orden")[
                    ["Cajas", "Vencimiento", "Días restantes", "Estado"]],
                hide_index=True,
            )

        retirar = st.number_input("Cajas a retirar:", min_value=1, step=1, key="retirar_cant")

        if st.button("📤 Retirar de bodega", type="primary"):
            if retirar > total:
                st.error(f"Dígale que no queda: solo hay {total} cajas 🤷")
            else:
                detalle = descontar_fifo(producto, retirar)
                if guardar():
                    avisar("success", f"Retiradas {int(retirar)} cajas de '{producto}': " + "; ".join(detalle) + ".")
                    st.rerun()


# -----------------------------------------------------
#  ASISTENTE (voz y texto) - pestaña principal
# -----------------------------------------------------
with tab_asistente:
    st.subheader("Asistente 🤖")

    if not asistente_disponible():
        aviso_ia_no_disponible("groq_api_key", "console.groq.com/keys")
    else:
        st.caption(
            "Habla o escribe lo que pasó en la bodega, como se lo dirías a alguien. "
            "Te muestro lo que entendí y solo se guarda si confirmas."
        )
        with st.expander("Ejemplos de órdenes"):
            st.markdown(
                "- *Llegaron 10 cajas de leche entera que vencen el 15/03/2027*\n"
                "- *Saca 3 cajas de Super 8 y 2 de Coca-Cola*\n"
                "- *El Chocman vence el 20 de mayo de 2027*\n"
                "- *Cambia el nombre de Muibon 50g a Muibon*\n"
                "- *¿Cuántas cajas de arroz quedan?* o *¿qué vence este mes?*"
            )

        n_asist = st.session_state.get("asist_n", 0)
        tiene_voz = hasattr(st, "audio_input")

        if tiene_voz:
            audio = st.audio_input(
                "🎤 Toca para hablar y toca de nuevo para terminar",
                key=f"asist_audio_{n_asist}",
            )
            if audio is not None:
                datos_audio = audio.getvalue()
                huella = hashlib.md5(datos_audio).hexdigest()
                if st.session_state.get("asist_audio_huella") != huella:
                    st.session_state.asist_audio_huella = huella  # así el mismo audio no se procesa dos veces
                    with st.spinner("Escuchando..."):
                        try:
                            texto_voz, _ = transcribir_audio(
                                datos_audio, getattr(audio, "type", None), nombres_productos()
                            )
                            res, quien = interpretar_orden(texto_voz, st.session_state.lotes)
                            st.session_state.asist = {
                                "orden": texto_voz, "res": res, "quien": quien, "voz": True,
                            }
                        except Exception as e:
                            st.session_state.pop("asist", None)
                            st.error(f"No pude entender el audio: {e}")

        with st.expander("⌨️ Escribir la orden", expanded=not tiene_voz):
            with st.form(f"form_asistente_{n_asist}"):
                orden = st.text_area(
                    "Orden:", height=100, key=f"asist_texto_{n_asist}",
                    placeholder="Ej: llegaron 8 cajas de Super 8 que vencen en marzo de 2027",
                )
                interpretar = st.form_submit_button("🤖 Interpretar")

        if interpretar:
            if not orden.strip():
                st.warning("Escribe una orden primero 😐")
            else:
                with st.spinner("Pensando..."):
                    try:
                        res, quien = interpretar_orden(orden.strip(), st.session_state.lotes)
                        st.session_state.asist = {"orden": orden.strip(), "res": res, "quien": quien}
                    except Exception as e:
                        st.session_state.pop("asist", None)
                        st.error(f"No pude interpretar la orden: {e}")

        if "asist" in st.session_state:
            datos_a = st.session_state.asist
            respuesta_a = str(datos_a["res"].get("respuesta") or "").strip()
            acciones_a = datos_a["res"].get("acciones") or []
            if not isinstance(acciones_a, list):
                acciones_a = []

            st.divider()
            st.caption(
                f"{'🎤 Escuché' if datos_a.get('voz') else 'Orden'}: «{datos_a['orden']}» "
                f"· interpretada con {datos_a['quien']}"
            )
            if respuesta_a:
                st.info(respuesta_a)
                if not acciones_a:
                    st.caption("Respuesta de la IA según el inventario. Si es importante, confírmala en 📦 Inventario.")

            validas_a = []
            if acciones_a:
                hoy_a = hoy_chile()
                plan_a = planificar(acciones_a, st.session_state.lotes, hoy_a)
                st.write("**Esto es lo que entendí:**")
                for item in plan_a:
                    st.markdown(f"{'✅' if item['ok'] else '❌'} {item['texto']}")
                    if item["aviso"]:
                        st.caption(item["aviso"])
                validas_a = [i for i in plan_a if i["ok"]]
                faltan_fecha = [i for i in plan_a
                                if not i["ok"] and i["texto"].endswith(MSG_FALTA_FECHA)]
                if faltan_fecha:
                    if st.button(f"📅 Ingresar sin fecha ({len(faltan_fecha)})", key="asist_sinfecha"):
                        for i in faltan_fecha:
                            i["accion"]["sin_vencimiento"] = True
                        st.rerun()
                if not validas_a:
                    st.warning("Ninguna acción se puede aplicar. Corrige la orden y vuelve a interpretarla.")
            elif not respuesta_a:
                st.warning("No entendí la orden. Prueba escribiéndola de otra forma.")

            b_ok, b_no = st.columns(2)
            confirmar_a = b_ok.button(
                f"✅ Confirmar ({len(validas_a)})", type="primary",
                disabled=not validas_a, key="asist_ok",
            )
            descartar_a = b_no.button("Descartar", key="asist_no")

            if descartar_a:
                del st.session_state.asist
                st.session_state.asist_n = n_asist + 1
                st.rerun()

            if confirmar_a and validas_a:
                for item in validas_a:
                    aplicar_accion(st.session_state.lotes, item["accion"], hoy_a)
                if guardar():
                    quien_usuario = st.session_state.get("usuario", "")
                    resumen_a = "; ".join(i["texto"] for i in validas_a)
                    del st.session_state.asist
                    st.session_state.asist_n = n_asist + 1
                    avisar("success", f"Listo{', ' + quien_usuario if quien_usuario else ''} 👍 {resumen_a}.")
                    st.rerun()


# -----------------------------------------------------
#  5. VENCIMIENTOS (por defecto solo lo que importa)
# -----------------------------------------------------
with tab_venc:
    if not lotes:
        st.info("Aún no hay lotes registrados.")
    else:
        ver_todos = st.toggle("➕ Ver todos los lotes", key="venc_todos")
        df_v = tabla_lotes(lotes).sort_values("_orden")
        sin_f = int(df_v["Vencimiento"].isna().sum())
        df_v = df_v[df_v["Vencimiento"].notna()]
        if not ver_todos:
            df_v = df_v[df_v["Días restantes"] <= DIAS_ALERTA]

        if df_v.empty:
            st.success(f"Nada vence en los próximos {DIAS_ALERTA} días 🎉")
        else:
            st.dataframe(
                df_v[["Estado", "Producto", "Cajas", "Vencimiento", "Días restantes"]],
                hide_index=True,
            )
        st.caption(
            f"🟠 {DIAS_URGENTE} días o menos · 🟡 {DIAS_ALERTA} días o menos · 🔴 ya vencido"
        )
        if sin_f:
            st.caption(f"⚪ {sin_f} lote(s) sin fecha de vencimiento no se vigilan aquí.")


# -----------------------------------------------------
#  5. INGRESAR MERCADERÍA (manual)
# -----------------------------------------------------
with tab_ingresar:
    st.subheader("Ingreso manual")
    st.caption("Para cuando no hay factura a mano. Ejemplo: llega McKay.")

    def ingresar_lote(nombre_l, categoria_l, cajas_l, venc_txt_l, proveedor_l):
        """Suma a un lote igual (mismo nombre y fecha) o crea uno nuevo. Devuelve True si se guardó."""
        existente = next(
            (l for l in st.session_state.lotes
             if l["nombre"].lower() == nombre_l.lower() and l["vencimiento"] == venc_txt_l),
            None,
        )
        if existente:
            existente["cajas"] += int(cajas_l)
        else:
            st.session_state.lotes.append({
                "id": uuid.uuid4().hex[:8],
                "nombre": nombre_l,
                "categoria": categoria_l,
                "cajas": int(cajas_l),
                "vencimiento": venc_txt_l,
                "proveedor": proveedor_l.strip(),
                "ingreso": date.today().isoformat(),
            })
        return guardar()

    pend = st.session_state.get("ing_pendiente")
    if pend:
        st.warning(
            f"📅 Te faltó la fecha de vencimiento de **{pend['nombre']}** ({pend['cajas']} cajas). "
            "¿La quieres agregar o la omites?"
        )
        c_si, c_no = st.columns(2)
        if c_si.button("📅 Agregar fecha", key="ing_agregar_fecha"):
            st.session_state.pop("ing_pendiente", None)
            st.rerun()
        if c_no.button("Omitir (no vence)", type="primary", key="ing_omitir_fecha"):
            if ingresar_lote(pend["nombre"], pend["categoria"], pend["cajas"], SIN_FECHA, pend["proveedor"]):
                st.session_state.pop("ing_pendiente", None)
                avisar("success", f"¡Listo! {pend['cajas']} cajas de '{pend['nombre']}' ingresadas sin vencimiento. Oliver mi peyito 🐶")
                st.rerun()
    else:
        with st.form("form_ingreso", clear_on_submit=True):
            nombre = st.text_input("Nombre del producto:")
            categoria = st.selectbox("Categoría:", CATEGORIAS)
            cajas = st.number_input("Cajas que llegaron:", min_value=1, step=1)
            try:
                vencimiento = st.date_input("Fecha de vencimiento:", value=None, format="DD/MM/YYYY")
            except Exception:  # versiones viejas de Streamlit no permiten dejarla vacía
                vencimiento = st.date_input("Fecha de vencimiento:", value=date.today(), format="DD/MM/YYYY")
            sin_venc = st.checkbox("No vence (papel higiénico, bolsas, etc.)")
            proveedor = st.text_input("Proveedor (opcional):", placeholder="Ej: McKay")

            enviar = st.form_submit_button("Agregar a bodega")

            if enviar:
                nombre_limpio = nombre.strip()
                if not nombre_limpio:
                    st.error("Sea serio: falta el nombre del producto 😐")
                elif vencimiento is None and not sin_venc:
                    # Se olvidó la fecha: se pregunta si se agrega u omite
                    st.session_state.ing_pendiente = {
                        "nombre": nombre_limpio, "categoria": categoria,
                        "cajas": int(cajas), "proveedor": proveedor,
                    }
                    st.rerun()
                else:
                    venc_txt = SIN_FECHA if sin_venc else vencimiento.isoformat()
                    if ingresar_lote(nombre_limpio, categoria, cajas, venc_txt, proveedor):
                        st.success(f"¡Listo! {int(cajas)} cajas de '{nombre_limpio}' ingresadas. Oliver mi peyito 🐶")


# -----------------------------------------------------
#  6. EDITAR O BORRAR LOTE
# -----------------------------------------------------
with tab_editar:
    st.subheader("Corregir un lote")

    if not lotes:
        st.warning("No hay lotes para editar.")
    else:
        ordenados = sorted(lotes, key=lambda l: (l["nombre"].lower(), clave_venc(l)))
        etiquetas = {
            l["id"]: f"{l['nombre']} · {'vence ' + fmt_fecha(l['vencimiento']) if l['vencimiento'] else 'sin vencimiento'} · {l['cajas']} cajas"
            for l in ordenados
        }
        id_sel = st.selectbox(
            "Lote:", list(etiquetas.keys()),
            format_func=lambda i: etiquetas[i], key="editar_lote",
        )
        lote = next(l for l in st.session_state.lotes if l["id"] == id_sel)

        with st.form(f"form_editar_{id_sel}"):
            nuevo_nombre = st.text_input("Nombre:", value=lote["nombre"])
            nueva_cat = st.selectbox(
                "Categoría:", CATEGORIAS,
                index=CATEGORIAS.index(lote["categoria"]) if lote["categoria"] in CATEGORIAS else 0,
            )
            nuevas_cajas = st.number_input("Cajas:", min_value=1, value=int(lote["cajas"]), step=1)
            nuevo_venc = st.date_input(
                "Vencimiento:", value=a_fecha(lote["vencimiento"]) or date.today(), format="DD/MM/YYYY",
            )
            quitar_venc = st.checkbox(
                "Sin vencimiento (ignora la fecha de arriba)",
                value=not lote["vencimiento"],
            )
            guardar_cambios = st.form_submit_button("Guardar cambios")

            if guardar_cambios:
                if not nuevo_nombre.strip():
                    st.error("Sea serio: el nombre no puede estar vacío 😐")
                else:
                    lote["nombre"] = nuevo_nombre.strip()
                    lote["categoria"] = nueva_cat
                    lote["cajas"] = int(nuevas_cajas)
                    lote["vencimiento"] = SIN_FECHA if quitar_venc else nuevo_venc.isoformat()
                    if guardar():
                        avisar("success", "Lote actualizado ✅")
                        st.rerun()

        st.divider()
        confirmar = st.checkbox("Confirmo que quiero borrar este lote de forma permanente")
        if st.button("🗑️ Borrar lote", disabled=not confirmar):
            st.session_state.lotes = [l for l in st.session_state.lotes if l["id"] != id_sel]
            if guardar():
                avisar("success", "Lote eliminado.")
                st.rerun()


# -----------------------------------------------------
#  7. DUPLICADOS (unir productos repetidos)
# -----------------------------------------------------
with tab_dup:
    st.subheader("Unir duplicados")
    st.caption(
        "Busca productos que parecen el mismo escrito distinto (ej. «Muibon» y «Muibon 50g»). "
        "Al unirlos se suman las cajas y queda la fecha de vencimiento MÁS TEMPRANA."
    )
    if not lotes:
        st.info("Aún no hay lotes registrados.")
    else:
        con_multi = st.toggle(
            "Incluir productos con varios lotes de distinta fecha", key="dup_multi",
            help="Junta todos los lotes de un producto en uno solo con la fecha más próxima.",
        )
        grupos = grupos_duplicados(st.session_state.lotes, con_multi)
        if not grupos:
            st.success("No encontré duplicados 🎉")
        else:
            elegidos = []
            for gi, g in enumerate(grupos):
                miembros = [l for l in st.session_state.lotes if l["nombre"] in g]
                con_fecha = sorted([l for l in miembros if l["vencimiento"]], key=clave_venc)
                total_cajas = sum(int(l["cajas"]) for l in miembros)
                with st.container(border=True):
                    canon = st.selectbox(
                        "Nombre final:", g, index=min(range(len(g)), key=lambda i: len(g[i])),
                        key=f"dup_nombre_{gi}_{'|'.join(g)}",
                    )
                    st.dataframe(
                        tabla_lotes(miembros).sort_values("_orden")[
                            ["Producto", "Cajas", "Vencimiento", "Estado"]],
                        hide_index=True,
                    )
                    cola = f"vence {fmt_fecha(con_fecha[0]['vencimiento'])}" if con_fecha else "sin vencimiento"
                    st.markdown(f"➡️ **{canon}** · {total_cajas} cajas · {cola}")
                    if st.checkbox("Unir este grupo", key=f"dup_ok_{gi}_{'|'.join(g)}"):
                        elegidos.append((g, canon))
            if st.button(f"🧹 Unir seleccionados ({len(elegidos)})", type="primary",
                         disabled=not elegidos, key="dup_unir"):
                for g, canon in elegidos:
                    unir_grupo(st.session_state.lotes, g, canon)
                if guardar():
                    avisar("success", f"Listo: {len(elegidos)} grupo(s) unido(s) 🧹")
                    st.rerun()


# -----------------------------------------------------
#  PIE DE PÁGINA
# -----------------------------------------------------
st.divider()
if st.button("🔒 Cerrar sesión", key="salir_pie"):
    st.session_state.clear()
    st.rerun()
st.caption(f"Las 3B · Versión {VERSION} · Hecho con cariño · 67 🫳🫴")
