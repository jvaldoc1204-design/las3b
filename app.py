import streamlit as st
import pandas as pd
import json
import os
import uuid
import base64
import re
import time
from datetime import date, datetime, timedelta

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

VERSION = "beta 1.1"
ARCHIVO_DATOS = "inventario_las3b.json"
CATEGORIAS = ["Aseo", "Higiene", "Abarrotes", "Lácteos", "Bebestibles", "Otros"]
DIAS_ALERTA = 30      # "Por vencer" si faltan 30 días o menos
DIAS_URGENTE = 7      # "Urgente" si faltan 7 días o menos

st.set_page_config(page_title="Las 3B - Bodega", page_icon="📦", layout="centered")


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
    st.title("📦 Las 3B · Bodega")
    st.caption(f"Versión {VERSION}")
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
def a_fecha(texto):
    return datetime.strptime(texto, "%Y-%m-%d").date()


def dias_restantes(lote):
    return (a_fecha(lote["vencimiento"]) - date.today()).days


def estado(dias):
    if dias < 0:
        return "🔴 Vencido"
    if dias <= DIAS_URGENTE:
        return "🟠 Urgente"
    if dias <= DIAS_ALERTA:
        return "🟡 Por vencer"
    return "🟢 OK"


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
        })
    return pd.DataFrame(filas)


def nombres_productos():
    return sorted({l["nombre"] for l in st.session_state.lotes}, key=str.lower)


def descontar_fifo(nombre, cantidad):
    """Saca cajas de un producto, primero las que vencen antes. Devuelve el detalle."""
    lotes_prod = sorted(
        [l for l in st.session_state.lotes if l["nombre"] == nombre],
        key=lambda l: l["vencimiento"],
    )
    faltan = int(cantidad)
    detalle = []
    for l in lotes_prod:
        if faltan == 0:
            break
        sacar = min(l["cajas"], faltan)
        l["cajas"] -= sacar
        faltan -= sacar
        detalle.append(f"{sacar} del lote que vence el {a_fecha(l['vencimiento']).strftime('%d/%m/%Y')}")
    st.session_state.lotes = [l for l in st.session_state.lotes if l["cajas"] > 0]
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


