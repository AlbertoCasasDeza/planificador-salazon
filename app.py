# app_planificador_lotes_vfinal_producto.py
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

# 👉 Este es el límite GLOBAL en días naturales entre DIA (recepción) y ENTRADA_SAL
dias_max_almacen_global = st.sidebar.number_input("Días máx. almacenamiento (GLOBAL)", value=5, step=1)

dias_festivos_default = ["2025-01-01","2025-04-18","2025-05-01","2025-08-15","2025-10-12","2025-11-01","2025-12-25"]
dias_festivos_list = st.sidebar.multiselect(
    "Selecciona los días festivos",
    options=dias_festivos_default,
    default=dias_festivos_default
)
dias_festivos = pd.to_datetime(dias_festivos_list)

ajuste_finde = st.sidebar.checkbox("Ajustar fines de semana", value=True)
ajuste_festivos = st.sidebar.checkbox("Ajustar festivos", value=True)

# -------------------------------
# Subir archivo Excel
# -------------------------------
uploaded_file = st.file_uploader("📂 Sube tu Excel con los lotes", type=["xlsx"])

# -------------------------------
# Funciones auxiliares
# -------------------------------
def es_habil(fecha):
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
    carga_entrada = df_corr.dropna(subset=["ENTRADA_SAL"]).groupby("ENTRADA_SAL")["UNDS"].sum().to_dict()
    carga_salida = df_corr.dropna(subset=["SALIDA_SAL"]).groupby("SALIDA_SAL")["UNDS"].sum().to_dict()

    for idx, row in df_corr[df_corr["ENTRADA_SAL"].isna()].iterrows():
        dia_recepcion = row["DIA"]
        unds = row["UNDS"]
        dias_sal_optimos = int(row["DIAS_SAL_OPTIMOS"])

        # 👇 Límite por PRODUCTO (si no existe, usa el GLOBAL)
        prod = row["PRODUCTO"] if "PRODUCTO" in df_corr.columns else None
        dias_max_almacen = dias_max_por_producto.get(prod, dias_max_almacen_global)

        entrada_valida = False

        # Punto de partida: primer día hábil >= DIA (el límite se mide en días naturales)
        entrada_ini = dia_recepcion if es_habil(dia_recepcion) else siguiente_habil(dia_recepcion)

        for capacidad in [capacidad1, capacidad2]:
            entrada = entrada_ini

            # Límite por días naturales entre DIA y ENTRADA_SAL candidato
            while (entrada - dia_recepcion).days <= dias_max_almacen:
                # Capacidad en ENTRADA
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
                            carga_ant = carga_salida.get(anterior, 0)
                            carga_sig = carga_salida.get(siguiente, 0)
                            salida = anterior if carga_ant <= carga_sig else siguiente
                        elif dia_semana == 4:
                            salida = anterior_habil(salida)

                    # Capacidad en SALIDA
                    if carga_salida.get(salida, 0) + unds <= capacidad:
                        # Aceptamos
                        df_corr.at[idx, "ENTRADA_SAL"] = entrada
                        df_corr.at[idx, "SALIDA_SAL"] = salida
                        df_corr.at[idx, "DIAS_SAL"] = (salida - entrada).days
                        df_corr.at[idx, "DIAS_ALMACENADOS"] = (entrada - dia_recepcion).days
                        df_corr.at[idx, "LOTE_NO_ENCAJA"] = "No"
                        carga_entrada[entrada] = carga_entrada.get(entrada, 0) + unds
                        carga_salida[salida] = carga_salida.get(salida, 0) + unds
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

def generar_excel(df):
    output = BytesIO()
    df.to_excel(output, index=False)
    output.seek(0)
    return output

# -------------------------------
# Ejecución de la app
# -------------------------------
if uploaded_file is not None:
    df = pd.read_excel(uploaded_file, engine="openpyxl")
    df["DIA"] = pd.to_datetime(df["DIA"], errors="coerce")
    df["ENTRADA_SAL"] = pd.to_datetime(df["ENTRADA_SAL"], errors="coerce")
    df["SALIDA_SAL"] = pd.to_datetime(df["SALIDA_SAL"], errors="coerce")

    # ---- Editor de overrides por PRODUCTO (aparece si el Excel trae la columna) ----
    dias_max_por_producto = {}
    if "PRODUCTO" in df.columns:
        productos = sorted(df["PRODUCTO"].dropna().unique().tolist())
        st.sidebar.markdown("### ⏱️ Días máx. almacenamiento por PRODUCTO")

        # Inicializamos una tabla editable en el sidebar la primera vez o si cambian los productos
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

    # Mostrar tabla editable solo después de aplicar planificación
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
        # Gráfico (lado a lado, detalle por lote)
        # -------------------------------
        st.subheader("📊 Entradas y salidas por fecha con detalle por lote")

        fig = go.Figure()

        # Entradas (azul)
        if "LOTE" in df_editable.columns:
            for lote, df_lote in df_editable.groupby("LOTE"):
                fig.add_trace(go.Bar(
                    x=df_lote["ENTRADA_SAL"],
                    y=df_lote["UNDS"],
                    name=f"Lote {lote} (Entrada)",
                    offsetgroup="entrada",
                    marker_color="blue",
                    showlegend=True
                ))
        else:
            fig.add_trace(go.Bar(
                x=df_editable["ENTRADA_SAL"],
                y=df_editable["UNDS"],
                name="Entrada",
                offsetgroup="entrada",
                marker_color="blue",
                showlegend=True
            ))

        # Salidas (naranja)
        if "LOTE" in df_editable.columns:
            for lote, df_lote in df_editable.groupby("LOTE"):
                fig.add_trace(go.Bar(
                    x=df_lote["SALIDA_SAL"],
                    y=df_lote["UNDS"],
                    name=f"Lote {lote} (Salida)",
                    offsetgroup="salida",
                    marker_color="orange",
                    showlegend=True
                ))
        else:
            fig.add_trace(go.Bar(
                x=df_editable["SALIDA_SAL"],
                y=df_editable["UNDS"],
                name="Salida",
                offsetgroup="salida",
                marker_color="orange",
                showlegend=True
            ))

        fig.update_layout(
            barmode="group",  # lado a lado
            xaxis_title="Fecha",
            yaxis_title="Unidades",
            xaxis=dict(tickmode="linear"),
            bargap=0.2,
            bargroupgap=0.05
        )

        # Etiquetas totales por día (Entrada y Salida)
        totales_entrada = df_editable.groupby("ENTRADA_SAL")["UNDS"].sum().reset_index()
        for _, row in totales_entrada.iterrows():
            fig.add_trace(go.Scatter(
                x=[row["ENTRADA_SAL"]],
                y=[row["UNDS"]],
                text=[row["UNDS"]],
                textposition="top center",
                mode="text",
                showlegend=False
            ))
        totales_salida = df_editable.groupby("SALIDA_SAL")["UNDS"].sum().reset_index()
        for _, row in totales_salida.iterrows():
            fig.add_trace(go.Scatter(
                x=[row["SALIDA_SAL"]],
                y=[row["UNDS"]],
                text=[row["UNDS"]],
                textposition="top center",
                mode="text",
                showlegend=False
            ))

        st.plotly_chart(fig, use_container_width=True)

        # -------------------------------
        # Botón para descargar Excel
        # -------------------------------
        def generar_excel(df_out):
            output = BytesIO()
            df_out.to_excel(output, index=False)
            output.seek(0)
            return output

        excel_bytes = generar_excel(df_editable)
        st.download_button(
            label="💾 Descargar Excel con planificación",
            data=excel_bytes,
            file_name="planificacion_lotes.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

