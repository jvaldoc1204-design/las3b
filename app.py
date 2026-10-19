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
import hmac
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

VERSION = "beta 1.8.1"
ARCHIVO_DATOS = "inventario_las3b.json"
ARCHIVO_FALTANTES = "faltantes_las3b.json"
CATEGORIAS = ["Aseo", "Higiene", "Abarrotes", "Lácteos", "Bebestibles", "Otros"]
DIAS_ALERTA = 30      # "Por vencer" si faltan 30 días o menos
DIAS_URGENTE = 7      # "Urgente" si faltan 7 días o menos

import html
import os as _os
_LOGO = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "logo.jpg")
_HAY_LOGO = _os.path.exists(_LOGO)

st.set_page_config(page_title="Las 3B - Bodega", page_icon=_LOGO if _HAY_LOGO else "📦", layout="centered")


# -----------------------------------------------------
#  DISEÑO (colores de Las 3B, pensado para celular)
# -----------------------------------------------------
ESTILOS = """
<style>
:root {
  --l3-rojo: #E23744; --l3-verde: #2E9E5B; --l3-amarillo: #E0A100;
  --l3-naranja: #E8731A; --l3-gris: #8A8AA0;
}
.block-container { padding-top: 1.2rem; padding-bottom: 3rem; max-width: 760px; }
footer { visibility: hidden; }

/* Botones grandes y redondeados (fáciles de tocar con una mano) */
.stButton > button, .stFormSubmitButton > button {
  min-height: 3rem; border-radius: 14px; font-weight: 600; width: 100%;
  transition: transform .06s ease;
}
.stButton > button:active, .stFormSubmitButton > button:active { transform: scale(.98); }
.stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"] {
  box-shadow: 0 2px 8px rgba(226,55,68,.30);
}

/* Pestañas: deslizables y más grandes */
.stTabs [data-baseweb="tab-list"] {
  gap: .25rem; overflow-x: auto; scrollbar-width: none; padding-bottom: 2px;
}
.stTabs [data-baseweb="tab-list"]::-webkit-scrollbar { display: none; }
.stTabs [data-baseweb="tab"] {
  height: 2.9rem; padding: 0 .9rem; border-radius: 12px 12px 0 0; white-space: nowrap;
}
.stTabs [aria-selected="true"] { font-weight: 700; }

/* Campos y contenedores */
.stTextInput input, .stTextArea textarea, .stNumberInput input, .stDateInput input {
  border-radius: 12px;
}
[data-testid="stForm"], [data-testid="stExpander"], div[data-testid="stVerticalBlockBorderWrapper"] {
  border-radius: 16px;
}
[data-testid="stAudioInput"] { border-radius: 16px; }

/* Resumen de arriba */
.l3-kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: .5rem; margin: .4rem 0 1rem; }
.l3-kpi {
  background: var(--secondary-background-color); border-radius: 16px; padding: .7rem .4rem;
  text-align: center; border-bottom: 4px solid var(--l3-gris);
}
.l3-kpi b { display: block; font-size: 1.5rem; line-height: 1.1; }
.l3-kpi span { font-size: .72rem; opacity: .75; }
.l3-kpi.verde { border-color: var(--l3-verde); }
.l3-kpi.amarillo { border-color: var(--l3-amarillo); }
.l3-kpi.rojo { border-color: var(--l3-rojo); }

/* Tarjetas de producto */
.l3-card {
  display: flex; justify-content: space-between; align-items: center; gap: .6rem;
  background: var(--secondary-background-color); border-radius: 14px;
  padding: .7rem .9rem; margin-bottom: .5rem; border-left: 6px solid var(--l3-gris);
}
.l3-card.ok { border-left-color: var(--l3-verde); }
.l3-card.alerta { border-left-color: var(--l3-amarillo); }
.l3-card.urgente { border-left-color: var(--l3-naranja); }
.l3-card.vencido { border-left-color: var(--l3-rojo); }
.l3-nombre { font-weight: 650; line-height: 1.2; word-break: break-word; }
.l3-sub { font-size: .78rem; opacity: .75; margin-top: .15rem; }
.l3-cajas { text-align: right; white-space: nowrap; font-size: 1.35rem; font-weight: 700; line-height: 1; }
.l3-cajas small { display: block; font-size: .65rem; font-weight: 500; opacity: .7; }
</style>
"""


def estilos():
    st.markdown(ESTILOS, unsafe_allow_html=True)


def clase_estado(dias):
    if dias is None or pd.isna(dias):
        return "sin"
    if dias < 0:
        return "vencido"
    if dias <= DIAS_URGENTE:
        return "urgente"
    if dias <= DIAS_ALERTA:
        return "alerta"
    return "ok"


def tarjetas_html(items):
    """items: lista de dicts {nombre, cajas, sub, clase}. Devuelve el HTML (con todo escapado)."""
    partes = []
    for it in items:
        partes.append(
            f'<div class="l3-card {html.escape(it.get("clase", ""))}">'
            f'<div><div class="l3-nombre">{html.escape(str(it["nombre"]))}</div>'
            f'<div class="l3-sub">{html.escape(str(it.get("sub", "")))}</div></div>'
            f'<div class="l3-cajas">{int(it["cajas"])}<small>cajas</small></div></div>'
        )
    return "".join(partes)


def boton_voz(texto):
    """Botón '🔊 Escuchar': el navegador lee el texto en voz alta (gratis, sin IA)."""
    texto = str(texto or "").strip()
    if not texto:
        return
    carga = json.dumps(texto).replace("</", "<\\/")
    st.components.v1.html(
        """<button id="b" style="width:100%;height:44px;border:0;border-radius:12px;background:#E23744;
        color:#fff;font-size:16px;font-weight:600;cursor:pointer">🔊 Escuchar respuesta</button>
        <script>
        const t = """ + carga + """;
        document.getElementById('b').onclick = () => {
          try {
            speechSynthesis.cancel();
            const u = new SpeechSynthesisUtterance(t);
            u.lang = 'es-CL';
            const v = speechSynthesis.getVoices().find(x => x.lang.toLowerCase().startsWith('es'));
            if (v) u.voice = v;
            speechSynthesis.speak(u);
          } catch (e) {}
        };
        </script>""",
        height=56,
    )


