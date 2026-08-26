import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import urllib.parse
import time

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="SIGP - Hospital Isola", page_icon="🏥", layout="wide")

@st.cache_resource
def obtener_cliente_gspread():
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    
    if "gcp_service_account" in st.secrets:
        creds_dict = dict(st.secrets["gcp_service_account"])
        
        # Normalizador ultra robusto para la clave privada (elimina cualquier conflicto de barras o saltos)
        if "private_key" in creds_dict:
            pk = str(creds_dict["private_key"])
            pk = pk.replace("\\\\n", "\n").replace("\\n", "\n")
            creds_dict["private_key"] = pk.strip()
            
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    else:
        creds = Credentials.from_service_account_file("credenciales.json", scopes=scopes)
        
    return gspread.authorize(creds)

@st.cache_data(ttl=60)
def cargar_datos_excel():
    cliente = obtener_cliente_gspread()
    doc = cliente.open("SIGP - Hospital Isola")
    
    # 1. Servicios
    datos_serv = doc.worksheet("Servicios").get_all_records()
    base_servicios = {str(f.get("Nombre_Servicio", "")).strip(): str(f.get("PIN", "")).strip() for f in datos_serv if str(f.get("Nombre_Servicio", "")).strip()}
    telefonos = {str(f.get("Nombre_Servicio", "")).strip(): str(f.get("Telefono jefe", "")).strip() for f in datos_serv if str(f.get("Nombre_Servicio", "")).strip()}
            
    # 2. Agentes
    datos_agentes = doc.worksheet("Agentes").get_all_records()
    agentes_por_servicio = {}
    dni_por_agente = {}
    for f in datos_agentes:
        srv = str(f.get("ID_Servicio", "")).strip()
        dni = str(f.get("DNI", "")).strip()
        apellido = str(f.get("Apellido", "")).strip()
        nombre = str(f.get("Nombre", "")).strip()
        agente_completo = f"{apellido}, {nombre}".strip()
        
        if srv and agente_completo and agente_completo != ",":
            if srv not in agentes_por_servicio:
                agentes_por_servicio[srv] = []
            agentes_por_servicio[srv].append(agente_completo)
            dni_por_agente[agente_completo] = dni

    # 3. Saldos
    saldos_agentes = {}
    for f in doc.worksheet("Licencias_Saldos").get_all_records():
        dni = str(f.get("DNI", "")).strip()
        if dni:
            saldos_agentes[dni] = {
                "dias_disp": int(f.get("Dias_Disponibl", 0) or 0),
                "art_disp": int(f.get("Articulos_Disponibles", 0) or 0)
            }
            
    return base_servicios, telefonos, agentes_por_servicio, dni_por_agente, saldos_agentes, doc.worksheet("Cierre_Diario")

if 'firmas_hoy' not in st.session_state:
    st.session_state.firmas_hoy = []

# Carga de datos
try:
    BASE_SERVICIOS, TELEFONOS, AGENTES_POR_SERVICIO, DNI_POR_AGENTE, SALDOS_AGENTES, hoja_cierre = cargar_datos_excel()
    fecha_hoy = datetime.now().strftime("%d/%m/%Y")
    
    datos_cierre = hoja_cierre.get_all_records()
    firmas_db = [str(f.get("ID_Servicio", "")).strip() for f in datos_cierre if str(f.get("Fecha", "")).strip() in [f"'{fecha_hoy}", fecha_hoy]]
    st.session_state.firmas_hoy = list(set(firmas_db))
except Exception as e:
    st.error(f"Error cargando datos: {e}")
    BASE_SERVICIOS, TELEFONOS, AGENTES_POR_SERVICIO, DNI_POR_AGENTE, SALDOS_AGENTES, hoja_cierre = {}, {}, {}, {}, {}, None

# --- UI ---
st.title("🏥 Sistema de Asistencia Diaria - Hospital Isola")
st.markdown("Plataforma de gestión para Jefaturas de Servicio y control de Personal.")
st.divider()

