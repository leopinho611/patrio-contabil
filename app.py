"""Patrio · planilha base para classificação contábil.

    streamlit run app.py
"""

import pandas as pd
import streamlit as st

from importacao import COLUNAS_BASE, importar, para_excel

st.set_page_config(page_title="Patrio · Planilha base", layout="wide")
st.title("Planilha base")
st.caption("Envie os arquivos do banco e do cliente (zip, OFX, PDF, CSV ou Excel). "
           "Tudo é lido na memória e descartado ao fechar a página.")

enviados = st.file_uploader("Arquivos", accept_multiple_files=True,
                            type=["zip", "ofx", "pdf", "csv", "txt", "xlsx", "xls", "excel"])
if not enviados:
    st.stop()

arquivos = [(f.name, f.getvalue()) for f in enviados]
ajustes = st.session_state.setdefault("ajustes", {})
base, leitura, layouts = importar(arquivos, ajustes)

with st.expander(f"Arquivos lidos ({len(leitura)})", expanded=base.empty):
    st.dataframe(leitura, hide_index=True, width="stretch")

for n, (cols, info) in enumerate(layouts.items()):
    c = info["colunas"]
    completo = c.get("data") and c.get("descricao") and (c.get("valor") or c.get("credito"))
    with st.expander(f"Colunas de {', '.join(info['arquivos'])}", expanded=not completo):
        opcoes = ["—"] + list(cols)
        idx = lambda k: opcoes.index(c[k]) if c.get(k) in opcoes else 0
        a, b, d, e, f = st.columns(5)
        novo = {
            "data": a.selectbox("Data", opcoes, idx("data"), key=f"d{n}"),
            "descricao": b.multiselect("Descrição", list(cols), c.get("descricao") or [], key=f"h{n}"),
            "valor": d.selectbox("Valor", opcoes, idx("valor"), key=f"v{n}"),
            "credito": e.selectbox("Crédito", opcoes, idx("credito"), key=f"c{n}"),
            "debito": f.selectbox("Débito", opcoes, idx("debito"), key=f"b{n}"),
        }
        novo = {k: (None if v == "—" else v) for k, v in novo.items()}
        st.dataframe(info["amostra"], width="stretch")
        if novo != {k: c.get(k) for k in novo}:
            ajustes[cols] = novo
            st.rerun()

if base.empty:
    st.warning("Nenhum lançamento lido.")
    st.stop()

contas = base.groupby("Banco/Conta")["Valor"].agg(Lançamentos="count", Entradas=lambda v: v[v > 0].sum(),
                                                  Saídas=lambda v: v[v < 0].sum())
st.subheader(f"{len(base)} lançamentos em {len(contas)} conta(s)")
st.dataframe(contas, width="stretch",
             column_config={"Entradas": st.column_config.NumberColumn(format="R$ %.2f"),
                            "Saídas": st.column_config.NumberColumn(format="R$ %.2f")})

filtro = st.multiselect("Contas", list(contas.index), default=list(contas.index))
vista = base[base["Banco/Conta"].isin(filtro)]
editada = st.data_editor(
    vista[COLUNAS_BASE], hide_index=True, width="stretch", height=600,
    disabled=["Banco/Conta", "Data", "Descrição", "Valor"],
    column_config={"Data": st.column_config.DateColumn(format="DD/MM/YYYY"),
                   "Valor": st.column_config.NumberColumn(format="%.2f")},
)
base.loc[vista.index, ["Débito", "Crédito", "Histórico"]] = editada[["Débito", "Crédito", "Histórico"]].values

st.download_button("Baixar planilha base (.xlsx)", para_excel(base), "planilha_base.xlsx",
                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