def kpis_html(cajas, productos, por_vencer, vencidos):
    def k(valor, texto, clase=""):
        return f'<div class="l3-kpi {clase}"><b>{valor}</b><span>{texto}</span></div>'
    return (
        '<div class="l3-kpis">'
        + k(cajas, "cajas", "verde") + k(productos, "productos")
        + k(por_vencer, "por vencer", "amarillo") + k(vencidos, "vencidos", "rojo")
        + "</div>"
    )


def texto_vence(dias, iso):
    if dias is None or pd.isna(dias):
        return "Sin vencimiento"
    f = fmt_fecha(iso)
    if dias < 0:
        return f"Vencido hace {-dias} d · {f}"
    if dias == 0:
        return f"Vence HOY · {f}"
    return f"Vence en {dias} d · {f}"


estilos()


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


COOKIE_SESION = "las3b_sesion"
DIAS_SESION = 30


def secreto_tokens():
    """Clave para firmar las sesiones guardadas. Sale de los Secrets; sin ella no se ofrece 'recordar'."""
    try:
        for nombre in ("token_secret", "supabase_key"):
            if nombre in st.secrets and str(st.secrets[nombre]).strip():
                return ("las3b|" + str(st.secrets[nombre]).strip()).encode()
        if "gcp_service_account" in st.secrets:
            pk = str(st.secrets["gcp_service_account"]["private_key"]).strip()
            if pk:
                return ("las3b|" + pk).encode()
    except Exception:
        pass
    return None


def _firma(secreto, datos):
    return hmac.new(secreto, datos, hashlib.sha256).hexdigest()[:40]


def crear_token(usuario, dias=DIAS_SESION, ahora=None):
    secreto = secreto_tokens()
    if not secreto:
        return None
    vence = int((ahora or time.time()) + dias * 86400)
    datos = base64.urlsafe_b64encode(f"{usuario}|{vence}".encode("utf-8"))
    return datos.decode() + "." + _firma(secreto, datos)


def validar_token(token, ahora=None):
    """Devuelve el nombre del usuario si el token es auténtico, no venció y esa persona sigue autorizada."""
    secreto = secreto_tokens()
    if not secreto or not isinstance(token, str) or token.count(".") != 1:
        return None
    datos, firma = token.split(".")
    try:
        if not hmac.compare_digest(firma, _firma(secreto, datos.encode())):
            return None
        usuario, vence = base64.urlsafe_b64decode(datos.encode()).decode("utf-8").rsplit("|", 1)
        if int(vence) < (ahora or time.time()):
            return None
    except Exception:
        return None
    if usuario.lower() not in nombres_con_acceso():
        return None
    return usuario


def claves_por_usuario():
    """Opcional: en los Secrets, [claves] con una clave distinta para cada persona."""
    try:
        d = st.secrets["claves"]
        return {str(k): str(v) for k, v in dict(d).items() if str(v).strip()}
    except Exception:
        return {}


def nombres_con_acceso():
    claves = claves_por_usuario()
    if claves:
        return [k.strip().lower() for k in claves]
    return usuarios_permitidos()


def usuario_por_clave(escrita):
    """Devuelve el nombre si la clave es válida (o None)."""
    escrita = str(escrita or "").strip().lower()
    if not escrita:
        return None
    claves = claves_por_usuario()
    if claves:
        for nombre, k in claves.items():
            if hmac.compare_digest(escrita.encode(), k.strip().lower().encode()):
                return nombre.strip().capitalize()
        return None
    return escrita.capitalize() if escrita in usuarios_permitidos() else None


def _js_cookie(codigo):
    st.components.v1.html("<script>" + codigo + "</script>", height=0)


def poner_cookie(token):
    """Guarda la sesión en el celular y muestra si el navegador la aceptó."""
    st.components.v1.html(
        '<div id="r" style="font:14px sans-serif;color:#666">Guardando este celular…</div><script>'
        "try {"
        "const https = (window.parent.location.protocol === 'https:');"
        f"document.cookie = '{COOKIE_SESION}=' + {json.dumps(token)} + '; max-age={DIAS_SESION * 86400}; path=/; SameSite=Lax' + (https ? '; Secure' : '');"
        f"const ok = document.cookie.indexOf('{COOKIE_SESION}=') >= 0;"
        "document.getElementById('r').textContent = ok ? '✅ Listo: este celular quedó recordado' "
        ": '⚠️ El navegador no dejó guardar la sesión en este celular';"
        "} catch (e) { document.getElementById('r').textContent = '⚠️ No se pudo guardar la sesión: ' + e; }"
        "</script>",
        height=32,
    )


def borrar_cookie():
    _js_cookie(f"document.cookie = '{COOKIE_SESION}=; max-age=0; path=/; SameSite=Lax';")


def usuario_desde_cookie():
    try:
        return validar_token(st.context.cookies.get(COOKIE_SESION))
    except Exception:
        return None


def usuario_desde_url():
    """Plan B si las cookies no funcionan: el enlace guardado en favoritos trae la sesión (?s=...)."""
    try:
        return validar_token(st.query_params.get("s"))
    except Exception:
        return None


