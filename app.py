    # app.py
import pandas as pd
import streamlit as st
from datetime import timedelta
import plotly.graph_objects as go
from io import BytesIO

st.set_page_config(page_title="Planificador Lotes Naturiber", layout="wide")
st.title("🧠 Planificador de Lotes Salazón Naturiber")

# -------------------------------
# Panel de configuración (globales)
# -------------------------------
st.sidebar.header("Parámetros de planificación")
capacidad1 = st.sidebar.number_input("Capacidad máxima (1er intento)", value=3100, step=100)
capacidad2 = st.sidebar.number_input("Capacidad máxima (2º intento)", value=3500, step=100)

# Límite GLOBAL en días naturales entre DIA (recepción) y ENTRADA_SAL
dias_max_almacen_global = st.sidebar.number_input("Días máx. almacenamiento (GLOBAL)", value=5, step=1)

# Capacidad de estabilización en el sidebar (valor base)
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

    # Capacidad efectiva por fecha (override si existe)  # FIX: robusto sin int(None)
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

def generar_excel(df_out):
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
        return capacidad1 if attempt == 1 else capacidad2

    def get_cap_sal(date_dt, attempt):
        dkey = pd.to_datetime(date_dt).normalize()
        ov = cap_overrides_sal.get(dkey)
        if ov is not None:
            if attempt == 1 and pd.notna(ov.get("CAP1")):
                return int(ov["CAP1"])
            if attempt == 2 and pd.notna(ov.get("CAP2")):
                return int(ov["CAP2"])
        return capacidad1 if attempt == 1 else capacidad2

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

    # Solo filas con ENTRADA_SAL NaT
    for idx, row in df_corr[df_corr["ENTRADA_SAL"].isna()].iterrows():
        dia_recepcion    = row["DIA"]
        unds             = row["UNDS"]
        dias_sal_optimos = int(row["DIAS_SAL_OPTIMOS"])

        # Límite por PRODUCTO (si no existe, usa GLOBAL)
        prod = row["PRODUCTO"] if "PRODUCTO" in df_corr.columns else None
        dias_max_almacen = dias_max_por_producto.get(prod, dias_max_almacen_global)

        entrada_valida = False
        entrada_ini = dia_recepcion if es_habil(dia_recepcion) else siguiente_habil(dia_recepcion)

        # Intento 1 y luego Intento 2 (usando overrides de ENTRADA/SALIDA)
        for attempt in [1, 2]:
            entrada = entrada_ini

            # Límite: días naturales entre DIA y ENTRADA_SAL candidato
            while (entrada - dia_recepcion).days <= dias_max_almacen:

                # 1) Capacidad ENTRADA (día de entrada) con override de ENTRADA
                cap_ent_dia = get_cap_ent(entrada, attempt)
                if carga_entrada.get(entrada, 0) + unds <= cap_ent_dia:

                    # 2) Capacidad estabilización entre [DIA, ENTRADA-1] con overrides diarios
                    if cabe_en_estab_rango(dia_recepcion, entrada - pd.Timedelta(days=1), unds):

                        # 3) Calcular SALIDA + ajustes
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

                        # 4) Capacidad SALIDA (día de salida) con override de SALIDA
                        cap_sal_dia = get_cap_sal(salida, attempt)
                        if carga_salida.get(salida, 0) + unds <= cap_sal_dia:
                            # Aceptamos
                            df_corr.at[idx, "ENTRADA_SAL"]      = entrada
                            df_corr.at[idx, "SALIDA_SAL"]       = salida
                            df_corr.at[idx, "DIAS_SAL"]         = (salida - entrada).days
                            df_corr.at[idx, "DIAS_ALMACENADOS"] = (entrada - dia_recepcion).days
                            df_corr.at[idx, "LOTE_NO_ENCAJA"]   = "No"

                            # actualizar cargas día
                            carga_entrada[entrada] = carga_entrada.get(entrada, 0) + unds
                            carga_salida[salida]   = carga_salida.get(salida, 0) + unds

                            # actualizar ocupación en estabilización [DIA, ENTRADA-1]
                            if entrada.date() > dia_recepcion.date():
                                _sumar_en_rango(estab_stock, dia_recepcion, entrada - pd.Timedelta(days=1), unds)

                            entrada_valida = True
                            break

                # siguiente día hábil (el límite natural lo controla el while)
                entrada = siguiente_habil(entrada)

            if entrada_valida:
                break

        if not entrada_valida:
            df_corr.at[idx, "LOTE_NO_ENCAJA"] = "Sí"

    # Métrica final
    df_corr["DIFERENCIA_DIAS_SAL"] = df_corr["DIAS_SAL"] - df_corr["DIAS_SAL_OPTIMOS"]
    return df_corr

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

        # Inicializa/actualiza tabla de overrides si cambian los productos
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
            disabled=["PRODUCTO"],  # proteger columna producto
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
    st.sidebar.markdown("### 📅 Capacidad máx. ENTRADA por día (opcional)")

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
    st.sidebar.markdown("### 📅 Capacidad máx. SALIDA por día (opcional)")

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
    # Asegura dtypes
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

    # FIX: no guardar None; si CAP está vacío, se ignora ese override
    estab_cap_overrides = {}
    if not cap_overrides_estab_df.empty:
        tmp3 = cap_overrides_estab_df.dropna(subset=["FECHA"]).copy()
        tmp3["FECHA"] = pd.to_datetime(tmp3["FECHA"]).dt.normalize()
        for _, r in tmp3.iterrows():
            if pd.notna(r["CAP"]):
                estab_cap_overrides[r["FECHA"]] = int(r["CAP"])
    st.session_state.cap_overrides_estab_df = cap_overrides_estab_df

    # Botón de planificación
    if st.button("🚀 Aplicar planificación"):
        df_planificado = planificar_filas_na(
            df, dias_max_almacen_global, dias_max_por_producto,
            estab_cap, cap_overrides_ent, cap_overrides_sal, estab_cap_overrides
        )
        st.session_state["df_planificado"] = df_planificado
        st.success("✅ Planificación aplicada a filas vacías.")

    # Mostrar tabla editable, gráfico y estabilización después de planificar
    if "df_planificado" in st.session_state:
        df_show = st.session_state["df_planificado"]
        # Column config: fecha / número / texto
        column_config = {}
        for col in df_show.columns:
            s = df_show[col]
            if pd.api.types.is_datetime64_any_dtype(s):
                column_config[col] = st.column_config.DateColumn(col, format="YYYY-MM-DD", disabled=False)
            elif pd.api.types.is_numeric_dtype(s):
                column_config[col] = st.column_config.NumberColumn(col, disabled=False)
            else:
                column_config[col] = st.column_config.TextColumn(col)

        df_editable = st.data_editor(
            df_show,
            column_config=column_config,
            num_rows="dynamic",
            use_container_width=True
        )

        # -------------------------------
        # Gráfico: Entrada vs Salida lado a lado + apilado por LOTE (leyenda por lote)
        # -------------------------------
        st.subheader("📊 Entradas y salidas por fecha con detalle por lote")

        fig = go.Figure()

        # Preparar data limpia (evitar NaT)
        df_e = df_editable.dropna(subset=["ENTRADA_SAL", "UNDS"]) if "ENTRADA_SAL" in df_editable.columns else pd.DataFrame()
        df_s = df_editable.dropna(subset=["SALIDA_SAL", "UNDS"]) if "SALIDA_SAL" in df_editable.columns else pd.DataFrame()

        # Pivot para apilar por LOTE dentro de cada fecha
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

        # Entradas (azul)
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

        # Salidas (naranja)
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

        # Etiquetas separadas
        label_shift = pd.Timedelta(hours=8)
        annotations = []

        # Totales Entrada/Salida
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

        # Headroom vertical
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

                # Colores por exceso relativo a la capacidad del día (opcional)
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
                # Etiqueta con totales sobre cada barra
                fig_est.add_trace(go.Scatter(
                    x=df_estab["FECHA"],
                    y=df_estab["ESTAB_UNDS"],
                    mode="text",
                    text=[str(int(v)) for v in df_estab["ESTAB_UNDS"]],
                    textposition="top center",
                    showlegend=False
                ))
                # ⛳ Línea horizontal fija (estilo antiguo): capacidad base con etiqueta
                fig_est.add_hline(
                    y=estab_cap, line_dash="dash", line_color="orange",
                    annotation_text=f"Capacidad: {estab_cap}",
                    annotation_position="top left"
                )
                fig_est.update_layout(
                    xaxis_title="Fecha",
                    yaxis_title="Unidades en estabilización",
                    bargap=0.25,
                    showlegend=False,      # <- sin leyenda
                    xaxis=dict(
                        tickmode="array",
                        tickvals=df_estab["FECHA"],
                        tickformat="%d %b (%a)"   # 👈 formato: 11 Sep (Thu)
                    )
                )
                st.plotly_chart(fig_est, use_container_width=True)

                # Descargar Excel de estabilización
                estab_xlsx = BytesIO()
                df_estab.to_excel(estab_xlsx, index=False)
                estab_xlsx.seek(0)
                st.download_button(
                    "💾 Descargar estabilización (Excel)",
                    data=estab_xlsx,
                    file_name="estabilizacion_diaria.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )

        # -------------------------------
        # Botón para descargar Excel (resultado visible)
        # -------------------------------
        excel_bytes = generar_excel(df_editable)
        st.download_button(
            label="💾 Descargar Excel con planificación",
            data=excel_bytes,
            file_name="planificacion_lotes.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )




