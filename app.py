"""Patrio · planilha base para classificação contábil.

    streamlit run app.py
"""

import importlib
import io
import os
import sys

import pandas as pd
import streamlit as st

# Depois de uma atualização, o Streamlit Cloud recarrega este arquivo mas mantém na
# memória a versão antiga dos outros módulos (a leitura continua a de antes até alguém
# reiniciar o app). Recarregá-los aqui evita isso.
for _modulo in ("financeiro", "leitores_pdf", "importacao", "classificacao"):   # nessa ordem
    if _modulo in sys.modules:
        importlib.reload(sys.modules[_modulo])

from classificacao import bancos_na_matriz, classificar, codigos_na_matriz, ler_matriz, parece_matriz, parece_planilha_base
from importacao import COLUNAS_BASE, importar, para_excel

st.set_page_config(page_title="Patrio · Planilha base APP", layout="wide")
# Cabeçalho no visual da Patrio (cores em .streamlit/config.toml). O topo da página fica
# abaixo da barra do Streamlit, para o título não ser cortado.
st.markdown("""<style>
.block-container{padding-top:3.5rem}
.patrio{display:flex;align-items:baseline;gap:.8rem;flex-wrap:wrap;margin-bottom:.4rem;
        padding-bottom:.5rem;border-bottom:3px solid #C9A45C}
.patrio b{font-size:1.9rem;font-weight:800;color:#1B2A41;letter-spacing:.02em}
.patrio span{font-size:1.9rem;font-weight:800;color:#C9A45C;letter-spacing:.02em}
.patrio small{color:#5B6B80;font-size:.95rem}
</style>
<div class="patrio"><b>PLANILHA BASE</b><span>APP</span>
<small>Patrio · contabilidade também é estratégia</small></div>""", unsafe_allow_html=True)

enviados = st.file_uploader(
    "Arquivos do banco e do cliente (zip, OFX, PDF, CSV ou Excel), com a planilha matriz. "
    "Tudo é lido na memória e descartado ao fechar a página.", accept_multiple_files=True,
    type=["zip", "ofx", "pdf", "csv", "txt", "xlsx", "xls", "excel", "htm", "html"])
if not enviados:
    st.stop()

# Versão do código de leitura: entra na chave do cache, senão, depois de uma atualização,
# os mesmos arquivos continuam com a leitura antiga guardada
VERSAO = hash(tuple(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), m), "rb").read()
                    for m in ("financeiro.py", "leitores_pdf.py", "importacao.py", "classificacao.py")))


@st.cache_data(show_spinner="Lendo os arquivos...", max_entries=3)
def ler(arquivos, ajustes, versao):
    return importar(arquivos, ajustes)


@st.cache_data(max_entries=3)
def matriz_lida(conteudo, versao):
    return (ler_matriz(io.BytesIO(conteudo)), codigos_na_matriz(io.BytesIO(conteudo)),
            bancos_na_matriz(io.BytesIO(conteudo)))


def banco_parecido(conta, bancos):
    """Posição (na lista com "—" na frente) do banco da matriz com o nome desta conta: o
    nome igual, senão o nome mais longo contido nela (ex.: SICOOB em "... SICOOB CREDIMEP")."""
    chave = lambda t: " ".join(str(t).lower().split())
    nomes = [chave(b) for b in bancos]
    if chave(conta) in nomes:
        return nomes.index(chave(conta)) + 1
    contidos = [(len(b), i) for i, b in enumerate(nomes) if b and b in chave(conta)]
    return max(contidos)[1] + 1 if contidos else 0


arquivos = [(f.name, f.getvalue()) for f in enviados]
# A matriz enviada junto com os extratos vai para a classificação, não para a leitura
matrizes = [a for a in arquivos if parece_matriz(*a)]
arquivos = [a for a in arquivos if a not in matrizes]
# Uma planilha base baixada daqui não é extrato: lida de novo, duplicaria tudo
for nome, conteudo in [a for a in arquivos if parece_planilha_base(*a)]:
    st.warning(f"{nome} é uma planilha base gerada por este app e foi ignorada. "
               "Para classificar, envie a planilha matriz (descrição e conta contábil).")
    arquivos.remove((nome, conteudo))
ajustes = st.session_state.setdefault("ajustes", {})
base, leitura, layouts = ler(arquivos, ajustes, VERSAO)
base = base.copy()

# "Arquivos lidos" e as colunas das planilhas lado a lado
lado_arquivos, lado_colunas = st.columns(2) if layouts else (st.container(), None)
with lado_arquivos.expander(f"Arquivos lidos ({len(leitura)})", expanded=base.empty):
    st.dataframe(leitura, hide_index=True, width="stretch")

