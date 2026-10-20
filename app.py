"""Patrio · planilha base para classificação contábil.

    streamlit run app.py
"""

import importlib
import io
import sys

import pandas as pd
import streamlit as st

# Depois de uma atualização, o Streamlit Cloud recarrega este arquivo mas mantém na
# memória a versão antiga dos outros módulos (a leitura continua a de antes até alguém
# reiniciar o app). Recarregá-los aqui evita isso.
for _modulo in ("financeiro", "leitores_pdf", "importacao", "classificacao"):   # nessa ordem
    if _modulo in sys.modules:
        importlib.reload(sys.modules[_modulo])

from classificacao import classificar, ler_matriz
from importacao import COLUNAS_BASE, importar, para_excel

st.set_page_config(page_title="Patrio · Planilha base", layout="wide")
st.title("Planilha base")
st.caption("Envie os arquivos do banco e do cliente (zip, OFX, PDF, CSV ou Excel). "
           "Tudo é lido na memória e descartado ao fechar a página.")

enviados = st.file_uploader("Arquivos", accept_multiple_files=True,
                            type=["zip", "ofx", "pdf", "csv", "txt", "xlsx", "xls", "excel"])
if not enviados:
    st.stop()

@st.cache_data(show_spinner="Lendo os arquivos...", max_entries=3)
def ler(arquivos, ajustes):
    return importar(arquivos, ajustes)


@st.cache_data(max_entries=3)
def matriz_lida(conteudo):
    return ler_matriz(io.BytesIO(conteudo))


arquivos = [(f.name, f.getvalue()) for f in enviados]
ajustes = st.session_state.setdefault("ajustes", {})
base, leitura, layouts = ler(arquivos, ajustes)
base = base.copy()

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

contas = sorted(base["Banco/Conta"].unique())

st.subheader("Lançamentos contábeis")
st.caption("Saída do banco: débito no fornecedor, crédito no banco. Entrada: débito no banco, "
           "crédito no cliente. A conta do fornecedor ou cliente vem da planilha matriz.")
matriz_enviada = st.file_uploader("Planilha matriz", type=["xlsx", "xls"], key="matriz")
st.write("Código contábil de cada conta (banco, aplicação, cartão):")
codigos = {}
colunas = st.columns(2)
for n, conta in enumerate(contas):
    codigos[conta] = colunas[n % 2].text_input(conta, key=f"codigo {conta}").strip()
if matriz_enviada:
    base = classificar(base, matriz_lida(matriz_enviada.getvalue()), codigos)
    prontos = ((base["Débito"] != "") & (base["Crédito"] != "")).sum()
    st.info(f"{prontos} de {len(base)} lançamentos com débito e crédito. "
            "O motivo dos outros está na coluna Conferir.")
else:
    st.info("Envie a planilha matriz para preencher débito, crédito e histórico.")

filtro = st.multiselect("Contas", contas, default=contas)
vista = base[base["Banco/Conta"].isin(filtro)]
extras = [c for c in ("Conta na matriz", "Conferir") if c in base]
editada = st.data_editor(
    vista[COLUNAS_BASE + extras], hide_index=True, width="stretch", height=600,
    disabled=["Banco/Conta", "Data", "Descrição", "Valor"] + extras,
    column_config={"Data": st.column_config.DateColumn(format="DD/MM/YYYY"),
                   "Valor": st.column_config.NumberColumn(format="%.2f")},
)
base.loc[vista.index, ["Débito", "Crédito", "Histórico"]] = editada[["Débito", "Crédito", "Histórico"]].values

st.download_button("Baixar planilha base (.xlsx)", para_excel(base), "planilha_base.xlsx",
                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
