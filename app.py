# app.py
import pandas as pd
import streamlit as st
from datetime import timedelta
import plotly.graph_objects as go
from io import BytesIO
from collections import Counter

st.set_page_config(page_title="Planificador Lotes Naturiber", layout="wide")
st.title("🧠 Planificador de Lotes Salazón Naturiber")

# -------------------------------
# Panel de configuración (globales)
# -------------------------------
st.sidebar.header("Parámetros de planificación")

# Capacidad global ENTRADA
st.sidebar.subheader("Capacidad global · ENTRADA")
cap_ent_1 = st.sidebar.number_input("Entrada · 1º intento", value=3100, step=100, min_value=0)
cap_ent_2 = st.sidebar.number_input("Entrada · 2º intento", value=3500, step=100, min_value=0)

# Capacidad global SALIDA
st.sidebar.subheader("Capacidad global · SALIDA")
cap_sal_1 = st.sidebar.number_input("Salida · 1º intento", value=3100, step=100, min_value=0)
cap_sal_2 = st.sidebar.number_input("Salida · 2º intento", value=3500, step=100, min_value=0)

# Límite GLOBAL en días naturales entre DIA (recepción) y ENTRADA_SAL
dias_max_almacen_global = st.sidebar.number_input("Días máx. almacenamiento (GLOBAL)", value=5, step=1)

# Capacidad de estabilización (valor base)
estab_cap = st.sidebar.number_input(
    "Capacidad cámara de estabilización (unds)",
    value=4700, step=100, min_value=0
)

dias_festivos_default = [
    "2025-01-01", "2025-04-18", "2025-05-01", "2025-08-15",
    "2025-10-12", "2025-11-01", "2025-12-25"
]
dias_festivos_list = st.sidebar.multiselect(
    "Selecciona los días festivos",
    options=dias_festivos_default,
    default=dias_festivos_default
)
dias_festivos = pd.to_datetime(dias_festivos_list)

ajuste_finde = st.sidebar.checkbox("Ajustar fines de semana (SALIDA)", value=True)
ajuste_festivos = st.sidebar.checkbox("Ajustar festivos (SALIDA)", value=True)

# Botón opcional para limpiar estado
if st.sidebar.button("🔄 Reiniciar sesión"):
    st.session_state.clear()
    st.rerun()

# -------------------------------
# Subir archivo Excel
# -------------------------------
uploaded_file = st.file_uploader("📂 Sube tu Excel con los lotes", type=["xlsx"])

# -------------------------------
# Funciones auxiliares
# -------------------------------
def es_habil(fecha):
    # Hábil si es lunes-viernes y no es festivo (comparando por fecha normalizada)
    return fecha.weekday() < 5 and fecha.normalize() not in dias_festivos

def siguiente_habil(fecha):
    f = fecha + timedelta(days=1)
    while not es_habil(f):
        f += timedelta(days=1)
    return f

def anterior_habil(fecha):
    f = fecha - timedelta(days=1)
    while not es_habil(f):
        f -= timedelta(days=1)
    return f

def _sumar_en_rango(dic, fecha_ini, fecha_fin_inclusive, unds):
    """Suma 'unds' en dic[fecha] para todas las fechas entre ini y fin (ambas incluidas)."""
    if pd.isna(fecha_ini) or pd.isna(fecha_fin_inclusive):
        return
    for d in pd.date_range(fecha_ini, fecha_fin_inclusive, freq="D"):
        d0 = d.normalize()
        dic[d0] = dic.get(d0, 0) + unds

def calcular_estabilizacion_diaria(df_plan: pd.DataFrame, cap: int, estab_cap_overrides: dict | None = None) -> pd.DataFrame:
    """
    Calcula la ocupación diaria de la cámara de estabilización.
    Desglosa por tipo de producto:
      - Paleta: PRODUCTO empieza por 'P'
      - Jamón : PRODUCTO empieza por 'J'
    Un lote ocupa estabilización en los días naturales [DIA, ENTRADA_SAL - 1].
    Permite overrides de capacidad por fecha.
    """
    carga_total  = {}
    carga_paleta = {}
    carga_jamon  = {}

    for _, r in df_plan.iterrows():
        dia     = r.get("DIA")
        entrada = r.get("ENTRADA_SAL")
        unds    = int(r.get("UNDS", 0) or 0)
        prod    = str(r.get("PRODUCTO", ""))

        if pd.isna(dia) or pd.isna(entrada) or unds <= 0:
            continue

        fin = entrada - pd.Timedelta(days=1)
        if fin.date() < dia.date():
            continue  # entra el mismo día, no pisa estabilización

        for d in pd.date_range(dia.normalize(), fin.normalize(), freq="D"):
            d0 = d.normalize()
            carga_total[d0] = carga_total.get(d0, 0) + unds
            if prod.startswith("P"):
                carga_paleta[d0] = carga_paleta.get(d0, 0) + unds
            elif prod.startswith("J"):
                carga_jamon[d0] = carga_jamon.get(d0, 0) + unds

    if not carga_total:
        return pd.DataFrame(columns=[
            "FECHA", "ESTAB_UNDS", "ESTAB_PALETA", "ESTAB_JAMON",
            "CAPACIDAD", "UTIL_%", "EXCESO"
        ])

    df_estab = (
        pd.Series(carga_total, name="ESTAB_UNDS")
        .sort_index()
        .to_frame()
        .reset_index()
        .rename(columns={"index": "FECHA"})
    )
    df_estab["ESTAB_PALETA"] = df_estab["FECHA"].map(lambda d: int(carga_paleta.get(d.normalize(), 0)))
    df_estab["ESTAB_JAMON"]  = df_estab["FECHA"].map(lambda d: int(carga_jamon.get(d.normalize(), 0)))

    # Capacidad efectiva por fecha (override si existe)
    if estab_cap_overrides is None:
        estab_cap_overrides = {}

    def _cap_for_date(d):
        if pd.isna(d):
            return int(cap)
        key = pd.to_datetime(d).normalize()
        if key in estab_cap_overrides:
            return int(estab_cap_overrides[key])
        return int(cap)

    df_estab["CAPACIDAD"] = df_estab["FECHA"].apply(_cap_for_date)
    df_estab["UTIL_%"] = (df_estab["ESTAB_UNDS"] / df_estab["CAPACIDAD"] * 100).round(1)
    df_estab["EXCESO"] = (df_estab["ESTAB_UNDS"] - df_estab["CAPACIDAD"]).clip(lower=0).astype(int)

    df_estab = df_estab[
        ["FECHA", "ESTAB_UNDS", "ESTAB_PALETA", "ESTAB_JAMON",
         "CAPACIDAD", "UTIL_%", "EXCESO"]
    ]
    return df_estab