with st.container():
    st.subheader("📝 Cierre Diario de Novedades")
    col_srv, col_pin = st.columns(2)
    with col_srv:
        servicio = st.selectbox("1. Seleccione su Servicio", [""] + list(BASE_SERVICIOS.keys()))
    with col_pin:
        pin_ingresado = st.text_input("2. Ingrese su PIN de Jefatura", type="password")
    
    estado_asistencia = st.radio("3. Estado del servicio hoy:", ["Asistencia Perfecta", "Hubo inasistencias"])
    
    detalle_ausentes = "Asistencia Perfecta"
    bloquear_envio = False

    if estado_asistencia == "Hubo inasistencias" and servicio:
        agentes_list = AGENTES_POR_SERVICIO.get(servicio, [])
        agentes_ausentes = st.multiselect("Seleccione las personas ausentes:", agentes_list)
        
        lista_motivos = [
            "Licencia por Enfermedad",
            "Artículo Personal / Particular",
            "Vacaciones",
            "Licencia por Estudios",
            "Donación de Sangre",
            "Franco Compensatorio",
            "Inasistencia Injustificada",
            "Otro (Especificar)"
        ]
        motivo_seleccionado = st.selectbox("Seleccione el motivo de la ausencia:", lista_motivos)
        
        for ag in agentes_ausentes:
            dni = DNI_POR_AGENTE.get(ag, "")
            saldo = SALDOS_AGENTES.get(dni, {"dias_disp": 30, "art_disp": 6})
            if motivo_seleccionado == "Artículo Personal / Particular" and saldo["art_disp"] <= 0:
                st.error(f"❌ El agente **{ag}** no cuenta con artículos disponibles en su saldo.")
                bloquear_envio = True
            elif motivo_seleccionado == "Vacaciones" and saldo["dias_disp"] <= 0:
                st.warning(f"⚠️ Atención: El agente **{ag}** no tiene días de vacaciones disponibles.")

        if motivo_seleccionado == "Otro (Especificar)":
            motivo_detalle = st.text_input("Escriba el motivo detallado:")
            motivo_final = f"Otro: {motivo_detalle}"
        else:
            motivo_final = motivo_seleccionado

        detalle_ausentes = f"{', '.join(agentes_ausentes)} | Motivo: {motivo_final}" if agentes_ausentes else "No se seleccionaron inasistencias"

    if st.button("📤 Enviar Parte Diario", use_container_width=True, type="primary"):
        if bloquear_envio:
            st.error("No se puede enviar el parte porque uno o más agentes no tienen saldo suficiente.")
        elif servicio and pin_ingresado == BASE_SERVICIOS.get(servicio):
            if servicio in st.session_state.firmas_hoy:
                st.warning("Este servicio ya envió su confirmación en el día de hoy.")
            else:
                hoja_cierre.append_row([f"'{fecha_hoy}", servicio, pin_ingresado, detalle_ausentes])
                st.success(f"✅ Firma digital aceptada y guardada exitosamente para {servicio}.")
                time.sleep(2)
                st.rerun()
        else:
            st.error("❌ PIN incorrecto o servicio no seleccionado.")

st.divider()
st.subheader("📊 Radar de Personal (Control de Presentismo)")
for srv in BASE_SERVICIOS.keys():
    if srv in st.session_state.firmas_hoy:
        st.success(f"✅ {srv}: ENTREGADO")
    else:
        c1, c2 = st.columns([4, 1])
        c1.error(f"❌ {srv}: FALTA FIRMAR")
        tel = TELEFONOS.get(srv, "")
        if tel:
            mensaje = f"Hola. Desde el área de Personal recordamos que todavía no se envió el parte diario de asistencia de {srv}. Por favor, completar a la brevedad."
            link = f"https://wa.me/549{tel}?text={urllib.parse.quote(mensaje)}"
            c2.link_button("💬 Reclamar", link, use_container_width=True)
