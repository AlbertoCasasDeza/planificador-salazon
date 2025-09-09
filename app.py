# app.py
import pandas as pd
import streamlit as st
from datetime import timedelta
import plotly.graph_objects as go
from io import BytesIO

st.set_page_config(page_title="Planificador Lotes Naturiber", layout="wide")
st.title("🧠 Planificador de Lotes Salazón Naturiber")

# -------------------------------
# Parámetros fijos (no visibles)
# -------------------------------
ESTAB_CAP = 3000  # capacidad diaria de la cámara de estabilización (unds)

# -------------------------------
# Panel de configuración (globales)
# -------------------------------
st.sidebar.header("Parámetros de planificación")
capacidad1 = st.sidebar.number_input("Capacidad máxima (1er intento)", value=3100, step=100)
capacidad2 = st.sidebar.number_input("Capacidad máxima (2º intento)", value=3500, step=100)

# Límite GLOBAL en días naturales entre DIA (recepción) y ENTRADA_SAL
dias_max_almacen_global = st.sidebar.number_input("Días máx. almacenamiento (GLOBAL)", value=5, step=1)

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

# Botón opcional para limpiar estado si algo “se queda pillado”
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
    # Días laborables y no festivos (fechas a medianoche según tu Excel)
    return fecha.weekday() < 5 and fecha not in dias_festivos

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

def _cabe_en_estab(dic, fecha_ini, fecha_fin_inclusive, unds, cap):
    """Comprueba si al sumar 'unds' en cada fecha del rango se respetaría 'cap'."""
    if pd.isna(fecha_ini) or pd.isna(fecha_fin_inclusive):
        return True
    if fecha_fin_inclusive < fecha_ini:
        return True  # entra el mismo día -> no ocupa estabilización
    for d in pd.date_range(fecha_ini, fecha_fin_inclusive, freq="D"):
        d0 = d.normalize()
        if dic.get(d0, 0) + unds > cap:
            return False
    return True

def calcular_estabilizacion_diaria(df_plan: pd.DataFrame, cap: int) -> pd.DataFrame:
    """
    Devuelve un dataframe con la ocupación diaria de la cámara de estabilización.
    Para cada lote, si ENTRADA_SAL > DIA, suma UNDS en todos los días [DIA, ENTRADA_SAL - 1].
    """
    carga = {}

    for _, r in df_plan.iterrows():
        dia = r.get("DIA")
        entrada = r.get("ENTRADA_SAL")
        unds = int(r.get("UNDS", 0) or 0)

        if pd.isna(dia) or pd.isna(entrada) or unds <= 0:
            continue

        fin = entrada - pd.Timedelta(days=1)
        if fin.date() < dia.date():
            continue

        for d in pd.date_range(dia.normalize(), fin.normalize(), freq="D"):
            d0 = d.normalize()
            carga[d0] = carga.get(d0, 0) + unds

    if not carga:
        return pd.DataFrame(columns=["FECHA", "ESTAB_UNDS", "CAPACIDAD", "UTIL_%", "EXCESO", "AL_DIA_SIGUIENTE"])

    df_estab = (
        pd.Series(carga, name="ESTAB_UNDS")
        .sort_index()
        .to_frame()
        .reset_index()
        .rename(columns={"index": "FECHA"})
    )
    df_estab["CAPACIDAD"] = cap
    df_estab["UTIL_%"] = (df_estab["ESTAB_UNDS"] / cap * 100).round(1)
    df_estab["EXCESO"] = (df_estab["ESTAB_UNDS"] - cap).clip(lower=0).astype(int)
    return df_estab

def generar_excel(df_out):
    output = BytesIO()
    df_out.to_excel(output, index=False)
    output.seek(0)
    return output