def diagnostico_sesion():
    """Datos para saber por qué no se recuerda el celular (nunca muestra valores secretos)."""
    filas = []
    try:
        filas.append(f"Versión de Streamlit: {st.__version__}")
    except Exception:
        filas.append("Versión de Streamlit: no se pudo leer")
    filas.append("Clave para firmar sesiones: " + ("sí" if secreto_tokens() else "NO (por eso no ofrece recordar)"))
    try:
        cookies = st.context.cookies
        nombres = sorted(str(k) for k in cookies.keys())
        filas.append("Cookies que llegan a la app: " + (", ".join(nombres) if nombres else "ninguna"))
        tok = cookies.get(COOKIE_SESION)
        if not tok:
            filas.append("Cookie de sesión de Las 3B: no llegó")
        else:
            filas.append("Cookie de sesión de Las 3B: llegó; " + (
                f"válida para {validar_token(tok)}" if validar_token(tok) else "pero NO es válida (venció, cambió la clave o la persona ya no tiene acceso)"))
    except Exception as e:
        filas.append(f"Esta versión de Streamlit no deja leer cookies ({type(e).__name__})")
    try:
        t = st.query_params.get("s")
        filas.append("Enlace con sesión (?s=): " + ("no" if not t else (
            f"sí, válido para {validar_token(t)}" if validar_token(t) else "sí, pero NO es válido")))
    except Exception:
        pass
    return filas


def cerrar_sesion():
    """Cierra la sesión y también olvida este celular."""
    try:
        st.query_params.clear()
    except Exception:
        pass
    st.session_state.clear()
    st.session_state["sin_auto"] = True
    st.rerun()


def pantalla_acceso():
    """Si no hay sesión iniciada, muestra el acceso y detiene la app (no carga datos)."""
    if st.session_state.get("usuario"):
        return
    if st.session_state.get("sin_auto"):
        borrar_cookie()                  # al cerrar sesión se olvida este celular
    else:
        recordado = usuario_desde_cookie() or usuario_desde_url()
        if recordado:
            st.session_state.usuario = recordado.capitalize()
            return
    encabezado(f"Versión {VERSION}")
    with st.form("form_acceso"):
        clave = st.text_input("Ingrese contraseña", type="password")
        recordar = False
        if secreto_tokens():
            recordar = st.checkbox(f"Recordar este celular ({DIAS_SESION} días)", value=True)
        entrar = st.form_submit_button("Entrar")
    if entrar:
        quien = usuario_por_clave(clave)
        if quien:
            st.session_state.usuario = quien
            st.session_state.pop("sin_auto", None)
            if recordar:
                token = crear_token(quien)
                st.session_state.poner_cookie = token
                try:
                    st.query_params["s"] = token      # plan B: el enlace también trae la sesión
                except Exception:
                    pass
            st.rerun()
        else:
            time.sleep(1)  # frena un poco los intentos al azar
            st.error("Contraseña incorrecta. Sea serio 😐")
    with st.expander("🔧 ¿No me recuerda el celular? Ver diagnóstico"):
        for fila_d in diagnostico_sesion():
            st.write("• " + fila_d)
    st.stop()


pantalla_acceso()
if st.session_state.get("poner_cookie"):
    poner_cookie(st.session_state.pop("poner_cookie"))


# -----------------------------------------------------
#  FECHAS (se usan al cargar los datos)
# -----------------------------------------------------
SIN_FECHA = ""                 # un lote sin vencimiento guarda texto vacío
FECHA_LEJANA = "9999-12-31"    # solo para ordenar: los lotes sin fecha van al final
SENTINELA = date(9999, 12, 31)


def a_fecha(texto):
    """'YYYY-MM-DD' -> date. Devuelve None si el lote no tiene vencimiento."""
    texto = str(texto or "").strip()
    if not texto:
        return None
    for formato in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):   # por si alguien editó la planilla a mano
        try:
            return datetime.strptime(texto[:10], formato).date()
        except ValueError:
            continue
    return None


def fecha_iso(texto):
    """Fecha como 'YYYY-MM-DD' si se entiende; si no, el texto tal cual (no se pierde nada)."""
    f = a_fecha(texto)
    return f.isoformat() if f else str(texto or "").strip()


# -----------------------------------------------------
#  ALMACENAMIENTO
# -----------------------------------------------------
CAMPOS = ["id", "nombre", "categoria", "cajas", "vencimiento", "proveedor", "ingreso"]
CAMPOS_F = ["nombre", "categoria", "desde", "nota", "origen"]


def sheets_configurado():
    """True si están los Secrets de Google Sheets (aunque no sea la base principal)."""
    try:
        return gspread is not None and "gcp_service_account" in st.secrets
    except Exception:
        return False


def usar_supabase():
    """True si están los Secrets de Supabase: entonces esa es la base de datos principal."""
    try:
        return requests is not None and "supabase_url" in st.secrets and "supabase_key" in st.secrets
    except Exception:
        return False


def usar_sheets():
    """Google Sheets como base principal (solo si NO hay Supabase configurado)."""
    return sheets_configurado() and not usar_supabase()


def modo_nube():
    return usar_supabase() or usar_sheets()


# ---------- Supabase (API REST, sin librerías extra) ----------
TRAMO_SB = 500


def sb_pedir(metodo, tabla, params=None, cuerpo=None, extra=None):
    url = str(st.secrets["supabase_url"]).strip().rstrip("/") + "/rest/v1/" + tabla
    clave = str(st.secrets["supabase_key"]).strip()
    cabeceras = {"apikey": clave, "Authorization": f"Bearer {clave}", "Content-Type": "application/json"}
    cabeceras.update(extra or {})
    r = requests.request(metodo, url, headers=cabeceras, params=params, json=cuerpo, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"Supabase respondió {r.status_code}: {str(r.text)[:300]}")
    return r


def sb_leer(tabla, orden, columnas="*"):
    """Lee TODAS las filas (Supabase entrega máximo 1000 por consulta)."""
    filas, desde = [], 0
    while True:
        r = sb_pedir("GET", tabla, params={"select": columnas, "order": orden,
                                           "limit": 1000, "offset": desde})
        tramo = r.json()
        filas.extend(tramo)
        if len(tramo) < 1000:
            return filas
        desde += 1000


