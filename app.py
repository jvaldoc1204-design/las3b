import streamlit as st
import pandas as pd
import json
import os
import random
import uuid
from datetime import date, datetime, timedelta

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
        return

    with open(ARCHIVO_DATOS, "w", encoding="utf-8") as f:
        json.dump(lotes, f, ensure_ascii=False, indent=4)


# En modo nube se recarga la planilla en cada interacción, así todos ven datos frescos.
if usar_sheets() or "lotes" not in st.session_state:
    st.session_state.lotes = cargar_lotes()


def guardar():
    guardar_lotes(st.session_state.lotes)


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
 tab_retirar, tab_editar) = st.tabs([
    "1. Ingresar mercadería",
    "2. Inventario",
    "3. Vencimientos",
    "4. Retirar / Reponer",
    "5. Editar o borrar lote",
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
                guardar()
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
                guardar()
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
                    guardar()
                    avisar("success", "Lote actualizado ✅")
                    st.rerun()

        st.divider()
        confirmar = st.checkbox("Confirmo que quiero borrar este lote de forma permanente")
        if st.button("🗑️ Borrar lote", disabled=not confirmar):
            st.session_state.lotes = [l for l in st.session_state.lotes if l["id"] != id_sel]
            guardar()
            avisar("success", "Lote eliminado.")
            st.rerun()


# -----------------------------------------------------
#  PIE DE PÁGINA
# -----------------------------------------------------
st.divider()
st.caption("Las 3B · Hecho con cariño · 67 🫳🫴")