# -------------------------------
# Planificador (GLOBAL, overrides por PRODUCTO y estabilización)
# -------------------------------
def planificar_filas_na(df_plan, dias_max_almacen_global, dias_max_por_producto):
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

        for capacidad in [capacidad1, capacidad2]:
            entrada = entrada_ini

            # Límite: días naturales entre DIA y ENTRADA_SAL candidato
            while (entrada - dia_recepcion).days <= dias_max_almacen:
                # 1) Capacidad ENTRADA (día de entrada)
                if carga_entrada.get(entrada, 0) + unds <= capacidad:

                    # 2) Capacidad estabilización entre [DIA, ENTRADA-1]
                    if _cabe_en_estab(estab_stock, dia_recepcion, entrada - pd.Timedelta(days=1), unds, ESTAB_CAP):

                        # 3) Calcular SALIDA + ajustes
                        salida = entrada + timedelta(days=dias_sal_optimos)
                        if ajuste_finde:
                            if salida.weekday() == 5:
                                salida = anterior_habil(salida)
                            elif salida.weekday() == 6:
                                salida = siguiente_habil(salida)
                        if ajuste_festivos and (salida in dias_festivos):
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

                        # 4) Capacidad SALIDA (día de salida)
                        if carga_salida.get(salida, 0) + unds <= capacidad:
                            # Aceptamos
                            df_corr.at[idx, "ENTRADA_SAL"]      = entrada
                            df_corr.at[idx, "SALIDA_SAL"]       = salida
                            df_corr.at[idx, "DIAS_SAL"]         = (salida - entrada).days
                            df_corr.at[idx, "DIAS_ALMACENADOS"] = (entrada - dia_recepcion).days
                            df_corr.at[idx, "LOTE_NO_ENCAJA"]   = "No"

                            # actualizar cargas día
                            carga_entrada[entrada] = carga_entrada.get(entrada, 0) + unds
                            carga_salida[salida]   = carga_salida.get(salida, 0)   + unds

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
            disabled={"PRODUCTO": True},
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

    # Botón de planificación
    if st.button("🚀 Aplicar planificación"):
        df_planificado = planificar_filas_na(df, dias_max_almacen_global, dias_max_por_producto)
        st.session_state["df_planificado"] = df_planificado
        st.success("✅ Planificación aplicada a filas vacías.")

    # Mostrar tabla editable, gráfico y estabilización después de planificar
    if "df_planificado" in st.session_state:
        df_editable = st.data_editor(
            st.session_state["df_planificado"],
            column_config={
                col: st.column_config.DateColumn(col, disabled=False)
                if pd.api.types.is_datetime64_any_dtype(st.session_state["df_planificado"][col])
                else st.column_config.NumberColumn(col, disabled=False)
                for col in st.session_state["df_planificado"].columns
            },
            num_rows="dynamic",
            use_container_width=True
        )

        # -------------------------------
        # Gráfico: Entrada vs Salida lado a lado + apilado por LOTE
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

        # Entradas (azul): apiladas por LOTE en offsetgroup "entrada"
        if not pivot_e.empty:
            for lote in pivot_e.columns:
                y_vals = pivot_e[lote]
                if (y_vals > 0).any():
                    fig.add_trace(go.Bar(
                        x=pivot_e.index,
                        y=y_vals,
                        name=f"{lote} (Entrada)",
                        offsetgroup="entrada",
                        legendgroup="entrada",
                        marker_color="blue",
                        marker_line_color="white",
                        marker_line_width=1.2,
                        hovertemplate="Fecha: %{x|%Y-%m-%d}<br>Lote: " + str(lote) + "<br>UNDS: %{y}<extra></extra>",
                        showlegend=True
                    ))

        # Salidas (naranja): apiladas por LOTE en offsetgroup "salida"
        if not pivot_s.empty:
            for lote in pivot_s.columns:
                y_vals = pivot_s[lote]
                if (y_vals > 0).any():
                    fig.add_trace(go.Bar(
                        x=pivot_s.index,
                        y=y_vals,
                        name=f"{lote} (Salida)",
                        offsetgroup="salida",
                        legendgroup="salida",
                        marker_color="orange",
                        marker_line_color="white",
                        marker_line_width=1.2,
                        hovertemplate="Fecha: %{x|%Y-%m-%d}<br>Lote: " + str(lote) + "<br>UNDS: %{y}<extra></extra>",
                        showlegend=True
                    ))

        # Etiquetas separadas con anotaciones en píxeles (evitan solapes)
        label_shift = pd.Timedelta(hours=8)  # centra sobre el grupo
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

        # Eje X: todas las fechas presentes en entradas o salidas
        ticks = pd.Index(sorted(set(
            (pivot_e.index.tolist() if not pivot_e.empty else []) +
            (pivot_s.index.tolist() if not pivot_s.empty else [])
        )))
        fig.update_layout(
            barmode="relative",  # apila por lote en cada grupo y muestra entrada/salida lado a lado
            xaxis_title="Fecha",
            yaxis_title="Unidades",
            xaxis=dict(
                tickmode="array",
                tickvals=ticks,
                tickformat="%A, %-d %b"  # Inglés
            ),
            bargap=0.25,
            bargroupgap=0.12,
            annotations=annotations
        )
        fig.update_yaxes(range=[0, max_y * 1.25])

        st.plotly_chart(fig, use_container_width=True)

        # ===============================
        # 📦 Estabilización: tabla + gráfico + descarga
        # ===============================
        df_estab = calcular_estabilizacion_diaria(df_editable, ESTAB_CAP)

        with st.expander("📦 Ocupación diaria de cámara de estabilización", expanded=True):
            if df_estab.empty:
                st.info("No hay días con stock en estabilización (todas las entradas son el mismo día de recepción).")
            else:
                # Tabla
                st.dataframe(
                    df_estab,
                    use_container_width=True,
                    hide_index=True
                )

                # Gráfico (barras por día + línea de capacidad)
                colores = df_estab["ESTAB_UNDS"].apply(lambda v: "crimson" if v > ESTAB_CAP else "teal")

                fig_est = go.Figure()
                fig_est.add_trace(go.Bar(
                    x=df_estab["FECHA"],
                    y=df_estab["ESTAB_UNDS"],
                    marker_color=colores,
                    name="Ocupación estabilización",
                    hovertemplate="Fecha: %{x|%Y-%m-%d}<br>Unds: %{y}<extra></extra>"
                ))
                fig_est.add_hline(
                    y=ESTAB_CAP, line_dash="dash", line_color="orange",
                    annotation_text=f"Capacidad: {ESTAB_CAP}",
                    annotation_position="top left"
                )
                fig_est.update_layout(
                    xaxis_title="Fecha",
                    yaxis_title="Unidades en estabilización",
                    bargap=0.25,
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