def aviso_ia_no_disponible():
    st.warning(
        "Falta configurar la clave de la IA. En Streamlit Cloud entra a "
        "Settings → Secrets y agrega una línea: gemini_api_key = \"tu_clave\" "
        "(la clave se crea en aistudio.google.com/apikey)."
    )
    # Diagnóstico: solo muestra NOMBRES de claves, nunca sus valores.
    try:
        claves = sorted(st.secrets.keys())
    except Exception:
        claves = []
    try:
        dentro = "gemini_api_key" in st.secrets["gcp_service_account"]
    except Exception:
        dentro = False
    if dentro:
        st.error(
            "Encontré gemini_api_key pegada DEBAJO de [gcp_service_account]. "
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
    datos = base64.standard_b64encode(contenido).decode("utf-8")
    parte_archivo = {"inline_data": {"mime_type": tipo, "data": datos}}
    cabeceras = {"x-goog-api-key": st.secrets["gemini_api_key"],
                 "Content-Type": "application/json"}
    cuerpo = {
        "contents": [{"parts": [parte_archivo, {"text": instrucciones}]}],
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


def identificar_productos(contenido, tipo, nombres):
    instrucciones = (
        "Esta foto muestra productos o cajas de una bodega de distribuidora. "
        "Estos son los productos que existen en el inventario: "
        + json.dumps(nombres, ensure_ascii=False) + ". "
        "Identifica cuáles de ellos aparecen en la foto (por etiqueta, marca o empaque) "
        "y cuenta cuántas cajas o bultos de cada uno se ven. "
        "Usa EXACTAMENTE el nombre que aparece en la lista. "
        "Si un producto de la foto no está en la lista, ignóralo. No inventes nada. "
        "Responde SOLO con JSON válido, sin texto adicional, con este formato exacto: "
        '{"productos": [{"nombre": "", "cantidad": 0}]}'
    )
    return llamar_gemini(contenido, tipo, instrucciones)


def elegir_archivo(clave, permitir_pdf):
    """Pide una foto (o PDF). En el celular, 'Subir o tomar foto' abre la cámara trasera."""
    origen = st.radio(
        "Origen:", ["Subir o tomar foto", "Cámara directa"],
        horizontal=True, key=f"origen_{clave}",
    )
    if origen == "Cámara directa":
        return st.camera_input("Toma la foto", key=f"cam_{clave}")
    tipos = ["jpg", "jpeg", "png", "webp"] + (["pdf"] if permitir_pdf else [])
    return st.file_uploader(
        "Foto o PDF" if permitir_pdf else "Foto", type=tipos, key=f"up_{clave}"
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
st.title("📦 Las 3B · Bodega")
st.caption(f"Versión {VERSION} · Hola, {st.session_state.get('usuario', '')} 👋 · ¡Sea serio! 😐")

if "aviso" in st.session_state:
    tipo, texto = st.session_state.pop("aviso")
    getattr(st, tipo)(texto)

lotes = st.session_state.lotes
vencidos = sum(1 for l in lotes if dias_restantes(l) < 0)
por_vencer = sum(1 for l in lotes if 0 <= dias_restantes(l) <= DIAS_ALERTA)
st.markdown(
    f"**{sum(l['cajas'] for l in lotes)} cajas** · {len(nombres_productos())} productos · "
    f"🟡 {por_vencer} por vencer · 🔴 {vencidos} vencidos"
)

(tab_inventario, tab_factura, tab_retirar,
 tab_venc, tab_ingresar, tab_editar) = st.tabs([
    "📦 Inventario",
    "🧾 Factura",
    "📤 Retirar",
    "⏰ Vencimientos",
    "➕ Ingresar",
    "✏️ Editar",
])


# -----------------------------------------------------
#  1. INVENTARIO (simple, con botón para ver más)
# -----------------------------------------------------
with tab_inventario:
    if not lotes:
        st.info("La bodega está vacía. Ingresa mercadería con la pestaña 🧾 Factura o ➕ Ingresar.")
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
                .agg(Cajas=("Cajas", "sum"), Vence=("Vencimiento", "min"))
                .sort_values("Producto")
            )
            st.dataframe(simple, hide_index=True)

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
                            "Próx. vence": ("Vencimiento", "min")})
                    .sort_values(["Categoría", "Producto"])
                )
                st.dataframe(detalle, hide_index=True)
                with st.expander("Ver lote por lote"):
                    st.dataframe(
                        df_d.sort_values(["Producto", "Vencimiento"])[
                            ["Producto", "Cajas", "Vencimiento", "Días restantes", "Proveedor"]],
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
            "Al ser gratis la IA puede saturarse y tardar en procesar. "
            "Si quieres algo bueno pagame la Clave de Gemini Pro Oliver"
        )

        archivo = elegir_archivo("factura", True)

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

            c_ok, c_cancel = st.columns(2)
            confirmar_f = c_ok.button("✅ Ingresar", type="primary")
            cancelar_f = c_cancel.button("Descartar")

            if cancelar_f:
                del st.session_state.factura
                st.rerun()

            if confirmar_f:
                errores, nuevos = [], []
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
                        errores.append(f"Fila {i + 1} ({nombre_f}): falta la fecha de vencimiento.")
                        continue
                    nuevos.append({
                        "nombre": nombre_f,
                        "categoria": fila["Categoría"] if fila["Categoría"] in CATEGORIAS else "Otros",
                        "cajas": int(fila["Cajas"]),
                        "vencimiento": pd.to_datetime(fila["Vencimiento"]).date().isoformat(),
                    })

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
                tabla_lotes(lotes_prod).sort_values("Vencimiento")[
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

        # ---------- Prueba beta: reconocer productos con la cámara ----------
        st.divider()
        with st.expander("🧪 Escanear productos a retirar (prueba beta)"):
            st.caption(
                "Fotografía los productos que vas a sacar. La IA intenta reconocerlos y "
                "contarlos; tú corriges antes de confirmar. Es una prueba: no te fíes a ciegas."
            )
            if not ia_disponible():
                aviso_ia_no_disponible()
            else:
                archivo_r = elegir_archivo("retiro", False)

                if archivo_r is not None and st.button("🔍 Reconocer productos", key="btn_reconocer"):
                    with st.spinner("Mirando la foto..."):
                        try:
                            res = identificar_productos(
                                archivo_r.getvalue(), archivo_r.type or "image/jpeg", productos
                            )
                            st.session_state.retiro_scan = res.get("productos", [])
                            st.session_state.retiro_n = st.session_state.get("retiro_n", 0) + 1
                        except Exception as e:
                            st.error(f"No pude reconocer los productos: {e}")

                if "retiro_scan" in st.session_state:
                    filas_r = []
                    for p in st.session_state.retiro_scan:
                        nombre_p = p.get("nombre")
                        if nombre_p not in productos:
                            continue
                        try:
                            cant_p = max(int(float(p.get("cantidad") or 1)), 1)
                        except (TypeError, ValueError):
                            cant_p = 1
                        filas_r.append({"Producto": nombre_p, "Cajas": cant_p})
                    df_r = pd.DataFrame(filas_r, columns=["Producto", "Cajas"])

                    if not filas_r:
                        st.warning("No reconocí productos del inventario. Puedes agregar filas a mano.")
                    st.write("**Revisa y corrige** (puedes agregar o borrar filas):")
                    editado_r = st.data_editor(
                        df_r,
                        num_rows="dynamic",
                        hide_index=True,
                        key=f"editor_retiro_{st.session_state.get('retiro_n', 0)}",
                        column_config={
                            "Producto": st.column_config.SelectboxColumn("Producto", options=productos),
                            "Cajas": st.column_config.NumberColumn("Cajas", min_value=1, step=1),
                        },
                    )

                    r_ok, r_cancel = st.columns(2)
                    confirmar_r = r_ok.button("✅ Retirar", type="primary", key="btn_retirar_scan")
                    cancelar_r = r_cancel.button("Descartar", key="btn_descartar_scan")

                    if cancelar_r:
                        del st.session_state.retiro_scan
                        st.rerun()

                    if confirmar_r:
                        pedidos, errores_r = {}, []
                        for i, fila in editado_r.iterrows():
                            nombre_p = fila["Producto"]
                            if pd.isna(nombre_p) or not str(nombre_p).strip():
                                errores_r.append(f"Fila {i + 1}: elige el producto.")
                                continue
                            if pd.isna(fila["Cajas"]) or int(fila["Cajas"]) < 1:
                                errores_r.append(f"Fila {i + 1} ({nombre_p}): faltan las cajas.")
                                continue
                            pedidos[nombre_p] = pedidos.get(nombre_p, 0) + int(fila["Cajas"])
                        for nombre_p, cant_p in pedidos.items():
                            disponible = sum(
                                l["cajas"] for l in st.session_state.lotes if l["nombre"] == nombre_p
                            )
                            if cant_p > disponible:
                                errores_r.append(
                                    f"{nombre_p}: pides {cant_p} y solo quedan {disponible}. "
                                    "Dígale que no queda 🤷"
                                )

                        if errores_r:
                            st.error("\n".join(f"- {e}" for e in errores_r))
                        elif not pedidos:
                            st.warning("No hay nada para retirar.")
                        else:
                            for nombre_p, cant_p in pedidos.items():
                                descontar_fifo(nombre_p, cant_p)
                            if guardar():
                                del st.session_state.retiro_scan
                                resumen_r = ", ".join(f"{c} de {n}" for n, c in pedidos.items())
                                avisar("success", f"Retirado: {resumen_r}.")
                                st.rerun()


# -----------------------------------------------------
#  4. VENCIMIENTOS (por defecto solo lo que importa)
# -----------------------------------------------------
with tab_venc:
    if not lotes:
        st.info("Aún no hay lotes registrados.")
    else:
        ver_todos = st.toggle("➕ Ver todos los lotes", key="venc_todos")
        df_v = tabla_lotes(lotes).sort_values("Vencimiento")
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


# -----------------------------------------------------
#  5. INGRESAR MERCADERÍA (manual)
# -----------------------------------------------------
with tab_ingresar:
    st.subheader("Ingreso manual")
    st.caption("Para cuando no hay factura a mano. Ejemplo: llega McKay.")

    with st.form("form_ingreso", clear_on_submit=True):
        nombre = st.text_input("Nombre del producto:")
        categoria = st.selectbox("Categoría:", CATEGORIAS)
        cajas = st.number_input("Cajas que llegaron:", min_value=1, step=1)
        vencimiento = st.date_input(
            "Fecha de vencimiento:",
            value=date.today() + timedelta(days=180),
            format="DD/MM/YYYY",
        )
        proveedor = st.text_input("Proveedor (opcional):", placeholder="Ej: McKay")

        enviar = st.form_submit_button("Agregar a bodega")

        if enviar:
            if not nombre.strip():
                st.error("Sea serio: falta el nombre del producto 😐")
            else:
                nombre_limpio = nombre.strip()
                venc_txt = vencimiento.isoformat()

                # Si ya existe el mismo producto con la misma fecha, se suman las cajas
                existente = next(
                    (l for l in st.session_state.lotes
                     if l["nombre"].lower() == nombre_limpio.lower()
                     and l["vencimiento"] == venc_txt),
                    None,
                )
                if existente:
                    existente["cajas"] += int(cajas)
                else:
                    st.session_state.lotes.append({
                        "id": uuid.uuid4().hex[:8],
                        "nombre": nombre_limpio,
                        "categoria": categoria,
                        "cajas": int(cajas),
                        "vencimiento": venc_txt,
                        "proveedor": proveedor.strip(),
                        "ingreso": date.today().isoformat(),
                    })
                if guardar():
                    st.success(f"¡Listo! {int(cajas)} cajas de '{nombre_limpio}' ingresadas. Oliver mi peyito 🐶")


# -----------------------------------------------------
#  6. EDITAR O BORRAR LOTE
# -----------------------------------------------------
with tab_editar:
    st.subheader("Corregir un lote")

    if not lotes:
        st.warning("No hay lotes para editar.")
    else:
        ordenados = sorted(lotes, key=lambda l: (l["nombre"].lower(), l["vencimiento"]))
        etiquetas = {
            l["id"]: f"{l['nombre']} · vence {a_fecha(l['vencimiento']).strftime('%d/%m/%Y')} · {l['cajas']} cajas"
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
                "Vencimiento:", value=a_fecha(lote["vencimiento"]), format="DD/MM/YYYY",
            )
            guardar_cambios = st.form_submit_button("Guardar cambios")

            if guardar_cambios:
                if not nuevo_nombre.strip():
                    st.error("Sea serio: el nombre no puede estar vacío 😐")
                else:
                    lote["nombre"] = nuevo_nombre.strip()
                    lote["categoria"] = nueva_cat
                    lote["cajas"] = int(nuevas_cajas)
                    lote["vencimiento"] = nuevo_venc.isoformat()
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
#  PIE DE PÁGINA
# -----------------------------------------------------
st.divider()
if st.button("🔒 Cerrar sesión", key="salir_pie"):
    st.session_state.clear()
    st.rerun()
st.caption(f"Las 3B · Versión {VERSION} · Hecho con cariño · 67 🫳🫴")
