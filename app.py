import streamlit as st
import pandas as pd
import json
import os
import random
import uuid
import base64
import re
import time
from datetime import date, datetime, timedelta

try:
    import requests
except ImportError:  # sin esta librería la pestaña de facturas queda desactivada
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

ARCHIVO_DATOS = "inventario_las3b.json"
CATEGORIAS = ["Aseo", "Higiene", "Abarrotes", "Lácteos", "Bebestibles", "Otros"]
DIAS_ALERTA = 30      # "Por vencer" si faltan 30 días o menos
DIAS_URGENTE = 7      # "Urgente" si faltan 7 días o menos

CHISTES = [
    "Dígale que no queda 🤷",
    "Sea serio 😐",
    "Oliver mi peyito 🐶",
    "67 🫳🫴",
]

st.set_page_config(page_title="Las 3B - Bodega", page_icon="📦", layout="wide")


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


# -----------------------------------------------------
#  BARRA LATERAL (chistes internos)
# -----------------------------------------------------
with st.sidebar:
    st.header("📦 Las 3B")
    st.caption("Distribuidora · Control de bodega")
    if usar_sheets():
        st.success("☁️ Datos guardados en Google Sheets")
    else:
        st.warning("💾 Modo local: los datos se guardan en un archivo JSON")
    st.divider()
    if st.button("🎲 Chiste interno"):
        st.info(random.choice(CHISTES))
    st.divider()
    st.caption("Oliver mi peyito 🐶")


# -----------------------------------------------------
#  ENCABEZADO
# -----------------------------------------------------
st.title("📦 Inventario de Bodega · Las 3B")
st.write("Registra lo que llega, lo que sale y vigila los vencimientos. ¡Sea serio! 😐")

if "aviso" in st.session_state:
    tipo, texto = st.session_state.pop("aviso")
    getattr(st, tipo)(texto)

# Resumen rápido arriba
lotes = st.session_state.lotes
vencidos = sum(1 for l in lotes if dias_restantes(l) < 0)
por_vencer = sum(1 for l in lotes if 0 <= dias_restantes(l) <= DIAS_ALERTA)
m1, m2, m3, m4 = st.columns(4)
m1.metric("Productos distintos", len(nombres_productos()))
m2.metric("Cajas totales", sum(l["cajas"] for l in lotes))
m3.metric("🟡 Por vencer (≤30 días)", por_vencer)
m4.metric("🔴 Vencidos", vencidos)

(tab_ingresar, tab_inventario, tab_venc,
 tab_retirar, tab_editar, tab_factura) = st.tabs([
    "1. Ingresar mercadería",
    "2. Inventario",
    "3. Vencimientos",
    "4. Retirar / Reponer",
    "5. Editar o borrar lote",
    "6. Escanear factura 🤖",
])


# -----------------------------------------------------
#  1. INGRESAR MERCADERÍA
# -----------------------------------------------------
with tab_ingresar:
    st.subheader("Llegó mercadería")
    st.caption("Ejemplo: llega McKay → ingresas el producto, las cajas y la fecha de vencimiento.")

    with st.form("form_ingreso", clear_on_submit=True):
        nombre = st.text_input("Nombre del producto:")
        c1, c2 = st.columns(2)
        with c1:
            categoria = st.selectbox("Categoría:", CATEGORIAS)
            cajas = st.number_input("Cajas que llegaron:", min_value=1, step=1)
        with c2:
            proveedor = st.text_input("Proveedor (opcional):", placeholder="Ej: McKay")
            vencimiento = st.date_input(
                "Fecha de vencimiento:",
                value=date.today() + timedelta(days=180),
                format="DD/MM/YYYY",
            )

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
#  2. INVENTARIO
# -----------------------------------------------------
with tab_inventario:
    st.subheader("Inventario actual")

    if not lotes:
        st.info("La bodega está vacía. Ve a 'Ingresar mercadería' para empezar.")
    else:
        f1, f2 = st.columns(2)
        with f1:
            filtro_cat = st.selectbox("Categoría:", ["Todas"] + CATEGORIAS, key="inv_cat")
        with f2:
            busqueda = st.text_input("Buscar producto:", key="inv_busqueda")

        df = tabla_lotes(lotes)
        if filtro_cat != "Todas":
            df = df[df["Categoría"] == filtro_cat]
        if busqueda.strip():
            df = df[df["Producto"].str.contains(busqueda.strip(), case=False, na=False)]

        if df.empty:
            st.warning("No hay productos con ese filtro.")
        else:
            resumen = (
                df.groupby(["Producto", "Categoría"], as_index=False)
                .agg(**{"Cajas totales": ("Cajas", "sum"),
                        "Próximo vencimiento": ("Vencimiento", "min"),
                        "Lotes": ("Cajas", "count")})
                .sort_values(["Categoría", "Producto"])
            )
            st.dataframe(resumen, hide_index=True)
            st.caption(f"Total de cajas en esta vista: **{int(df['Cajas'].sum())}**")

            with st.expander("Ver detalle por lote"):
                st.dataframe(
                    df.sort_values(["Producto", "Vencimiento"]),
                    hide_index=True,
                )

        st.divider()
        st.write("**Cajas por categoría**")
        por_cat = (
            pd.DataFrame(tabla_lotes(lotes))
            .groupby("Categoría")["Cajas"].sum()
            .reindex(CATEGORIAS, fill_value=0)
        )
        st.bar_chart(por_cat)


