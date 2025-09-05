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

dias_festivos_default = [
    "2025-01-01","2025-04-18","2025-05-01","2025-08-15",
    "2025-10-12","2025-11-01","2025-12-25"
]
dias_festivos_list = st.sidebar.multiselect(
    "Selecciona los días festivos",
    options=dias_festivos_default,
    default=dias_festivos_default
)
dias_festivos = pd.to_datetime(dias_festivos_list)

ajuste_finde = st.sidebar.checkbox("Ajustar fines de semana (SALIDA)", value=True)
ajuste_festivos = st.sidebar.checkbox("Ajustar festivos (SALIDA)", value=True)

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

# -------------------------------
# Planificador (usa límite GLOBAL y overrides por PRODUCTO)
# -------------------------------
def planificar_filas_na(df_plan, dias_max_almacen_global, dias_max_por_producto):
    df_corr = df_plan.copy()
    # Cargas ya planificadas (se respetan)
    carga_entrada = df_corr.dropna(subset=["ENTRADA_SAL"]).groupby("ENTRADA_SAL")["UNDS"].sum().to_dict()
    carga_salida  = df_corr.dropna(subset=["SALIDA_SAL"]).groupby("SALIDA_SAL")["UNDS"].sum().to_dict()

    # Solo filas con ENTRADA_SAL NaT
    for idx, row in df_corr[df_corr["ENTRADA_SAL"].isna()].iterrows():
        dia_recepcion    = row["DIA"]
        unds             = row["UNDS"]
        dias_sal_optimos = int(row["DIAS_SAL_OPTIMOS"])

        # Límite por PRODUCTO (si no existe, usa GLOBAL)
        prod = row["PRODUCTO"] if "PRODUCTO" in df_corr.columns else None
        dias_max_almacen = dias_max_por_producto.get(prod, dias_max_almacen_global)

        entrada_valida = False

        # Primer día hábil >= DIA (el límite es por días naturales hasta esta "entrada")
        entrada_ini = dia_recepcion if es_habil(dia_recepcion) else siguiente_habil(dia_recepcion)

        for capacidad in [capacidad1, capacidad2]:
            entrada = entrada_ini

            # Límite por días naturales entre DIA y ENTRADA_SAL candidato
            while (entrada - dia_recepcion).days <= dias_max_almacen:
                # Capacidad ENTRADA
                if carga_entrada.get(entrada, 0) + unds <= capacidad:
                    # Salida ideal
                    salida = entrada + timedelta(days=dias_sal_optimos)

                    # Ajuste SALIDA por finde
                    if ajuste_finde:
                        if salida.weekday() == 5:
                            salida = anterior_habil(salida)
                        elif salida.weekday() == 6:
                            salida = siguiente_habil(salida)

                    # Ajuste SALIDA por festivo
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

                    # Capacidad SALIDA
                    if carga_salida.get(salida, 0) + unds <= capacidad:
                        # Aceptamos
                        df_corr.at[idx, "ENTRADA_SAL"]      = entrada
                        df_corr.at[idx, "SALIDA_SAL"]       = salida
                        df_corr.at[idx, "DIAS_SAL"]         = (salida - entrada).days
                        df_corr.at[idx, "DIAS_ALMACENADOS"] = (entrada - dia_recepcion).days
                        df_corr.at[idx, "LOTE_NO_ENCAJA"]   = "No"
                        carga_entrada[entrada] = carga_entrada.get(entrada, 0) + unds
                        carga_salida[salida]   = carga_salida.get(salida, 0)   + unds
                        entrada_valida = True
                        break

                # Siguiente día hábil (el límite natural lo controla el while)
                entrada = siguiente_habil(entrada)

            if entrada_valida:
                break

        # Si no encontró hueco en ninguna pasada
        if not entrada_valida:
            df_corr.at[idx, "LOTE_NO_ENCAJA"] = "Sí"

    # Métrica final
    df_corr["DIFERENCIA_DIAS_SAL"] = df_corr["DIAS_SAL"] - df_corr["DIAS_SAL_OPTIMOS"]
    return df_corr

def generar_excel(df_out):
    output = BytesIO()
    df_out.to_excel(output, index=False)
    output.seek(0)
    return output