def _sb_valor_in(v):
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def sb_guardar_tabla(tabla, clave, filas):
    """Deja la tabla igual a 'filas': primero inserta/actualiza y después borra lo que sobra."""
    for i in range(0, len(filas), TRAMO_SB):
        sb_pedir("POST", tabla, params={"on_conflict": clave}, cuerpo=filas[i:i + TRAMO_SB],
                 extra={"Prefer": "resolution=merge-duplicates,return=minimal"})
    nuevas = {str(f[clave]) for f in filas}
    sobran = [k for k in (str(x[clave]) for x in sb_leer(tabla, clave, clave)) if k not in nuevas]
    for i in range(0, len(sobran), 100):
        lista = ",".join(_sb_valor_in(k) for k in sobran[i:i + 100])
        sb_pedir("DELETE", tabla, params={clave: f"in.({lista})"})


def _fecha_o_none(v):
    f = a_fecha(v)
    return f.isoformat() if f else None


def lote_desde_fila_sb(f):
    return {
        "id": str(f["id"]), "nombre": f["nombre"], "categoria": f.get("categoria") or "Otros",
        "cajas": int(f["cajas"]), "vencimiento": str(f.get("vencimiento") or ""),
        "proveedor": f.get("proveedor") or "", "ingreso": str(f.get("ingreso") or ""),
    }


def faltante_desde_fila_sb(f):
    return {
        "nombre": f["nombre"], "categoria": f.get("categoria") or "Otros",
        "desde": str(f.get("desde") or ""), "nota": f.get("nota") or "",
        "origen": f.get("origen") or "auto",
    }


def lotes_a_filas_sb(lotes):
    return [{
        "id": str(l["id"]), "nombre": l["nombre"], "categoria": l.get("categoria") or "Otros",
        "cajas": int(l["cajas"]), "vencimiento": _fecha_o_none(l.get("vencimiento")),
        "proveedor": l.get("proveedor") or "", "ingreso": _fecha_o_none(l.get("ingreso")),
    } for l in lotes]


def faltantes_a_filas_sb(lista):
    return [{
        "nombre": f["nombre"], "categoria": f.get("categoria") or "Otros",
        "desde": _fecha_o_none(f.get("desde")), "nota": f.get("nota") or "",
        "origen": f.get("origen") or "auto",
    } for f in lista]


@st.cache_resource
def obtener_hoja(pestana="lotes"):
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
        hoja = libro.worksheet(pestana)
    except gspread.WorksheetNotFound:
        hoja = libro.add_worksheet(title=pestana, rows=1000, cols=10)
    return hoja


def lotes_desde_sheets():
    valores = obtener_hoja().get_all_values()
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
        d["vencimiento"] = fecha_iso(d["vencimiento"])
        d["ingreso"] = fecha_iso(d["ingreso"])
        lotes.append(d)
    return lotes


def faltantes_desde_sheets():
    valores = obtener_hoja("faltantes").get_all_values()
    salida = []
    for fila in valores[1:]:
        fila = fila + [""] * (len(CAMPOS_F) - len(fila))
        d = dict(zip(CAMPOS_F, fila))
        if d["nombre"].strip():
            salida.append(d)
    return salida