def generar_excel(df_out, filename="archivo.xlsx"):
    output = BytesIO()
    df_out.to_excel(output, index=False)
    output.seek(0)
    return output

# -------------------------------
# Planificador (GLOBAL, overrides por PRODUCTO y estabilización + overrides por FECHA entrada/salida/estab)
# -------------------------------
def planificar_filas_na(
    df_plan,
    dias_max_almacen_global,
    dias_max_por_producto,
    estab_cap,
    cap_overrides_ent,
    cap_overrides_sal,
    estab_cap_overrides
):
    df_corr = df_plan.copy()

    # Asegurar columnas auxiliares
    for col in ["LOTE_NO_ENCAJA"]:
        if col not in df_corr.columns:
            df_corr[col] = pd.NA

    # Cargas ya planificadas (se respetan)
    carga_entrada = df_corr.dropna(subset=["ENTRADA_SAL"]).groupby("ENTRADA_SAL")["UNDS"].sum().to_dict()
    carga_salida  = df_corr.dropna(subset=["SALIDA_SAL"]).groupby("SALIDA_SAL")["UNDS"].sum().to_dict()

    # Ocupación diaria ya existente en estabilización (por filas ya planificadas)
    estab_stock = {}
    for _, r in df_corr.dropna(subset=["ENTRADA_SAL"]).iterrows():
        dia_rec = r["DIA"]
        ent     = r["ENTRADA_SAL"]
        unds    = r["UNDS"]
        if pd.notna(dia_rec) and pd.notna(ent) and ent.date() > dia_rec.date():
            _sumar_en_rango(estab_stock, dia_rec, ent - pd.Timedelta(days=1), unds)

    # Helpers: capacidad por día/intent separadas para ENTRADA y SALIDA
    def get_cap_ent(date_dt, attempt):
        dkey = pd.to_datetime(date_dt).normalize()
        ov = cap_overrides_ent.get(dkey)
        if ov is not None:
            if attempt == 1 and pd.notna(ov.get("CAP1")):
                return int(ov["CAP1"])
            if attempt == 2 and pd.notna(ov.get("CAP2")):
                return int(ov["CAP2"])
        return cap_ent_1 if attempt == 1 else cap_ent_2

    def get_cap_sal(date_dt, attempt):
        dkey = pd.to_datetime(date_dt).normalize()
        ov = cap_overrides_sal.get(dkey)
        if ov is not None:
            if attempt == 1 and pd.notna(ov.get("CAP1")):
                return int(ov["CAP1"])
            if attempt == 2 and pd.notna(ov.get("CAP2")):
                return int(ov["CAP2"])
        return cap_sal_1 if attempt == 1 else cap_sal_2

    # Capacidad de estabilización por día (override si existe)
    def get_estab_cap(date_dt):
        dkey = pd.to_datetime(date_dt).normalize()
        ov = estab_cap_overrides.get(dkey)
        return ov if (ov is not None and pd.notna(ov)) else estab_cap

    # Chequeo de capacidad de estabilización en rango [ini, fin]
    def cabe_en_estab_rango(fecha_ini, fecha_fin_inclusive, unds):
        if pd.isna(fecha_ini) or pd.isna(fecha_fin_inclusive):
            return True
        if fecha_fin_inclusive < fecha_ini:
            return True
        for d in pd.date_range(fecha_ini, fecha_fin_inclusive, freq="D"):
            d0 = d.normalize()
            if estab_stock.get(d0, 0) + unds > get_estab_cap(d0):
                return False
        return True

    # Devuelve déficits de estabilización por día (dict fecha->faltan_unds) para un rango
    def deficits_estab(fecha_ini, fecha_fin_inclusive, unds):
        deficits = {}
        if pd.isna(fecha_ini) or pd.isna(fecha_fin_inclusive):
            return deficits
        if fecha_fin_inclusive < fecha_ini:
            return deficits
        for d in pd.date_range(fecha_ini, fecha_fin_inclusive, freq="D"):
            d0 = d.normalize()
            falta = (estab_stock.get(d0, 0) + unds) - get_estab_cap(d0)
            if falta > 0:
                deficits[d0] = int(falta)
        return deficits

    # ============================================================
    # REGLAS ESPECIALES DE ENTRADA COMÚN
    # - Grupo A (único): ["JBSPRCLC-MEX"]  -> todos ese código al mismo día
    # - Grupo B (conjunto): ["JCIVRPORCISAN", "PCIVRPORCISAN"] -> MISMO día para ambos
    #   Si no cabe conjuntamente, cae a intentar cada código por separado.
    # ============================================================
    def _aplicar_entrada_comun_para_grupo(codigos, marcar_si_falla=False):
        if "PRODUCTO" not in df_corr.columns:
            return False

        mask_group = df_corr["PRODUCTO"].astype(str).isin(codigos) & df_corr["ENTRADA_SAL"].isna()
        if not mask_group.any():
            return False
        pending = df_corr.loc[mask_group].copy()

        fechas_existentes = sorted(
            df_corr.loc[
                df_corr["PRODUCTO"].astype(str).isin(codigos) & df_corr["ENTRADA_SAL"].notna(),
                "ENTRADA_SAL"
            ].dt.normalize().unique().tolist()
        )
        fecha_preferente = fechas_existentes[0] if len(fechas_existentes) > 0 else None

        inicios, limites = [], []
        for _, r in pending.iterrows():
            dia_recepcion = r["DIA"]
            prod = r["PRODUCTO"]
            dias_max_almacen = dias_max_por_producto.get(prod, dias_max_almacen_global)
            entrada_ini_i = dia_recepcion if es_habil(dia_recepcion) else siguiente_habil(dia_recepcion)
            limite_i = dia_recepcion + pd.Timedelta(days=int(dias_max_almacen))
            inicios.append(entrada_ini_i.normalize())
            limites.append(limite_i.normalize())

        if not inicios:
            return False

        inicio_comun = max(inicios)
        limite_comun = min(limites)
        if inicio_comun > limite_comun:
            if marcar_si_falla:
                for idxp, _ in pending.iterrows():
                    df_corr.at[idxp, "LOTE_NO_ENCAJA"] = "Sí"
            return False

        def _es_factible_entrada_comun(d, attempt):
            if d is None:
                return False
            d = pd.to_datetime(d).normalize()

            total_unds = int(pending["UNDS"].sum())
            if carga_entrada.get(d, 0) + total_unds > get_cap_ent(d, attempt):
                return False

            sim_stock = dict(estab_stock)
            for _, r in pending.iterrows():
                dia_rec = r["DIA"]
                unds_i = int(r["UNDS"])
                if d.date() > dia_rec.date():
                    for k in pd.date_range(dia_rec.normalize(), (d - pd.Timedelta(days=1)).normalize(), freq="D"):
                        k0 = k.normalize()
                        if sim_stock.get(k0, 0) + unds_i > get_estab_cap(k0):
                            return False
                        sim_stock[k0] = sim_stock.get(k0, 0) + unds_i

            add_salida = {}
            for _, r in pending.iterrows():
                unds_i = int(r["UNDS"])
                dias_sal_optimos = int(r["DIAS_SAL_OPTIMOS"])
                salida = d + timedelta(days=dias_sal_optimos)
                if ajuste_finde:
                    if salida.weekday() == 5:
                        salida = anterior_habil(salida)
                    elif salida.weekday() == 6:
                        salida = siguiente_habil(salida)
                if ajuste_festivos and (salida.normalize() in dias_festivos):
                    dia_semana = salida.weekday()
                    if dia_semana == 0:
                        salida = siguiente_habil(salida)
                    elif dia_semana in [1, 2, 3]:
                        anterior = anterior_habil(salida)
                        siguiente = siguiente_habil(salida)
                        carga_ant = carga_salida.get(anterior, 0) + add_salida.get(anterior, 0)
                        carga_sig = carga_salida.get(siguiente, 0) + add_salida.get(siguiente, 0)
                        salida = anterior if carga_ant <= carga_sig else siguiente
                    elif dia_semana == 4:
                        salida = anterior_habil(salida)
                add_salida[salida] = add_salida.get(salida, 0) + unds_i

            for sfecha, suma_unds in add_salida.items():
                if carga_salida.get(sfecha, 0) + suma_unds > get_cap_sal(sfecha, attempt):
                    return False

            return True

        entrada_elegida = None
        for attempt in [1, 2]:
            candidatos = []
            if fecha_preferente is not None:
                if (fecha_preferente >= inicio_comun) and (fecha_preferente <= limite_comun):
                    candidatos.append(pd.to_datetime(fecha_preferente).normalize())

            d = inicio_comun
            if not es_habil(d):
                d = siguiente_habil(d)
            while d <= limite_comun:
                if d not in candidatos:
                    candidatos.append(d)
                d = siguiente_habil(d)

            for d in candidatos:
                if _es_factible_entrada_comun(d, attempt):
                    entrada_elegida = d
                    break
            if entrada_elegida is not None:
                break

        if entrada_elegida is not None:
            for idxp, r in pending.iterrows():
                dia_recepcion = r["DIA"]
                unds_i = int(r["UNDS"])
                dias_sal_optimos = int(r["DIAS_SAL_OPTIMOS"])

                df_corr.at[idxp, "ENTRADA_SAL"] = entrada_elegida
                salida = entrada_elegida + timedelta(days=dias_sal_optimos)
                if ajuste_finde:
                    if salida.weekday() == 5:
                        salida = anterior_habil(salida)
                    elif salida.weekday() == 6:
                        salida = siguiente_habil(salida)
                if ajuste_festivos and (salida.normalize() in dias_festivos):
                    dia_semana = salida.weekday()
                    if dia_semana == 0:
                        salida = siguiente_habil(salida)
                    elif dia_semana in [1, 2, 3]:
                        anterior = anterior_habil(salida)
                        siguiente = siguiente_habil(salida)
                        carga_ant = carga_salida.get(anterior, 0)
                        carga_sig = carga_salida.get(siguiente, 0)
                        salida = anterior if carga_ant <= carga_sig else siguiente
                    elif dia_semana == 4:
                        salida = anterior_habil(salida)

                df_corr.at[idxp, "SALIDA_SAL"] = salida
                df_corr.at[idxp, "DIAS_SAL"] = (salida - entrada_elegida).days
                df_corr.at[idxp, "DIAS_ALMACENADOS"] = (entrada_elegida - dia_recepcion).days
                df_corr.at[idxp, "LOTE_NO_ENCAJA"] = "No"

                carga_entrada[entrada_elegida] = carga_entrada.get(entrada_elegida, 0) + unds_i
                carga_salida[salida] = carga_salida.get(salida, 0) + unds_i
                if entrada_elegida.date() > dia_recepcion.date():
                    _sumar_en_rango(estab_stock, dia_recepcion, entrada_elegida - pd.Timedelta(days=1), unds_i)

            return True

        if marcar_si_falla:
            for idxp, _ in pending.iterrows():
                df_corr.at[idxp, "LOTE_NO_ENCAJA"] = "Sí"
        return False

    # Ejecutar reglas especiales
    _aplicar_entrada_comun_para_grupo(["JBSPRCLC-MEX"], marcar_si_falla=False)
    exito_conjunto = _aplicar_entrada_comun_para_grupo(["JCIVRPORCISAN", "PCIVRPORCISAN"], marcar_si_falla=False)
    if not exito_conjunto:
        _aplicar_entrada_comun_para_grupo(["JCIVRPORCISAN"], marcar_si_falla=False)
        _aplicar_entrada_comun_para_grupo(["PCIVRPORCISAN"], marcar_si_falla=False)

    # ===============================
    # Asignación de pendientes minimizando cambios de TIPO/NITRIF por día
    # ===============================
    entrada_profile = {}
    if "ENTRADA_SAL" in df_corr.columns:
        ya = df_corr.dropna(subset=["ENTRADA_SAL"]).copy()
        if not ya.empty:
            def _norm_tipo(v):
                s = str(v).strip().upper()
                if "IBER" in s:
                    return "IBÉRICO"
                if "BLAN" in s:
                    return "BLANCO"
                return "OTRO"
            def _norm_nitrif(v):
                try:
                    return int(v)
                except Exception:
                    return None
            col_tipo = "TIPO NITRIF" if "TIPO NITRIF" in ya.columns else None
            col_nitrif = "NITRIF" if "NITRIF" in ya.columns else None
            for _, r in ya.iterrows():
                d = pd.to_datetime(r["ENTRADA_SAL"]).normalize()
                tipo = _norm_tipo(r[col_tipo]) if col_tipo else "OTRO"
                nitr = _norm_nitrif(r[col_nitrif]) if col_nitrif else None
                if d not in entrada_profile:
                    entrada_profile[d] = {"tipo": Counter(), "nitrif": Counter()}
                entrada_profile[d]["tipo"][tipo] += 1
                if nitr is not None:
                    entrada_profile[d]["nitrif"][nitr] += 1

    def _norm_tipo(v):
        s = str(v).strip().upper()
        if "IBER" in s:
            return "IBÉRICO"
        if "BLAN" in s:
            return "BLANCO"
        return "OTRO"
    def _norm_nitrif(v):
        try:
            return int(v)
        except Exception:
            return None

    col_tipo = "TIPO NITRIF" if "TIPO NITRIF" in df_corr.columns else None
    col_nitrif = "NITRIF" if "NITRIF" in df_corr.columns else None

    # Sugerencias para lotes que no encajan
    sugerencias_rows = []

    pendientes = df_corr[df_corr["ENTRADA_SAL"].isna()].copy()
    if "DIA" in pendientes.columns:
        pendientes = pendientes.sort_values(["DIA", "PRODUCTO"], kind="stable")

    for idx, row in pendientes.iterrows():
        dia_recepcion    = row["DIA"]
        unds             = int(row["UNDS"])
        dias_sal_optimos = int(row["DIAS_SAL_OPTIMOS"])
        prod             = row.get("PRODUCTO", None)
        lote_id          = row.get("LOTE", idx)

        dias_max_almacen = dias_max_por_producto.get(prod, dias_max_almacen_global)
        tipo_lote = _norm_tipo(row[col_tipo]) if col_tipo else "OTRO"
        nitr_lote = _norm_nitrif(row[col_nitrif]) if col_nitrif else None

        entrada_ini = dia_recepcion if es_habil(dia_recepcion) else siguiente_habil(dia_recepcion)
        asignado = False

        for attempt in [1, 2]:
            candidatos = []
            entrada = entrada_ini
            while (entrada - dia_recepcion).days <= dias_max_almacen:
                cap_ent_dia = get_cap_ent(entrada, attempt)
                if carga_entrada.get(entrada, 0) + unds <= cap_ent_dia:
                    if cabe_en_estab_rango(dia_recepcion, entrada - pd.Timedelta(days=1), unds):
                        salida = entrada + timedelta(days=dias_sal_optimos)
                        if ajuste_finde:
                            if salida.weekday() == 5:
                                salida = anterior_habil(salida)
                            elif salida.weekday() == 6:
                                salida = siguiente_habil(salida)
                        if ajuste_festivos and (salida.normalize() in dias_festivos):
                            dia_semana = salida.weekday()
                            if dia_semana == 0:
                                salida = siguiente_habil(salida)
                            elif dia_semana in [1, 2, 3]:
                                anterior = anterior_habil(salida)
                                siguiente = siguiente_habil(salida)
                                carga_ant  = carga_salida.get(anterior, 0)
                                carga_sig  = carga_salida.get(siguiente, 0)
                                salida = anterior if carga_ant <= carga_sig else siguiente
                            elif dia_semana == 4:
                                salida = anterior_habil(salida)

                        cap_sal_dia = get_cap_sal(salida, attempt)
                        if carga_salida.get(salida, 0) + unds <= cap_sal_dia:
                            # Candidato válido; calcular score por TIPO/NITRIF + fecha
                            prof = entrada_profile.get(entrada, {"tipo": Counter(), "nitrif": Counter()})
                            tipo_counts   = prof["tipo"]
                            nitrif_counts = prof["nitrif"]

                            if sum(tipo_counts.values()) == 0:
                                cost_tipo = 0
                            else:
                                cost_tipo = 0 if tipo_counts.get(tipo_lote, 0) > 0 else 1

                            if sum(nitrif_counts.values()) == 0:
                                cost_nitr = 0
                            else:
                                cost_nitr = 0 if (nitr_lote is not None and nitrif_counts.get(nitr_lote, 0) > 0) else 1

                            score = (cost_tipo, cost_nitr, entrada)
                            candidatos.append((score, entrada, salida))

                entrada = siguiente_habil(entrada)

            if candidatos:
                candidatos.sort(key=lambda t: t[0])
                _, entrada_sel, salida_sel = candidatos[0]

                df_corr.at[idx, "ENTRADA_SAL"]      = entrada_sel
                df_corr.at[idx, "SALIDA_SAL"]       = salida_sel
                df_corr.at[idx, "DIAS_SAL"]         = (salida_sel - entrada_sel).days
                df_corr.at[idx, "DIAS_ALMACENADOS"] = (entrada_sel - dia_recepcion).days
                df_corr.at[idx, "LOTE_NO_ENCAJA"]   = "No"

                carga_entrada[entrada_sel] = carga_entrada.get(entrada_sel, 0) + unds
                carga_salida[salida_sel]   = carga_salida.get(salida_sel, 0) + unds

                if entrada_sel.date() > dia_recepcion.date():
                    _sumar_en_rango(estab_stock, dia_recepcion, entrada_sel - pd.Timedelta(days=1), unds)

                if entrada_sel not in entrada_profile:
                    entrada_profile[entrada_sel] = {"tipo": Counter(), "nitrif": Counter()}
                entrada_profile[entrada_sel]["tipo"][tipo_lote] += 1
                if nitr_lote is not None:
                    entrada_profile[entrada_sel]["nitrif"][nitr_lote] += 1

                asignado = True
                break

        # Si no se pudo asignar → generar sugerencias (tabla detallada por combinación)
        if not asignado:
            df_corr.at[idx, "LOTE_NO_ENCAJA"] = "Sí"

            sugerencias_rows_lote = []
            entrada = entrada_ini

            while (entrada - dia_recepcion).days <= dias_max_almacen:
                if not es_habil(entrada):
                    entrada = siguiente_habil(entrada)
                    continue

                for attempt in [1, 2]:
                    # Déficit en ENTRADA
                    cap_ent_dia = get_cap_ent(entrada, attempt)
                    deficit_ent = max(0, (carga_entrada.get(entrada, 0) + unds) - cap_ent_dia)

                    # Déficit en ESTABILIZACIÓN (máximo en el rango DIA..ENTRADA-1)
                    def_est = deficits_estab(dia_recepcion, entrada - pd.Timedelta(days=1), unds)
                    deficit_estab_max = max(def_est.values()) if def_est else 0

                    # Calcular SALIDA propuesta (con ajustes finde/festivo)
                    salida = entrada + timedelta(days=dias_sal_optimos)
                    if ajuste_finde:
                        if salida.weekday() == 5:
                            salida = anterior_habil(salida)
                        elif salida.weekday() == 6:
                            salida = siguiente_habil(salida)
                    if ajuste_festivos and (salida.normalize() in dias_festivos):
                        dia_semana = salida.weekday()
                        if dia_semana == 0:
                            salida = siguiente_habil(salida)
                        elif dia_semana in [1, 2, 3]:
                            anterior = anterior_habil(salida)
                            siguiente = siguiente_habil(salida)
                            carga_ant = carga_salida.get(anterior, 0)
                            carga_sig = carga_salida.get(siguiente, 0)
                            salida = anterior if carga_ant <= carga_sig else siguiente
                        elif dia_semana == 4:
                            salida = anterior_habil(salida)

                    # Déficit en SALIDA
                    cap_sal_dia = get_cap_sal(salida, attempt)
                    deficit_sal = max(0, (carga_salida.get(salida, 0) + unds) - cap_sal_dia)

                    # Agregar fila de sugerencia para esta combinación
                    sugerencias_rows_lote.append({
                        "LOTE": lote_id,
                        "PRODUCTO": prod,
                        "UNDS": unds,
                        "DIA_RECEPCION": pd.to_datetime(dia_recepcion).normalize(),
                        "ENTRADA_PROPUESTA": pd.to_datetime(entrada).normalize(),
                        "SALIDA_PROPUESTA": pd.to_datetime(salida).normalize(),
                        "INTENTO": attempt,
                        "DEFICIT_ENTRADA": int(deficit_ent),
                        "DEFICIT_ESTAB_MAX": int(deficit_estab_max),
                        "DEFICIT_SALIDA": int(deficit_sal),
                        "MAX_DEFICIT": int(max(deficit_ent, deficit_estab_max, deficit_sal)),
                        "TOTAL_DEFICIT": int(deficit_ent + deficit_estab_max + deficit_sal),
                    })

                entrada = siguiente_habil(entrada)

            # Priorizamos combinaciones factibles (MAX_DEFICIT=0), luego las de menor TOTAL_DEFICIT
            if sugerencias_rows_lote:
                # Orden: MAX_DEFICIT asc, TOTAL_DEFICIT asc, ENTRADA_PROPUESTA asc
                sugerencias_rows_lote.sort(
                    key=lambda r: (r["MAX_DEFICIT"], r["TOTAL_DEFICIT"], r["ENTRADA_PROPUESTA"])
                )
                # Limitar a un máximo por lote si quieres (por ejemplo 20):
                sugerencias_rows.extend(sugerencias_rows_lote[:20])

    # Métrica final
    if "DIAS_SAL" in df_corr.columns and "DIAS_SAL_OPTIMOS" in df_corr.columns:
        df_corr["DIFERENCIA_DIAS_SAL"] = df_corr["DIAS_SAL"] - df_corr["DIAS_SAL_OPTIMOS"]

    cols_sug = [
        "LOTE", "PRODUCTO", "UNDS", "DIA_RECEPCION",
        "ENTRADA_PROPUESTA", "SALIDA_PROPUESTA", "INTENTO",
        "DEFICIT_ENTRADA", "DEFICIT_ESTAB_MAX", "DEFICIT_SALIDA",
        "MAX_DEFICIT", "TOTAL_DEFICIT"
    ]
    df_sugerencias = pd.DataFrame(sugerencias_rows, columns=cols_sug) if sugerencias_rows else pd.DataFrame(columns=cols_sug)

    if not df_sugerencias.empty:
        df_sugerencias = df_sugerencias.sort_values(
            by=["MAX_DEFICIT", "TOTAL_DEFICIT", "ENTRADA_PROPUESTA", "SALIDA_PROPUESTA", "LOTE"],
            ascending=[True, True, True, True, True]
        ).reset_index(drop=True)

    return df_corr, df_sugerencias