# -------------------------------
# Ejecución de la app
# -------------------------------
if uploaded_file is not None:
    df = pd.read_excel(uploaded_file, engine="openpyxl")
    # Normaliza fechas
    df["DIA"]         = pd.to_datetime(df["DIA"], errors="coerce")
    df["ENTRADA_SAL"] = pd.to_datetime(df["ENTRADA_SAL"], errors="coerce")
    df["SALIDA_SAL"]  = pd.to_datetime(df["SALIDA_SAL"], errors="coerce")

    # ---- Overrides por PRODUCTO (sidebar) ----
    dias_max_por_producto = {}
    if "PRODUCTO" in df.columns:
        productos = sorted(df["PRODUCTO"].dropna().unique().tolist())
        st.sidebar.markdown("### ⏱️ Días máx. almacenamiento por PRODUCTO")

        # Inicializamos tabla de overrides si es la primera vez o si cambió el set de productos
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

    # Mostrar tabla editable, gráfico y descarga solo después de aplicar planificación
    if "df_planificado" in st.session_state:
        df_editable = st.data_editor(
            st.session_state["df_planificado"],
            column_config={
                col: st.column_config.DateColumn(col, disabled=False)
                if pd.api.types.is_datetime64_any_dtype(df[col])
                else st.column_config.NumberColumn(col, disabled=False)
                for col in df.columns
            },
            num_rows="dynamic"
        )

        # -------------------------------
        # Gráfico: Entrada vs Salida lado a lado + apilado por LOTE
        # -------------------------------
        st.subheader("📊 Entradas y salidas por fecha con detalle por lote")

        fig = go.Figure()

        # Preparar data limpia (evitar NaT)
        df_e = df_editable.dropna(subset=["ENTRADA_SAL", "UNDS"])
        df_s = df_editable.dropna(subset=["SALIDA_SAL", "UNDS"])

        # Pivot para apilar por LOTE dentro de cada fecha
        pivot_e = (
            df_e.groupby(["ENTRADA_SAL", "LOTE"])["UNDS"]
                .sum()
                .unstack(fill_value=0)
                .sort_index()
            if {"ENTRADA_SAL", "LOTE", "UNDS"}.issubset(df_e.columns)
            else pd.DataFrame()
        )
        pivot_s = (
            df_s.groupby(["SALIDA_SAL", "LOTE"])["UNDS"]
                .sum()
                .unstack(fill_value=0)
                .sort_index()
            if {"SALIDA_SAL", "LOTE", "UNDS"}.issubset(df_s.columns)
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
                        hovertemplate="Fecha: %{x}<br>Lote: " + str(lote) + "<br>UNDS: %{y}<extra></extra>",
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
                        hovertemplate="Fecha: %{x}<br>Lote: " + str(lote) + "<br>UNDS: %{y}<extra></extra>",
                        showlegend=True
                    ))

        # Etiquetas separadas: UNDS arriba y LOTES justo debajo, centradas sobre cada grupo
        label_shift = pd.Timedelta(hours=8)  # desplaza x para centrar sobre el grupo (entrada izquierda, salida derecha)

        if not df_e.empty:
            if "LOTE" in df_e.columns:
                tot_e = df_e.groupby("ENTRADA_SAL").agg(UNDS=("UNDS", "sum"), LOTES=("LOTE", "nunique")).reset_index()
            else:
                tot_e = df_e.groupby("ENTRADA_SAL").agg(UNDS=("UNDS", "sum"), LOTES=("UNDS", "size")).reset_index()

            for _, row in tot_e.iterrows():
                x_e = row["ENTRADA_SAL"] - label_shift
                y_e = row["UNDS"]
                # UNDS (arriba, fuera de la barra)
                fig.add_trace(go.Scatter(
                    x=[x_e],
                    y=[y_e * 1.06],
                    text=[f"{int(row['UNDS'])}"],
                    mode="text",
                    textposition="middle center",
                    textfont=dict(size=12, color="black"),
                    cliponaxis=False,
                    showlegend=False
                ))
                # LOTES (debajo de UNDS, aún fuera de la barra)
                fig.add_trace(go.Scatter(
                    x=[x_e],
                    y=[y_e * 1.03],
                    text=[f"{int(row['LOTES'])} lotes"],
                    mode="text",
                    textposition="middle center",
                    textfont=dict(size=10, color="gray"),
                    cliponaxis=False,
                    showlegend=False
                ))

        if not df_s.empty:
            if "LOTE" in df_s.columns:
                tot_s = df_s.groupby("SALIDA_SAL").agg(UNDS=("UNDS", "sum"), LOTES=("LOTE", "nunique")).reset_index()
            else:
                tot_s = df_s.groupby("SALIDA_SAL").agg(UNDS=("UNDS", "sum"), LOTES=("UNDS", "size")).reset_index()

            for _, row in tot_s.iterrows():
                x_s = row["SALIDA_SAL"] + label_shift
                y_s = row["UNDS"]
                # UNDS (arriba, fuera de la barra)
                fig.add_trace(go.Scatter(
                    x=[x_s],
                    y=[y_s * 1.06],
                    text=[f"{int(row['UNDS'])}"],
                    mode="text",
                    textposition="middle center",
                    textfont=dict(size=12, color="black"),
                    cliponaxis=False,
                    showlegend=False
                ))
                # LOTES (debajo de UNDS, aún fuera de la barra)
                fig.add_trace(go.Scatter(
                    x=[x_s],
                    y=[y_s * 1.03],
                    text=[f"{int(row['LOTES'])} lotes"],
                    mode="text",
                    textposition="middle center",
                    textfont=dict(size=10, color="gray"),
                    cliponaxis=False,
                    showlegend=False
                ))


        # Eje X: todas las fechas presentes en entradas o salidas
        ticks = pd.Index(sorted(set(
            (pivot_e.index.tolist() if not pivot_e.empty else []) +
            (pivot_s.index.tolist() if not pivot_s.empty else [])
        )))
        fig.update_layout(
            barmode="relative",  # apila por lote dentro de cada offsetgroup y muestra entrada/salida lado a lado
            xaxis_title="Fecha",
            yaxis_title="Unidades",
            xaxis=dict(
                tickmode="array",
                tickvals=ticks,
                tickformat="%A, %-d %b"  # Inglés: Monday, 8 Sep
            ),
            bargap=0.25,
            bargroupgap=0.10
        )

        st.plotly_chart(fig, use_container_width=True)

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