def cargar_lotes():
    # --- Supabase ---
    if usar_supabase():
        try:
            filas = sb_leer("lotes", "nombre.asc,vencimiento.asc.nullslast")
        except Exception as e:
            st.error(f"No pude leer Supabase: {e}")
            return st.session_state.get("lotes", [])
        return [lote_desde_fila_sb(f) for f in filas]

    # --- Modo nube: Google Sheets ---
    if usar_sheets():
        try:
            return lotes_desde_sheets()
        except Exception as e:
            st.error(f"No pude leer Google Sheets: {e}")
            return st.session_state.get("lotes", [])

    # --- Modo local: archivo JSON ---
    if os.path.exists(ARCHIVO_DATOS):
        try:
            with open(ARCHIVO_DATOS, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
    return []


def guardar_lotes(lotes):
    if usar_supabase():
        try:
            sb_guardar_tabla("lotes", "id", lotes_a_filas_sb(lotes))
        except Exception as e:
            st.error(f"No pude guardar en Supabase: {e}")
            return False
        return True

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


def cargar_faltantes():
    """Lista de productos que no quedan en bodega."""
    if usar_supabase():
        try:
            filas = sb_leer("faltantes", "desde.asc.nullslast,nombre.asc")
        except Exception as e:
            st.warning(f"No pude leer la lista de faltantes: {e}")
            return st.session_state.get("faltantes", [])
        return [faltante_desde_fila_sb(f) for f in filas]
    if usar_sheets():
        try:
            return faltantes_desde_sheets()
        except Exception as e:
            st.warning(f"No pude leer la lista de faltantes: {e}")
            return st.session_state.get("faltantes", [])
    if os.path.exists(ARCHIVO_FALTANTES):
        try:
            with open(ARCHIVO_FALTANTES, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
    return []


def guardar_faltantes(lista):
    if usar_supabase():
        try:
            sb_guardar_tabla("faltantes", "nombre", faltantes_a_filas_sb(lista))
        except Exception as e:
            st.error(f"No pude guardar la lista de faltantes: {e}")
            return False
        return True
    if usar_sheets():
        filas = [CAMPOS_F] + [[f.get(c, "") for c in CAMPOS_F] for f in lista]
        try:
            hoja = obtener_hoja("faltantes")
            hoja.clear()
            hoja.update(values=filas, range_name="A1", value_input_option="RAW")
        except Exception as e:
            st.error(f"No pude guardar la lista de faltantes: {e}")
            return False
        return True
    with open(ARCHIVO_FALTANTES, "w", encoding="utf-8") as f:
        json.dump(lista, f, ensure_ascii=False, indent=4)
    return True


# En modo nube se recarga la planilla en cada interacción, así todos ven datos frescos.
if modo_nube() or "lotes" not in st.session_state:
    st.session_state.lotes = cargar_lotes()
if modo_nube() or "faltantes" not in st.session_state:
    st.session_state.faltantes = cargar_faltantes()
st.session_state.setdefault("faltantes_sucio", False)


RESPALDO_DIAS_TODOS = 3      # se guardan todos los respaldos de los últimos 3 días
RESPALDO_DIAS_MAX = 30       # después, uno por día hasta 30 días


def huella_estado(lotes, faltantes):
    datos = json.dumps(
        {"l": sorted(lotes_a_filas_sb(lotes), key=lambda x: x["id"]),
         "f": sorted(faltantes_a_filas_sb(faltantes), key=lambda x: x["nombre"])},
        sort_keys=True, ensure_ascii=False)
    return hashlib.md5(datos.encode("utf-8")).hexdigest()


def _fecha_utc(texto):
    t = datetime.fromisoformat(str(texto).replace("Z", "+00:00"))
    if t.tzinfo is None:
        from datetime import timezone
        t = t.replace(tzinfo=timezone.utc)
    return t


def hora_chile(texto):
    """'2026-10-18T20:42:10+00:00' -> '18/10 17:42' (hora de Chile)."""
    try:
        t = _fecha_utc(texto)
        if ZoneInfo is not None:
            t = t.astimezone(ZoneInfo("America/Santiago"))
        return t.strftime("%d/%m %H:%M")
    except Exception:
        return str(texto)[:16]


def podar_respaldos():
    """Borra respaldos viejos: quedan todos los de 3 días y uno por día hasta los 30 días."""
    hoy = hoy_chile().isoformat()
    if st.session_state.get("poda_dia") == hoy:
        return
    from datetime import timezone
    ahora = datetime.now(timezone.utc)
    dias_vistos, borrar = set(), []
    for f in sb_leer("respaldos", "creado.desc", "id,creado"):       # del más nuevo al más viejo
        t = _fecha_utc(f["creado"])
        edad = (ahora - t).days
        if edad < RESPALDO_DIAS_TODOS:
            continue
        dia = (t.astimezone(ZoneInfo("America/Santiago")) if ZoneInfo else t).date()
        if edad > RESPALDO_DIAS_MAX or dia in dias_vistos:
            borrar.append(f["id"])
        else:
            dias_vistos.add(dia)
    for i in range(0, len(borrar), 100):
        sb_pedir("DELETE", "respaldos", params={"id": "in.(" + ",".join(str(x) for x in borrar[i:i + 100]) + ")"})
    st.session_state.poda_dia = hoy


def respaldar(forzar=False):
    """Guarda una copia completa (lotes + faltantes) en Supabase. Nunca hace fallar un guardado."""
    if not usar_supabase():
        return False
    lotes, falt = st.session_state.lotes, st.session_state.faltantes
    huella = huella_estado(lotes, falt)
    if not forzar and st.session_state.get("respaldo_huella") == huella:
        return True
    try:
        sb_pedir("POST", "respaldos", cuerpo={
            "usuario": st.session_state.get("usuario", ""), "huella": huella,
            "n_lotes": len(lotes), "total_cajas": sum(int(l["cajas"]) for l in lotes),
            "lotes": lotes_a_filas_sb(lotes), "faltantes": faltantes_a_filas_sb(falt),
        }, extra={"Prefer": "return=minimal"})
        st.session_state.respaldo_huella = huella
        st.session_state.pop("respaldo_error", None)
        podar_respaldos()
        return True
    except Exception as e:
        st.session_state.respaldo_error = str(e)
        return False


def guardar():
    """Devuelve True solo si los datos realmente se guardaron."""
    if not guardar_lotes(st.session_state.lotes):
        return False
    limpiar_faltantes()          # los que ya tienen stock salen de la lista
    guardar_faltantes_si_hace_falta()
    respaldar()
    return True


def guardar_faltantes_si_hace_falta():
    if st.session_state.get("faltantes_sucio"):
        if guardar_faltantes(st.session_state.faltantes):
            st.session_state.faltantes_sucio = False
            respaldar()
            return True
        return False
    return True


def avisar(tipo, texto):
    """Guarda un mensaje para mostrarlo después del st.rerun()."""
    st.session_state.aviso = (tipo, texto)


# -----------------------------------------------------
#  FUNCIONES DE APOYO
# -----------------------------------------------------
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


def tiene_stock(nombre):
    n = normalizar(nombre)
    return any(normalizar(l["nombre"]) == n for l in st.session_state.lotes)


def n_cajas(n):
    return f"{n} caja" if n == 1 else f"{n} cajas"


def registrar_faltante(nombre, categoria="Otros", nota="", origen="auto"):
    """Agrega a Faltantes (si no estaba). Devuelve True si se agregó."""
    nombre = str(nombre or "").strip()
    if not nombre:
        return False
    n = normalizar(nombre)
    if any(normalizar(f["nombre"]) == n for f in st.session_state.faltantes):
        return False
    st.session_state.faltantes.append({
        "nombre": nombre,
        "categoria": categoria if categoria in CATEGORIAS else "Otros",
        "desde": hoy_chile().isoformat(),
        "nota": str(nota or "").strip(),
        "origen": origen,
    })
    st.session_state.faltantes_sucio = True
    return True


def quitar_faltante(nombre):
    n = normalizar(nombre)
    antes = len(st.session_state.faltantes)
    st.session_state.faltantes = [f for f in st.session_state.faltantes if normalizar(f["nombre"]) != n]
    if len(st.session_state.faltantes) != antes:
        st.session_state.faltantes_sucio = True


def limpiar_faltantes():
    """Saca de la lista los productos que volvieron a tener stock."""
    antes = len(st.session_state.faltantes)
    st.session_state.faltantes = [f for f in st.session_state.faltantes if not tiene_stock(f["nombre"])]
    if len(st.session_state.faltantes) != antes:
        st.session_state.faltantes_sucio = True


def registrar_agotados(candidatos, origen="auto"):
    """candidatos: {nombre: categoria}. Los que ya no tienen stock pasan a Faltantes.

    Devuelve la lista de nombres que se agregaron.
    """
    agregados = []
    for nombre, cat in candidatos.items():
        if not tiene_stock(nombre) and registrar_faltante(nombre, cat, "", origen):
            agregados.append(nombre)
    return agregados


def responder_consulta(lotes, accion, faltantes):
    """Contesta '¿cuántas cajas quedan de X?' con los datos reales (sin IA)."""
    nombre_in = str(accion.get("producto") or "").strip()
    if not nombre_in:
        return "¿De qué producto quieres saber?"
    existentes = sorted({l["nombre"] for l in lotes}, key=str.lower)
    nombre, existe = resolver_producto(nombre_in, existentes)
    if existe:
        mios = sorted([l for l in lotes if l["nombre"] == nombre], key=clave_venc)
        total = sum(int(l["cajas"]) for l in mios)

        def venc_txt(l):
            if not l["vencimiento"]:
                return "sin vencimiento"
            return f"que {'vence' if int(l['cajas']) == 1 else 'vencen'} el {fmt_fecha(l['vencimiento'])}"

        verbo = "Queda" if total == 1 else "Quedan"
        if len(mios) == 1:
            return f"{verbo} {n_cajas(total)} de {nombre}, {venc_txt(mios[0])}."
        return f"{verbo} {n_cajas(total)} de {nombre}: " + "; ".join(
            f"{n_cajas(int(l['cajas']))} {venc_txt(l)}" for l in mios) + "."
    nn = normalizar(nombre_in)
    en_f = next((f for f in faltantes
                 if normalizar(f["nombre"]) == nn or (len(nn) >= 3 and nn in normalizar(f["nombre"]))), None)
    if en_f:
        return f"De {en_f['nombre']} no queda ninguna caja. Está en Faltantes desde el {fmt_fecha(en_f['desde'])}."
    sug = sugerencias(nombre_in, existentes)
    extra = f" ¿Quisiste decir: {', '.join(sug)}?" if sug else ""
    return f"No encontré '{nombre_in}' en bodega.{extra}"


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

    if tipo == "agotar":
        if not existe:
            return True, f"Anotar {nombre} en Faltantes (no había cajas en bodega)", None
        total = sum(l["cajas"] for l in lotes if l["nombre"] == nombre)
        lotes[:] = [l for l in lotes if l["nombre"] != nombre]
        return True, f"Dejar {nombre} en 0 cajas (había {total}) y anotarlo en Faltantes", None

    if tipo == "faltante":
        if existe:
            total = sum(l["cajas"] for l in lotes if l["nombre"] == nombre)
            return False, f"{nombre}: todavía hay {n_cajas(total)} en bodega, no está faltando.", None
        return True, f"Anotar {nombre} en Faltantes", None

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
        '- "renombrar": cambiar el nombre de un producto. Campos: producto (nombre actual), nuevo_nombre.\n'
        '- "consultar": preguntan cuántas cajas quedan de un producto. Campos: producto. No cambia nada.\n'
        '- "agotar": se acabó un producto que está en bodega (no queda nada, quedan 0, no hay más). Campos: producto.\n'
        '- "faltante": anotar en la lista de Faltantes un producto que falta o hay que pedir. Campos: producto, categoria.\n\n'
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
        '6. Si preguntan por las cajas de UN producto usa "consultar" (una acción por producto). '
        "Si la orden es otra pregunta sobre el inventario (qué vence pronto, qué hay de una categoría, etc.), "
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
        cerrar_sesion()
    with st.expander("🔧 Diagnóstico de sesión"):
        for fila_d in diagnostico_sesion():
            st.write("• " + fila_d)
        st.caption("Si 'Cookies que llegan' no incluye las3b_sesion, el navegador no la guardó: "
                   "usa el enlace con ?s=... guardado en favoritos o en la pantalla de inicio.")
    if usar_supabase():
        st.success("☁️ Datos guardados en Supabase")
        if st.session_state.get("respaldo_error"):
            st.warning("⚠️ No se pudo hacer el respaldo automático. Mira la pestaña 🛟 Respaldos.")
    elif usar_sheets():
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
    kpis_html(sum(l["cajas"] for l in lotes), len(nombres_productos()), por_vencer, vencidos),
    unsafe_allow_html=True,
)

if usar_supabase() and not st.session_state.lotes and not st.session_state.faltantes and sheets_configurado():
    with st.container(border=True):
        st.warning("La base de datos nueva (Supabase) está vacía. ¿Traigo todo lo que hay en Google Sheets?")
        if st.button("📥 Importar desde Google Sheets", type="primary", key="importar_sheets"):
            try:
                lotes_imp = lotes_desde_sheets()
                try:
                    falt_imp = faltantes_desde_sheets()
                except Exception:
                    falt_imp = []      # si la pestaña 'faltantes' no existe, no pasa nada
            except Exception as e:
                st.error(f"No pude leer Google Sheets: {e}")
            else:
                if not lotes_imp and not falt_imp:
                    st.info("Google Sheets también está vacío: no hay nada que importar.")
                else:
                    st.session_state.lotes = lotes_imp
                    st.session_state.faltantes = falt_imp
                    st.session_state.faltantes_sucio = True
                    if guardar():
                        avisar("success", f"Importados {len(lotes_imp)} lotes y {len(falt_imp)} faltantes desde Google Sheets ✅")
                        st.rerun()

(tab_asistente, tab_inventario, tab_falt, tab_factura, tab_venc,
 tab_retirar, tab_ingresar, tab_editar, tab_dup, tab_resp) = st.tabs([
    "🤖 Asistente",
    "📦 Inventario",
    f"🚨 Faltantes ({len(st.session_state.faltantes)})" if st.session_state.faltantes else "🚨 Faltantes",
    "🧾 Factura",
    "⏰ Vencimientos",
    "📤 Retirar",
    "➕ Ingresar",
    "✏️ Editar",
    "🧹 Duplicados",
    "🛟 Respaldos",
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
            tarjetas = []
            for _, fila_s in simple.iterrows():
                f_ven = None if pd.isna(fila_s["Vence"]) else fila_s["Vence"]
                dias_s = None if f_ven is None else (f_ven - date.today()).days
                tarjetas.append({
                    "nombre": fila_s["Producto"], "cajas": int(fila_s["Cajas"]),
                    "sub": texto_vence(dias_s, None if f_ven is None else f_ven.isoformat()),
                    "clase": clase_estado(dias_s),
                })
            st.markdown(tarjetas_html(tarjetas), unsafe_allow_html=True)
            with st.expander("Ver como tabla"):
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
#  1b. FALTANTES
# -----------------------------------------------------
with tab_falt:
    st.subheader("Productos que faltan")
    st.caption(
        "Se anotan solos cuando un producto llega a 0 cajas y salen solos cuando vuelve a entrar "
        "mercadería. También puedes anotar uno a mano."
    )
    faltantes_v = st.session_state.faltantes
    if not faltantes_v:
        st.success("No falta nada 🎉")
    else:
        hoy_f = hoy_chile()
        tarjetas_f = []
        for f in sorted(faltantes_v, key=lambda x: x.get("desde") or ""):
            try:
                dias_f = (hoy_f - a_fecha(f["desde"])).days
                desde_txt = f"Falta desde el {fmt_fecha(f['desde'])}" + (
                    " (hoy)" if dias_f == 0 else f" (hace {dias_f} d)")
            except Exception:
                desde_txt = "Falta"
            origen_txt = "anotado a mano" if f.get("origen") == "manual" else "se acabó"
            nota_txt = f" · {f['nota']}" if f.get("nota") else ""
            tarjetas_f.append({
                "nombre": f["nombre"], "cajas": 0, "clase": "vencido",
                "sub": f"{desde_txt} · {origen_txt}{nota_txt}",
            })
        st.markdown(tarjetas_html(tarjetas_f), unsafe_allow_html=True)

        st.write("**Para avisarle a tu jefe** (toca el ícono de copiar):")
        st.code("Faltan en bodega:\n" + "\n".join(f"- {f['nombre']}" for f in faltantes_v), language=None)

        nombres_f = [f["nombre"] for f in faltantes_v]
        quitar = st.selectbox("Quitar de la lista:", nombres_f, key="falt_quitar")
        if st.button("✅ Ya llegó / quitar", key="falt_quitar_btn"):
            quitar_faltante(quitar)
            if guardar_faltantes_si_hace_falta():
                avisar("success", f"'{quitar}' salió de Faltantes.")
                st.rerun()

    st.divider()
    with st.form("form_faltante", clear_on_submit=True):
        nom_f = st.text_input("Anotar un producto que falta:")
        cat_f = st.selectbox("Categoría:", CATEGORIAS, key="falt_cat")
        nota_f = st.text_input("Nota (opcional):", placeholder="Ej: pedir 10 cajas")
        enviar_f = st.form_submit_button("🚨 Agregar a faltantes")
        if enviar_f:
            nom_f = nom_f.strip()
            if not nom_f:
                st.error("Escribe el nombre del producto 😐")
            elif tiene_stock(nom_f):
                st.warning(f"De '{nom_f}' todavía hay cajas en bodega, no está faltando.")
            elif registrar_faltante(nom_f, cat_f, nota_f, "manual"):
                if guardar_faltantes_si_hace_falta():
                    avisar("success", f"'{nom_f}' anotado en Faltantes 🚨")
                    st.rerun()
            else:
                st.info("Ese producto ya estaba en la lista.")


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
                cat_prod = next(l["categoria"] for l in st.session_state.lotes if l["nombre"] == producto)
                detalle = descontar_fifo(producto, retirar)
                agotado = registrar_agotados({producto: cat_prod})
                if guardar():
                    extra_f = " 🚨 Se acabó: quedó anotado en Faltantes." if agotado else ""
                    avisar("success", f"Retiradas {int(retirar)} cajas de '{producto}': " + "; ".join(detalle) + "." + extra_f)
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
            def _es_consulta(a):
                return isinstance(a, dict) and str(a.get("tipo") or "").strip().lower() == "consultar"

            consultas_a = [a for a in acciones_a if _es_consulta(a)]
            acciones_a = [a for a in acciones_a if not _es_consulta(a)]
            respuestas_c = [
                responder_consulta(st.session_state.lotes, a, st.session_state.faltantes)
                for a in consultas_a
            ]
            for texto_c in respuestas_c:
                st.success(texto_c)
            if respuesta_a:
                st.info(respuesta_a)
                if not acciones_a and not respuestas_c:
                    st.caption("Respuesta de la IA según el inventario. Si es importante, confírmala en 📦 Inventario.")
            boton_voz(" ".join(respuestas_c + ([respuesta_a] if respuesta_a else [])))

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
            elif not respuesta_a and not respuestas_c:
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
                # Antes de aplicar: qué productos podrían quedar en cero y cuáles anotar a mano
                cat_por_nombre = {l["nombre"]: l["categoria"] for l in st.session_state.lotes}
                candidatos_f, manuales_f = {}, []
                for item in validas_a:
                    acc = item["accion"]
                    tipo_c = str(acc.get("tipo") or "").strip().lower()
                    if tipo_c in ("retirar", "agotar", "faltante"):
                        nombre_c, existe_c = resolver_producto(
                            acc.get("producto"), sorted(cat_por_nombre, key=str.lower))
                        if existe_c and tipo_c in ("retirar", "agotar"):
                            candidatos_f[nombre_c] = cat_por_nombre[nombre_c]
                        elif not existe_c:
                            manuales_f.append((nombre_c, acc.get("categoria")))
                for item in validas_a:
                    aplicar_accion(st.session_state.lotes, item["accion"], hoy_a)
                agotados_a = registrar_agotados(candidatos_f)
                for nombre_m, cat_m in manuales_f:
                    if registrar_faltante(nombre_m, cat_m or "Otros", "", "manual"):
                        agotados_a.append(nombre_m)
                if guardar():
                    quien_usuario = st.session_state.get("usuario", "")
                    resumen_a = "; ".join(i["texto"] for i in validas_a)
                    extra_a = f" 🚨 En Faltantes: {', '.join(agotados_a)}." if agotados_a else ""
                    del st.session_state.asist
                    st.session_state.asist_n = n_asist + 1
                    avisar("success", f"Listo{', ' + quien_usuario if quien_usuario else ''} 👍 {resumen_a}.{extra_a}")
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
            st.markdown(tarjetas_html([
                {"nombre": r_v["Producto"], "cajas": int(r_v["Cajas"]),
                 "sub": texto_vence(int(r_v["Días restantes"]), r_v["Vencimiento"].isoformat()),
                 "clase": clase_estado(int(r_v["Días restantes"]))}
                for _, r_v in df_v.iterrows()
            ]), unsafe_allow_html=True)
            with st.expander("Ver como tabla"):
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
            nuevas_cajas = st.number_input(
                "Cajas (0 = se acabó):", min_value=0, value=int(lote["cajas"]), step=1)
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
                    agotado = []
                    if int(nuevas_cajas) == 0:   # lote en cero: sale del inventario
                        st.session_state.lotes = [l for l in st.session_state.lotes if l["id"] != id_sel]
                        agotado = registrar_agotados({lote["nombre"]: nueva_cat})
                    if guardar():
                        if int(nuevas_cajas) == 0:
                            avisar("success", "Lote en cero: se quitó del inventario"
                                   + (f" y {agotado[0]} quedó en Faltantes 🚨." if agotado else "."))
                        else:
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
#  8. RESPALDOS
# -----------------------------------------------------
with tab_resp:
    st.subheader("Respaldos")
    df_descarga = pd.DataFrame(st.session_state.lotes, columns=CAMPOS)
    st.download_button(
        "⬇️ Descargar inventario (CSV)",
        data=df_descarga.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"inventario_las3b_{hoy_chile().isoformat()}.csv", mime="text/csv",
        help="Una copia en tu celular o computador, por si acaso.",
    )
    if not usar_supabase():
        st.info("Los respaldos automáticos funcionan con Supabase. Mientras tanto puedes descargar el CSV.")
    else:
        st.caption(
            "Con cada cambio se guarda una copia automática. Se conservan todas las de los últimos "
            f"{RESPALDO_DIAS_TODOS} días y una por día hasta los {RESPALDO_DIAS_MAX} días."
        )
        if st.session_state.get("respaldo_error"):
            st.warning(
                "No se pudo guardar el último respaldo: " + st.session_state.respaldo_error
                + "\n\n¿Ya creaste la tabla? Pega `supabase_respaldos.sql` en el SQL Editor de Supabase."
            )
        if st.button("💾 Respaldar ahora", key="resp_ahora"):
            if respaldar(forzar=True):
                avisar("success", "Respaldo guardado ✅")
                st.rerun()
        try:
            respaldos = sb_leer("respaldos", "creado.desc", "id,creado,usuario,n_lotes,total_cajas")
        except Exception as e:
            respaldos = []
            st.error(f"No pude leer los respaldos: {e}")
        if respaldos:
            ver = respaldos[:60]
            etiquetas_r = {
                r["id"]: f"{hora_chile(r['creado'])} · {r.get('usuario') or '—'} · "
                         f"{r['n_lotes']} lotes · {r['total_cajas']} cajas"
                for r in ver
            }
            id_resp = st.selectbox("Respaldo:", list(etiquetas_r), format_func=lambda i: etiquetas_r[i], key="resp_sel")
            elegido = next(r for r in ver if r["id"] == id_resp)
            st.caption(
                f"Ahora hay {len(st.session_state.lotes)} lotes y "
                f"{sum(int(l['cajas']) for l in st.session_state.lotes)} cajas. "
                f"Ese respaldo tiene {elegido['n_lotes']} lotes y {elegido['total_cajas']} cajas."
            )
            conf_r = st.checkbox("Confirmo que quiero volver a ese respaldo", key="resp_conf")
            if st.button("♻️ Restaurar este respaldo", disabled=not conf_r, key="resp_restaurar"):
                try:
                    fila_r = sb_pedir("GET", "respaldos", params={"id": f"eq.{id_resp}", "select": "*"}).json()[0]
                except Exception as e:
                    st.error(f"No pude leer ese respaldo: {e}")
                else:
                    respaldar()      # primero queda copia de cómo estaba ahora
                    st.session_state.lotes = [lote_desde_fila_sb(f) for f in fila_r["lotes"]]
                    st.session_state.faltantes = [faltante_desde_fila_sb(f) for f in fila_r["faltantes"]]
                    st.session_state.faltantes_sucio = True
                    if guardar():
                        avisar("success", f"Restaurado el respaldo del {hora_chile(fila_r['creado'])} ✅")
                        st.rerun()
        elif not st.session_state.get("respaldo_error"):
            st.info("Aún no hay respaldos. Se crea el primero con el próximo cambio.")


# -----------------------------------------------------
#  PIE DE PÁGINA
# -----------------------------------------------------
st.divider()
if st.button("🔒 Cerrar sesión", key="salir_pie"):
    cerrar_sesion()
st.caption(f"Las 3B · Versión {VERSION} · Hecho con cariño · 67 🫳🫴")