for n, (cols, info) in enumerate(layouts.items()):
    c = info["colunas"]
    completo = c.get("data") and c.get("descricao") and (c.get("valor") or c.get("credito"))
    duvidas = info.get("duvidas") or {}
    with lado_colunas.expander(f"Colunas de {', '.join(info['arquivos'])}",
                     expanded=not completo or bool(duvidas)):
        if duvidas:
            nomes = {"data": "data", "valor": "valor", "descricao": "histórico"}
            st.warning("Encontrei mais de uma coluna possível para "
                       + "; ".join(f"**{nomes[k]}** ({', '.join(map(str, v))})"
                                   for k, v in duvidas.items())
                       + ". Escolha abaixo quais usar e clique em **Usar estas colunas**.")
        opcoes = ["—"] + list(cols)
        idx = lambda k: opcoes.index(c[k]) if c.get(k) in opcoes else 0
        a, b, d = st.columns(3)
        e, f, g = st.columns(3)
        v = abs(hash(repr(c)))   # widgets recomeçam quando a escolha muda (ex.: pela barra lateral)
        novo = {
            "data": a.selectbox("Data", opcoes, idx("data"), key=f"d{n}-{v}"),
            "descricao": b.multiselect("Histórico (descrição)", list(cols), c.get("descricao") or [], key=f"h{n}-{v}"),
            "valor": d.selectbox("Valor", opcoes, idx("valor"), key=f"v{n}-{v}"),
            "credito": e.selectbox("Crédito", opcoes, idx("credito"), key=f"c{n}-{v}"),
            "debito": f.selectbox("Débito", opcoes, idx("debito"), key=f"b{n}-{v}"),
            "banco": g.selectbox("Banco/Conta de cada linha", opcoes, idx("banco"), key=f"k{n}-{v}",
                                 help="Coluna com o banco de cada lançamento. Sem ela, a conta "
                                      "é o nome do arquivo."),
        }
        novo = {k: (None if v == "—" else v) for k, v in novo.items()}
        st.dataframe(info["amostra"], width="stretch")
        if duvidas:
            if st.button("Usar estas colunas", key=f"ok{n}", type="primary"):
                ajustes[cols] = novo
                st.rerun()
        elif novo != {k: c.get(k) for k in novo}:
            ajustes[cols] = novo
            st.rerun()

if base.empty:
    if any(info.get("duvidas") for info in layouts.values()):
        st.info("Escolha as colunas acima para ler a planilha.")
    else:
        st.warning("Nenhum lançamento lido.")
    st.stop()

contas = sorted(base["Banco/Conta"].unique())

def nomes_curtos(nomes):
    """Tira o começo repetido ("BANCO COOPERATIVO DO BRASIL S.A. - BANCOOB - ...") para
    caber na barra lateral."""
    curtos = {}
    for n in nomes:
        prefixo = ""
        for outro in nomes:
            if outro != n:
                comum = n[:len(os.path.commonprefix([n, outro]))]
                comum = comum[:comum.rfind(" - ") + 3] if " - " in comum else ""
                prefixo = max(prefixo, comum, key=len)
        curtos[n] = n[len(prefixo):] or n
    return curtos


# Classificação na barra lateral, para a tabela aparecer logo abaixo dos arquivos.
# À vista só o histórico e o que falta preencher; o resto fica recolhido.
with st.sidebar:
    st.markdown("#### Lançamentos contábeis",
                help="Saída: débito no fornecedor, crédito no banco. Entrada: débito no banco, "
                     "crédito no cliente. A conta do fornecedor ou cliente vem da planilha matriz.")

    # Histórico das planilhas do cliente: dá para trocar a qualquer momento
    for n, (cols, info) in enumerate(layouts.items()):
        if info.get("duvidas"):
            continue
        atual = info["colunas"].get("descricao") or []
        escolha = st.multiselect("Histórico" + (f" de {', '.join(info['arquivos'])}"
                                                if len(layouts) > 1 else ""),
                                 list(cols), atual,
                                 key=f"hist{n}-{abs(hash(repr(info['colunas'])))}",
                                 help="Colunas juntadas para formar o histórico.")
        if escolha and escolha != atual:
            ajustes[cols] = dict(info["colunas"], descricao=escolha)
            st.rerun()

    matriz_enviada = st.session_state.get("matriz")
    matriz = matriz_enviada.getvalue() if matriz_enviada else (matrizes[0][1] if matrizes else None)
    matriz_df, codigos_matriz, bancos = matriz_lida(matriz, VERSAO) if matriz else (None, {}, {})

    curtos = nomes_curtos(list(contas) + list(bancos))
    opcoes_banco = {"—": ""} | {f"{c} · {curtos[b]}": c for b, c in bancos.items()}
    if len(opcoes_banco) <= len(bancos):   # nomes curtos repetidos: usa o nome inteiro
        opcoes_banco = {"—": ""} | {f"{c} · {b}": c for b, c in bancos.items()}
    codigos = {}
    # O código de cada conta: escolhido antes de desenhar, para saber se falta algum
    for conta in contas:
        chave = f"codigo {conta}"
        if not st.session_state.get(chave) and codigos_matriz.get(conta):
            st.session_state[chave] = codigos_matriz[conta]
    faltando = [c for c in contas if not st.session_state.get(f"codigo {c}")
                and not (bancos and banco_parecido(c, bancos))
                and st.session_state.get(f"banco {c}", "—") in ("—", "Outro código")]

    def campo_do_codigo(conta, pronto):
        chave = f"codigo {conta}"
        if bancos and not codigos_matriz.get(conta):
            # A matriz traz a tabela BANCO | CONTA: escolhe o banco desta conta. Já
            # escolhido, o próprio item mostra o nome e o rótulo some.
            opcoes = list(opcoes_banco) + ["Outro código"]
            escolha = st.selectbox(curtos[conta], opcoes, index=banco_parecido(conta, bancos),
                                   key=f"banco {conta}", help=conta,
                                   label_visibility="collapsed" if pronto else "visible")
            if escolha == "Outro código":
                return st.text_input(f"Código de {curtos[conta]}", key=chave).strip()
            return opcoes_banco[escolha]
        return st.text_input(curtos[conta], key=chave, help=conta).strip()

    if faltando:
        st.markdown("**Falta o código de**")
        for conta in faltando:
            codigos[conta] = campo_do_codigo(conta, pronto=False)
    prontas = [c for c in contas if c not in faltando]
    if prontas:
        with st.expander(f"Códigos das contas ({len(prontas)})"):
            for conta in prontas:
                codigos[conta] = campo_do_codigo(conta, pronto=True)

    with st.expander("Planilha matriz" + (" · falta enviar" if not matriz else ""),
                     expanded=not matriz):
        st.file_uploader("Trocar a planilha matriz" if matriz else "Planilha matriz",
                         type=["xlsx", "xls"], key="matriz")
        if matrizes and not matriz_enviada:
            st.caption(f"Usando {matrizes[0][0]}, enviada junto com os arquivos.")