# -------------------------------
# Ejecución de la app
# -------------------------------
if uploaded_file is not None:
    # Lee el Excel
    df = pd.read_excel(uploaded_file, engine="openpyxl")

    # Alias básicos por si vienen con espacios/guiones bajos
    alias_map = {
        "DIAS SAL OPTIMOS": "DIAS_SAL_OPTIMOS",
        "DIAS_SAL_OPTIMOS": "DIAS_SAL_OPTIMOS",
        "ENTRADA SAL": "ENTRADA_SAL",
        "SALIDA SAL": "SALIDA_SAL"
    }
    for a, target in alias_map.items():
        if a in df.columns and target not in df.columns:
            df.rename(columns={a: target}, inplace=True)

    # Normaliza tipos
    for col in ["DIA", "ENTRADA_SAL", "SALIDA_SAL"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    if "UNDS" in df.columns:
        df["UNDS"] = pd.to_numeric(df["UNDS"], errors="coerce").fillna(0).astype(int)

    # ---- Overrides por PRODUCTO (sidebar) ----
    dias_max_por_producto = {}
    if "PRODUCTO" in df.columns:
        productos = sorted(df["PRODUCTO"].dropna().astype(str).unique().tolist())
        st.sidebar.markdown("### ⏱️ Días máx. almacenamiento por PRODUCTO")

        if "overrides_df" not in st.session_state or set(st.session_state.get("productos_cache", [])) != set(productos):
            st.session_state.overrides_df = pd.DataFrame({
                "PRODUCTO": productos,
                "DIAS_MAX_ALMACEN": [dias_max_almacen_global] * len(productos)
            })
            st.session_state.productos_cache = productos

        overrides_df = st.sidebar.data_editor(
            st.session_state.overrides_df,
            use_container_width=True,
            num_rows="dynamic",
            disabled=["PRODUCTO"],
            column_config={
                "PRODUCTO": st.column_config.TextColumn("PRODUCTO"),
                "DIAS_MAX_ALMACEN": st.column_config.NumberColumn("Días máx. naturales", step=1, min_value=0)
            },
            key="overrides_editor"
        )
        if not overrides_df.empty:
            dias_max_por_producto = dict(zip(overrides_df["PRODUCTO"], overrides_df["DIAS_MAX_ALMACEN"]))
    else:
        st.sidebar.info("No se encontró columna PRODUCTO. Se aplicará solo el límite GLOBAL.")

    # ---- Overrides de capacidad por FECHA: ENTRADA ----
    st.sidebar.markdown("### 📅 Overrides capacidad ENTRADA (opcional)")

    if "cap_overrides_ent_df" not in st.session_state:
        st.session_state.cap_overrides_ent_df = pd.DataFrame({
            "FECHA": pd.to_datetime(pd.Series([], dtype="datetime64[ns]")),
            "CAP1":  pd.Series([], dtype="Int64"),
            "CAP2":  pd.Series([], dtype="Int64"),
        })
    st.session_state.cap_overrides_ent_df["FECHA"] = pd.to_datetime(
        st.session_state.cap_overrides_ent_df["FECHA"], errors="coerce"
    )
    for c in ("CAP1", "CAP2"):
        st.session_state.cap_overrides_ent_df[c] = pd.to_numeric(
            st.session_state.cap_overrides_ent_df[c], errors="coerce"
        ).astype("Int64")

    cap_overrides_ent_df = st.sidebar.data_editor(
        st.session_state.cap_overrides_ent_df,
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "FECHA": st.column_config.DateColumn("Fecha (entrada)", format="YYYY-MM-DD"),
            "CAP1": st.column_config.NumberColumn("Capacidad 1º intento", step=50, min_value=0),
            "CAP2": st.column_config.NumberColumn("Capacidad 2º intento", step=50, min_value=0),
        },
        key="cap_overrides_ent_editor"
    )

    # ---- Overrides de capacidad por FECHA: SALIDA ----
    st.sidebar.markdown("### 📅 Overrides capacidad SALIDA (opcional)")

    if "cap_overrides_sal_df" not in st.session_state:
        st.session_state.cap_overrides_sal_df = pd.DataFrame({
            "FECHA": pd.to_datetime(pd.Series([], dtype="datetime64[ns]")),
            "CAP1":  pd.Series([], dtype="Int64"),
            "CAP2":  pd.Series([], dtype="Int64"),
        })
    st.session_state.cap_overrides_sal_df["FECHA"] = pd.to_datetime(
        st.session_state.cap_overrides_sal_df["FECHA"], errors="coerce"
    )
    for c in ("CAP1", "CAP2"):
        st.session_state.cap_overrides_sal_df[c] = pd.to_numeric(
            st.session_state.cap_overrides_sal_df[c], errors="coerce"
        ).astype("Int64")

    cap_overrides_sal_df = st.sidebar.data_editor(
        st.session_state.cap_overrides_sal_df,
        num_rows="dynamic",
        use_container_width=True,
        column_config={
                "FECHA": st.column_config.DateColumn("Fecha (salida)", format="YYYY-MM-DD"),
                "CAP1": st.column_config.NumberColumn("Capacidad 1º intento", step=50, min_value=0),
                "CAP2": st.column_config.NumberColumn("Capacidad 2º intento", step=50, min_value=0),
        },
        key="cap_overrides_sal_editor"
    )

    # ---- Overrides de capacidad por FECHA: ESTABILIZACIÓN ----
    st.sidebar.markdown("### 📅 Overrides capacidad ESTABILIZACIÓN (opcional)")

    if "cap_overrides_estab_df" not in st.session_state:
        st.session_state.cap_overrides_estab_df = pd.DataFrame({
            "FECHA": pd.to_datetime(pd.Series([], dtype="datetime64[ns]")),
            "CAP":   pd.Series([], dtype="Int64"),
        })
    st.session_state.cap_overrides_estab_df["FECHA"] = pd.to_datetime(
        st.session_state.cap_overrides_estab_df["FECHA"], errors="coerce"
    )
    st.session_state.cap_overrides_estab_df["CAP"] = pd.to_numeric(
        st.session_state.cap_overrides_estab_df["CAP"], errors="coerce"
    ).astype("Int64")

    cap_overrides_estab_df = st.sidebar.data_editor(
        st.session_state.cap_overrides_estab_df,
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "FECHA": st.column_config.DateColumn("Fecha (estabilización)", format="YYYY-MM-DD"),
            "CAP":   st.column_config.NumberColumn("Capacidad estabilización (unds)", step=50, min_value=0),
        },
        key="cap_overrides_estab_editor"
    )

    # Normaliza a dicts con clave fecha-normalizada
    cap_overrides_ent = {}
    if not cap_overrides_ent_df.empty:
        tmp = cap_overrides_ent_df.dropna(subset=["FECHA"]).copy()
        tmp["FECHA"] = pd.to_datetime(tmp["FECHA"]).dt.normalize()
        for _, r in tmp.iterrows():
            cap_overrides_ent[r["FECHA"]] = {
                "CAP1": (int(r["CAP1"]) if pd.notna(r["CAP1"]) else None),
                "CAP2": (int(r["CAP2"]) if pd.notna(r["CAP2"]) else None),
            }
    st.session_state.cap_overrides_ent_df = cap_overrides_ent_df

    cap_overrides_sal = {}
    if not cap_overrides_sal_df.empty:
        tmp2 = cap_overrides_sal_df.dropna(subset=["FECHA"]).copy()
        tmp2["FECHA"] = pd.to_datetime(tmp2["FECHA"]).dt.normalize()
        for _, r in tmp2.iterrows():
            cap_overrides_sal[r["FECHA"]] = {
                "CAP1": (int(r["CAP1"]) if pd.notna(r["CAP1"]) else None),
                "CAP2": (int(r["CAP2"]) if pd.notna(r["CAP2"]) else None),
            }
    st.session_state.cap_overrides_sal_df = cap_overrides_sal_df

    estab_cap_overrides = {}
    if not cap_overrides_estab_df.empty:
        tmp3 = cap_overrides_estab_df.dropna(subset=["FECHA"]).copy()
        tmp3["FECHA"] = pd.to_datetime(tmp3["FECHA"]).dt.normalize()
        for _, r in tmp3.iterrows():
            if pd.notna(r["CAP"]):
                estab_cap_overrides[r["FECHA"]] = int(r["CAP"])
    st.session_state.cap_overrides_estab_df = cap_overrides_estab_df

    # ===============================
    # 🔧 Planificación incremental
    # ===============================
    st.markdown("### ⚙️ Modo de planificación")
    usar_plan_actual = st.toggle(
        "Usar planificación actual como base (no tocar lo ya planificado)",
        value=True,
        help="Si está activo, se parte de la planificación guardada en la sesión. Solo se intentan los lotes seleccionados (por defecto, los que no encajan o están sin ENTRADA)."
    )

    if usar_plan_actual and ("df_planificado" in st.session_state):
        df_base = st.session_state["df_planificado"].copy()
    else:
        df_base = df.copy()

    candidatos_mask = df_base["ENTRADA_SAL"].isna()
    if "LOTE_NO_ENCAJA" in df_base.columns:
        candidatos_mask = candidatos_mask | (df_base["LOTE_NO_ENCAJA"].astype(str).str.upper() == "SÍ")

    candidatos_df = df_base[candidatos_mask].copy()

    lotes_candidatos = candidatos_df["LOTE"].astype(str).tolist() if "LOTE" in candidatos_df.columns else candidatos_df.index.astype(str).tolist()
    lotes_select = st.multiselect(
        "Elige qué lotes quieres replanificar (solo estos se modificarán):",
        options=lotes_candidatos,
        default=lotes_candidatos,
        help="Por defecto se incluyen los lotes sin ENTRADA o con LOTE_NO_ENCAJA='Sí'."
    )

    if "LOTE" in df_base.columns:
        idx_a_replan = df_base[df_base["LOTE"].astype(str).isin(lotes_select)].index
    else:
        idx_a_replan = df_base.index[df_base.index.astype(str).isin(lotes_select)]

    df_trabajo = df_base.copy()

    # Liberar SOLO las filas seleccionadas preservando tipos (evita errores en data_editor)
    datetime_cols = [c for c in ["ENTRADA_SAL", "SALIDA_SAL"] if c in df_trabajo.columns]
    numeric_cols  = [c for c in ["DIAS_SAL", "DIAS_ALMACENADOS", "DIFERENCIA_DIAS_SAL"] if c in df_trabajo.columns]
    text_cols     = [c for c in ["LOTE_NO_ENCAJA"] if c in df_trabajo.columns]

    if datetime_cols:
        df_trabajo.loc[idx_a_replan, datetime_cols] = pd.NaT
    for c in numeric_cols:
        df_trabajo.loc[idx_a_replan, c] = pd.NA
    for c in text_cols:
        df_trabajo.loc[idx_a_replan, c] = pd.NA

    for c in datetime_cols:
        df_trabajo[c] = pd.to_datetime(df_trabajo[c], errors="coerce")
    for c in numeric_cols:
        df_trabajo[c] = pd.to_numeric(df_trabajo[c], errors="coerce").astype("Int64")

    # Botón de planificación incremental
    if st.button("🚀 Aplicar planificación (solo lotes seleccionados)"):
        df_planificado, df_sugerencias = planificar_filas_na(
            df_trabajo, dias_max_almacen_global, dias_max_por_producto,
            estab_cap, cap_overrides_ent, cap_overrides_sal, estab_cap_overrides
        )
        st.session_state["df_planificado"] = df_planificado
        st.session_state["df_sugerencias"] = df_sugerencias
        st.success(f"✅ Replanificación aplicada a {len(idx_a_replan)} lote(s). El resto no se ha modificado.")

    # ===============================
    # Mostrar tabla editable, gráfico y estabilización (fuera del botón)
    # ===============================
    if "df_planificado" in st.session_state:
        df_show = st.session_state["df_planificado"]

        # Diagnóstico opcional
        with st.expander("🧪 Diagnóstico dtypes", expanded=False):
            st.write(df_show.dtypes.astype(str))

        # Config de columnas robusta (según dtype real)
        column_config = {}
        for col in df_show.columns:
            s = df_show[col]
            try:
                if pd.api.types.is_datetime64_any_dtype(s):
                    column_config[col] = st.column_config.DateColumn(col, format="YYYY-MM-DD", disabled=False)
                elif pd.api.types.is_integer_dtype(s) or pd.api.types.is_float_dtype(s):
                    column_config[col] = st.column_config.NumberColumn(col, disabled=False)
                else:
                    column_config[col] = st.column_config.TextColumn(col)
            except Exception:
                column_config[col] = st.column_config.TextColumn(col)

        df_editable = st.data_editor(
            df_show,
            column_config=column_config,
            num_rows="dynamic",
            use_container_width=True
        )

        # -------------------------------
        # Gráfico: Entradas vs Salidas por lote/fecha
        # -------------------------------
        st.subheader("📊 Entradas y salidas por fecha con detalle por lote")

        fig = go.Figure()

        df_e = df_editable.dropna(subset=["ENTRADA_SAL", "UNDS"]) if "ENTRADA_SAL" in df_editable.columns else pd.DataFrame()
        df_s = df_editable.dropna(subset=["SALIDA_SAL", "UNDS"]) if "SALIDA_SAL" in df_editable.columns else pd.DataFrame()

        pivot_e = (
            df_e.groupby(["ENTRADA_SAL", "LOTE"])["UNDS"]
                .sum()
                .unstack(fill_value=0)
                .sort_index()
            if not df_e.empty and {"ENTRADA_SAL", "LOTE", "UNDS"}.issubset(df_e.columns)
            else pd.DataFrame()
        )
        pivot_s = (
            df_s.groupby(["SALIDA_SAL", "LOTE"])["UNDS"]
                .sum()
                .unstack(fill_value=0)
                .sort_index()
            if not df_s.empty and {"SALIDA_SAL", "LOTE", "UNDS"}.issubset(df_s.columns)
            else pd.DataFrame()
        )

        if not pivot_e.empty:
            for lote in pivot_e.columns:
                y_vals = pivot_e[lote]
                if (y_vals > 0).any():
                    fig.add_trace(go.Bar(
                        x=pivot_e.index,
                        y=y_vals,
                        name=f"Lote {lote}",
                        offsetgroup="entrada",
                        legendgroup=f"lote-{lote}",
                        marker_color="blue",
                        marker_line_color="white",
                        marker_line_width=1.2,
                        hovertemplate="Fecha: %{x|%Y-%m-%d}<br>Lote: " + str(lote) + "<br>UNDS: %{y}<extra></extra>",
                        showlegend=True
                    ))

        if not pivot_s.empty:
            for lote in pivot_s.columns:
                y_vals = pivot_s[lote]
                if (y_vals > 0).any():
                    fig.add_trace(go.Bar(
                        x=pivot_s.index,
                        y=y_vals,
                        name=f"Lote {lote} (Salida)",
                        offsetgroup="salida",
                        legendgroup=f"lote-{lote}",
                        marker_color="orange",
                        marker_line_color="white",
                        marker_line_width=1.2,
                        hovertemplate="Fecha: %{x|%Y-%m-%d}<br>Lote: " + str(lote) + "<br>UNDS: %{y}<extra></extra>",
                        showlegend=False
                    ))

        label_shift = pd.Timedelta(hours=8)
        annotations = []

        tot_e = pd.DataFrame()
        tot_s = pd.DataFrame()
        if not df_e.empty:
            if "LOTE" in df_e.columns:
                tot_e = df_e.groupby("ENTRADA_SAL").agg(UNDS=("UNDS","sum"), LOTES=("LOTE","nunique")).reset_index()
            else:
                tot_e = df_e.groupby("ENTRADA_SAL").agg(UNDS=("UNDS","sum"), LOTES=("UNDS","size")).reset_index()
        if not df_s.empty:
            if "LOTE" in df_s.columns:
                tot_s = df_s.groupby("SALIDA_SAL").agg(UNDS=("UNDS","sum"), LOTES=("LOTE","nunique")).reset_index()
            else:
                tot_s = df_s.groupby("SALIDA_SAL").agg(UNDS=("UNDS","sum"), LOTES=("UNDS","size")).reset_index()

        max_e = int(tot_e["UNDS"].max()) if not tot_e.empty else 0
        max_s = int(tot_s["UNDS"].max()) if not tot_s.empty else 0
        max_y = max(max_e, max_s) or 1

        def add_two_labels(x_dt, y_val, lots_count, is_entry=True):
            x_pos = x_dt - label_shift if is_entry else x_dt + label_shift
            y_base = max(y_val, max_y * 0.02)
            annotations.append(dict(
                x=x_pos, y=y_base, xref="x", yref="y",
                text=f"<b>{int(y_val)}</b>",
                showarrow=False, yshift=28,
                align="center", font=dict(size=13, color="black")
            ))
            annotations.append(dict(
                x=x_pos, y=y_base, xref="x", yref="y",
                text=f"{int(lots_count)} lotes",
                showarrow=False, yshift=12,
                align="center", font=dict(size=11, color="gray")
            ))

        if not tot_e.empty:
            for _, r in tot_e.iterrows():
                add_two_labels(r["ENTRADA_SAL"], r["UNDS"], r["LOTES"], is_entry=True)
        if not tot_s.empty:
            for _, r in tot_s.iterrows():
                add_two_labels(r["SALIDA_SAL"], r["UNDS"], r["LOTES"], is_entry=False)

        ticks = pd.Index(sorted(set(
            (pivot_e.index.tolist() if not pivot_e.empty else []) +
            (pivot_s.index.tolist() if not pivot_s.empty else [])
        )))
        fig.update_layout(
            barmode="relative",
            xaxis_title="Fecha",
            yaxis_title="Unidades",
            xaxis=dict(
                tickmode="array",
                tickvals=ticks,
                tickformat="%d %b (%a)"
            ),
            bargap=0.25,
            bargroupgap=0.12,
            annotations=annotations,
            legend=dict(
                itemclick="toggleothers",
                itemdoubleclick="toggle",
                groupclick="togglegroup"
            )
        )
        fig.update_yaxes(range=[0, max_y * 1.25])

        st.plotly_chart(fig, use_container_width=True)

        # ===============================
        # 📦 Estabilización: tabla + gráfico + descarga
        # ===============================
        df_estab = calcular_estabilizacion_diaria(df_editable, estab_cap, estab_cap_overrides)

        with st.expander("📦 Ocupación diaria de cámara de estabilización", expanded=True):
            if df_estab.empty:
                st.info("No hay días con stock en estabilización.")
            else:
                st.dataframe(df_estab, use_container_width=True, hide_index=True)

                colores = df_estab.apply(
                    lambda r: "crimson" if r["ESTAB_UNDS"] > r["CAPACIDAD"] else "teal",
                    axis=1
                )

                fig_est = go.Figure()
                fig_est.add_trace(go.Bar(
                    x=df_estab["FECHA"],
                    y=df_estab["ESTAB_UNDS"],
                    marker_color=colores,
                    hovertemplate="Fecha: %{x|%Y-%m-%d}<br>Unds: %{y}<extra></extra>",
                    showlegend=False
                ))
                fig_est.add_trace(go.Scatter(
                    x=df_estab["FECHA"],
                    y=df_estab["ESTAB_UNDS"],
                    mode="text",
                    text=[str(int(v)) for v in df_estab["ESTAB_UNDS"]],
                    textposition="top center",
                    showlegend=False
                ))
                fig_est.add_hline(
                    y=estab_cap, line_dash="dash", line_color="orange",
                    annotation_text=f"Capacidad: {estab_cap}",
                    annotation_position="top left"
                )
                fig_est.update_layout(
                    xaxis_title="Fecha",
                    yaxis_title="Unidades en estabilización",
                    bargap=0.25,
                    showlegend=False,
                    xaxis=dict(
                        tickmode="array",
                        tickvals=df_estab["FECHA"],
                        tickformat="%d %b (%a)"
                    )
                )
                st.plotly_chart(fig_est, use_container_width=True)

                estab_xlsx = generar_excel(df_estab, "estabilizacion_diaria.xlsx")
                st.download_button(
                    "💾 Descargar estabilización (Excel)",
                    data=estab_xlsx,
                    file_name="estabilizacion_diaria.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )

        # ===============================
        # 📌 Sugerencias para lotes que no encajan
        # ===============================
        if "df_sugerencias" in st.session_state:
            df_sug = st.session_state["df_sugerencias"]
        else:
            # Si no existe, intenta regenerarlas para el plan actual
            _, df_sug = planificar_filas_na(
                df_show, dias_max_almacen_global, dias_max_por_producto,
                estab_cap, cap_overrides_ent, cap_overrides_sal, estab_cap_overrides
            )
            st.session_state["df_sugerencias"] = df_sug

        with st.expander("🧩 Lotes que no encajan: sugerencias", expanded=not df_sug.empty):
            if df_sug.empty:
                st.success("Todos los lotes encajan con las restricciones actuales. 🎉")
            else:
                st.dataframe(df_sug, use_container_width=True, hide_index=True)
                sug_xlsx = generar_excel(df_sug, "sugerencias_lotes_no_encajan.xlsx")
                st.download_button(
                    "💾 Descargar sugerencias (Excel)",
                    data=sug_xlsx,
                    file_name="sugerencias_lotes_no_encajan.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )

        # -------------------------------
        # Botón para descargar Excel (resultado visible)
        # -------------------------------
        excel_bytes = generar_excel(df_editable, "planificacion_lotes.xlsx")
        st.download_button(
            label="💾 Descargar Excel con planificación",
            data=excel_bytes,
            file_name="planificacion_lotes.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