# -----------------------------------------------------
#  3. VENCIMIENTOS
# -----------------------------------------------------
with tab_venc:
    st.subheader("Monitor de vencimientos")

    if not lotes:
        st.info("Aún no hay lotes registrados.")
    else:
        opciones = ["Todos", "🔴 Vencido", "🟠 Urgente", "🟡 Por vencer", "🟢 OK"]
        filtro_estado = st.radio("Mostrar:", opciones, horizontal=True)

        df_v = tabla_lotes(lotes).sort_values("Vencimiento")
        if filtro_estado != "Todos":
            df_v = df_v[df_v["Estado"] == filtro_estado]

        if df_v.empty:
            st.success("Nada en esta categoría. ¡Todo tranquilo!")
        else:
            st.dataframe(
                df_v[["Estado", "Producto", "Categoría", "Cajas",
                      "Vencimiento", "Días restantes", "Proveedor"]],
                hide_index=True,
            )

        st.caption(
            f"🟠 Urgente = {DIAS_URGENTE} días o menos · "
            f"🟡 Por vencer = {DIAS_ALERTA} días o menos · 🔴 Vencido = fecha pasada"
        )


# -----------------------------------------------------
#  4. RETIRAR / REPONER
# -----------------------------------------------------
with tab_retirar:
    st.subheader("Sacar cajas de bodega")
    st.caption("Se descuentan primero las cajas que vencen antes (FIFO).")

    productos = nombres_productos()
    if not productos:
        st.warning("No hay productos en bodega.")
    else:
        producto = st.selectbox("Producto:", productos, key="retirar_producto")
        lotes_prod = sorted(
            [l for l in st.session_state.lotes if l["nombre"] == producto],
            key=lambda l: l["vencimiento"],
        )
        total = sum(l["cajas"] for l in lotes_prod)
        st.write(f"En bodega hay **{total} cajas** de {producto}:")
        st.dataframe(
            tabla_lotes(lotes_prod)[["Cajas", "Vencimiento", "Días restantes", "Estado"]],
            hide_index=True,
        )

        retirar = st.number_input("Cajas a retirar:", min_value=1, step=1, key="retirar_cant")

        if st.button("📤 Retirar de bodega", type="primary"):
            if retirar > total:
                st.error(f"Dígale que no queda: solo hay {total} cajas 🤷")
            else:
                faltan = int(retirar)
                detalle = []
                for l in lotes_prod:
                    if faltan == 0:
                        break
                    sacar = min(l["cajas"], faltan)
                    l["cajas"] -= sacar
                    faltan -= sacar
                    detalle.append(f"{sacar} del lote que vence el {a_fecha(l['vencimiento']).strftime('%d/%m/%Y')}")
                # Eliminar lotes que quedaron en cero
                st.session_state.lotes = [l for l in st.session_state.lotes if l["cajas"] > 0]
                if guardar():
                    avisar("success", f"Retiradas {int(retirar)} cajas de '{producto}': " + "; ".join(detalle) + ".")
                    st.rerun()


# -----------------------------------------------------
#  5. EDITAR O BORRAR LOTE
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
            e1, e2 = st.columns(2)
            with e1:
                nuevo_nombre = st.text_input("Nombre:", value=lote["nombre"])
                nueva_cat = st.selectbox(
                    "Categoría:", CATEGORIAS,
                    index=CATEGORIAS.index(lote["categoria"]) if lote["categoria"] in CATEGORIAS else 0,
                )
            with e2:
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
#  6. ESCANEAR FACTURA CON IA
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


def leer_factura(contenido, tipo):
    """Envía la foto/PDF a Gemini y devuelve un diccionario con proveedor y productos."""
    datos = base64.standard_b64encode(contenido).decode("utf-8")
    parte_archivo = {"inline_data": {"mime_type": tipo, "data": datos}}

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
    return json.loads(encontrado.group(0))


with tab_factura:
    st.subheader("Escanear factura con IA")

    if not ia_disponible():
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
    else:
        st.caption(
            "Toma una foto de la factura (o sube un PDF). La IA arma una tabla; "
            "tú la revisas, completas los vencimientos y confirmas. "
            "Nada entra al inventario hasta que presiones el botón final."
        )
        st.info(
            "Ojo: con la clave gratuita de Gemini, Google puede usar lo que envías para "
            "mejorar sus productos y personal suyo puede revisarlo. No subas facturas "
            "con datos que no quieras compartir."
        )

        origen = st.radio("Origen:", ["Subir foto o PDF", "Usar cámara"], horizontal=True)
        if origen == "Usar cámara":
            archivo = st.camera_input("Toma una foto de la factura")
        else:
            archivo = st.file_uploader(
                "Foto o PDF de la factura", type=["jpg", "jpeg", "png", "webp", "pdf"]
            )

        if archivo is not None and st.button("🔍 Leer factura"):
            with st.spinner("Leyendo la factura..."):
                try:
                    tipo = archivo.type or "image/jpeg"
                    st.session_state.factura = leer_factura(archivo.getvalue(), tipo)
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
                key="editor_factura",
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
            confirmar_f = c_ok.button("✅ Ingresar al inventario", type="primary")
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
                        avisar("success", f"Factura ingresada: {len(nuevos)} productos agregados a bodega 📦")
                        st.rerun()


# -----------------------------------------------------
#  PIE DE PÁGINA
# -----------------------------------------------------
st.divider()
st.caption("Las 3B · Hecho con cariño · 67 🫳🫴")