if matriz:
    base = classificar(base, matriz_df, codigos)
    prontos = ((base["Débito"] != "") & (base["Crédito"] != "")).sum()
    achados = (base["Conta na matriz"] != "").sum()
    if not any(codigos.values()):
        st.warning(f"A matriz achou a conta de {achados} de {len(base)} lançamentos. Preencha o "
                   "código de cada conta na barra lateral para completar o lado do banco.")
    else:
        st.info(f"{prontos} de {len(base)} lançamentos com débito e crédito. "
                "O motivo dos outros está na coluna Conferir.")
else:
    st.info("Envie a planilha matriz para preencher débito, crédito e histórico.")

base.insert(base.columns.get_loc("Data"), "Entrada/Saída",
            base["Valor"].map(lambda v: "Entrada" if v > 0 else "Saída" if v < 0 else ""))

# Filtros da tabela
f_contas, f_tipo, f_situacao, f_busca = st.columns([3, 1.3, 1.5, 2.5])
filtro = f_contas.multiselect("Contas", contas, default=contas)
tipo = f_tipo.selectbox("Entrada/Saída", ["Todas", "Entrada", "Saída"])
situacao = f_situacao.selectbox("Classificação", ["Todos", "Com débito e crédito", "A conferir"])
busca = f_busca.text_input("Buscar", placeholder="fornecedor, histórico, conta, valor...")
vista = base[base["Banco/Conta"].isin(filtro) & (tipo == "Todas" or base["Entrada/Saída"] == tipo)]
completo = (vista["Débito"] != "") & (vista["Crédito"] != "")
if situacao == "Com débito e crédito":
    vista = vista[completo]
elif situacao == "A conferir":
    vista = vista[~completo]
if busca.strip():
    texto = vista.astype(str).apply(" ".join, axis=1).str.lower()
    vista = vista[texto.str.contains(busca.strip().lower(), regex=False)]
st.caption(f"{len(vista)} de {len(base)} lançamentos na tabela.")
extras = [c for c in ("Conta na matriz", "Conferir") if c in base]
colunas_vista = COLUNAS_BASE[:1] + ["Entrada/Saída"] + COLUNAS_BASE[1:] + extras
# Linhas sem débito ou crédito em amarelo-claro, para achar o que falta conferir
amarelo = lambda linha: ["background-color:#FFF6C7" if not (linha["Débito"] and linha["Crédito"])
                         else ""] * len(linha)
editada = st.data_editor(
    vista[colunas_vista].style.apply(amarelo, axis=1), hide_index=True, width="stretch", height=600,
    disabled=["Banco/Conta", "Data", "Descrição", "Valor", "Entrada/Saída"] + extras,
    column_config={"Data": st.column_config.DateColumn(format="DD/MM/YYYY"),
                   "Valor": st.column_config.NumberColumn(format="%.2f")},
)
base.loc[vista.index, ["Débito", "Crédito", "Histórico"]] = editada[["Débito", "Crédito", "Histórico"]].values

st.download_button("Baixar planilha base (.xlsx)", para_excel(base), "planilha_base.xlsx",
                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
