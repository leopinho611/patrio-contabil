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
for _modulo in ("financeiro", "leitores_pdf", "importacao", "classificacao", "razao"):   # nessa ordem
    if _modulo in sys.modules:
        importlib.reload(sys.modules[_modulo])

from classificacao import (TABELAS, a_classificar, classificar, codigos_na_matriz, linhas_novas_da_matriz,
                           matriz_da_tabela, matriz_mais, matriz_para_excel, parece_matriz,
                           parece_planilha_base, tabela_da_matriz, tabela_mais)
from importacao import COLUNAS_BASE, eh_cartao, importar, para_excel
from razao import Razao, ler_razao, parece_razao

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
    "Arquivos do banco e do cliente (zip, OFX, PDF, CSV ou Excel), com a planilha matriz e, se "
    "tiver, o razão do Domínio. "
    "Tudo é lido na memória e descartado ao fechar a página.", accept_multiple_files=True,
    type=["zip", "ofx", "pdf", "csv", "txt", "xlsx", "xls", "excel", "htm", "html"])
if not enviados:
    st.stop()

# Versão do código de leitura: entra na chave do cache, senão, depois de uma atualização,
# os mesmos arquivos continuam com a leitura antiga guardada
VERSAO = hash(tuple(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), m), "rb").read()
                    for m in ("financeiro.py", "leitores_pdf.py", "importacao.py", "classificacao.py",
                              "razao.py")))


@st.cache_data(show_spinner="Lendo os arquivos...", max_entries=3)
def ler(arquivos, ajustes, versao):
    return importar(arquivos, ajustes)


@st.cache_data(max_entries=3)
def matriz_lida(conteudo, versao):
    return tabela_da_matriz(io.BytesIO(conteudo)), codigos_na_matriz(io.BytesIO(conteudo))


@st.cache_resource(show_spinner="Lendo o razão...", max_entries=2)
def razao_lido(conteudo, versao):
    return Razao(*ler_razao(conteudo))


def aplicar_edicoes(tabela, vista, estado, nova_tabela="Participante"):
    """A tabela inteira com as mudanças feitas no editor sobre a vista filtrada (o
    Streamlit guarda as diferenças pela posição da linha na vista)."""
    t = tabela.copy()
    for pos, mudancas in estado.get("edited_rows", {}).items():
        for coluna, valor in mudancas.items():
            t.at[vista.index[int(pos)], coluna] = valor
    t = t.drop(index=[vista.index[i] for i in estado.get("deleted_rows", [])])
    incluidas = pd.DataFrame(estado.get("added_rows", []), columns=t.columns)
    incluidas["Tabela"] = incluidas["Tabela"].fillna(nova_tabela)
    return pd.concat([t, incluidas], ignore_index=True)


def banco_parecido(conta, bancos):
    """Posição (na lista com "—" na frente) do banco da matriz com o nome desta conta: o
    nome igual, senão o nome mais longo contido nela (ex.: SICOOB em "... SICOOB CREDIMEP")."""
    chave = lambda t: " ".join(str(t).lower().split())
    nomes = [chave(b) for b in bancos]
    if eh_cartao(conta):   # cartão: só uma linha que também seja do cartão
        nomes = [b if eh_cartao(b) else "" for b in nomes]
    if chave(conta) in nomes:
        return nomes.index(chave(conta)) + 1
    contidos = [(len(b), i) for i, b in enumerate(nomes) if b and b in chave(conta)]
    return max(contidos)[1] + 1 if contidos else 0


arquivos = [(f.name, f.getvalue()) for f in enviados]
# A matriz enviada junto com os extratos vai para a classificação, não para a leitura
matrizes = [a for a in arquivos if parece_matriz(*a)]
arquivos = [a for a in arquivos if a not in matrizes]
# O razão do Domínio também não é extrato: dá as sugestões de conta
razoes = [a for a in arquivos if parece_razao(*a)]
arquivos = [a for a in arquivos if a not in razoes]
razao = razao_lido(razoes[0][1], VERSAO) if razoes else None
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
        curto = n[len(prefixo):] or n
        # O cartão continua dizendo que é cartão, mesmo sem o começo do nome
        curtos[n] = f"Cartão {curto}" if eh_cartao(prefixo) and not eh_cartao(curto) else curto
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
    # A matriz vale como foi editada na aba "Planilha matriz"
    tabela_original, codigos_matriz = matriz_lida(matriz, VERSAO) if matriz else (None, {})
    # A tabela inteira, com as alterações feitas na aba "Planilha matriz"
    chave_matriz = f"matriz {hash(matriz)}"
    if matriz and chave_matriz not in st.session_state:
        st.session_state[chave_matriz] = {"tabela": tabela_original.copy(), "versao": 0}
    tabela_matriz = st.session_state[chave_matriz]["tabela"] if matriz else None
    matriz_df, bancos = matriz_da_tabela(tabela_matriz) if matriz else (None, {})

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

com_sentido = lambda b: b.assign(**{"Entrada/Saída": b["Valor"].map(
    lambda v: "Entrada" if v > 0 else "Saída" if v < 0 else "")})[
    list(b.columns[:1]) + ["Entrada/Saída"] + list(b.columns[1:])]
lida = base
if matriz:
    base = classificar(lida, matriz_df, codigos)
    achados = (base["Conta na matriz"] != "").sum()
    if not any(codigos.values()):
        st.warning(f"A matriz achou a conta de {achados} de {len(base)} lançamentos. Preencha o "
                   "código de cada conta na barra lateral para completar o lado do banco.")
    else:
        resumo = st.empty()   # preenchido depois das correções feitas na tabela
else:
    st.info("Envie a planilha matriz para preencher débito, crédito e histórico.")
base = com_sentido(base)

# Correções feitas na tabela: guardadas por lançamento, para não sumirem ao trocar o filtro
classificada = base.copy()
assinatura = hash((tuple((n, len(c)) for n, c in arquivos), VERSAO))
correcoes = st.session_state.setdefault("correcoes", {}).setdefault(assinatura, {})
EDITAVEIS = ["Débito", "Crédito", "Histórico"]


def corrigir(b):
    for i, valores in correcoes.items():
        if i in b.index:
            b.loc[i, EDITAVEIS] = valores
    return b


base = corrigir(base)
# O que foi corrigido vira linha nova da matriz, e já vale para os lançamentos iguais
novas = linhas_novas_da_matriz(classificada, base, matriz_df) if matriz else None
if novas is not None and len(novas):
    base = corrigir(com_sentido(classificar(lida, matriz_mais(matriz_df, novas), codigos)))
if matriz and any(codigos.values()):
    prontos = ((base["Débito"] != "") & (base["Crédito"] != "")).sum()
    resumo.info(f"{prontos} de {len(base)} lançamentos com débito e crédito. "
                "O motivo dos outros está na coluna Conferir.")

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
# Lançamentos ainda sem a conta do fornecedor/cliente, juntados pelo nome
do_cliente = [n for info in layouts.values() for n in info["arquivos"]]
pendentes = a_classificar(base, do_cliente) if matriz else None
if pendentes is not None and razao is not None:
    sugeridas = [razao.sugerir(e, s == "Entrada")
                 for e, s in zip(pendentes["Exemplo"], pendentes["Entrada/Saída"])]
    pendentes["Sugestão"] = [c or "" for c, _ in sugeridas]
    pendentes["Conta sugerida"] = [f"{c} · {razao.nome(c)}" if c else "" for c, _ in sugeridas]
    pendentes["Usar"] = False
# Com key, a aba aberta continua a mesma depois de classificar (o app roda de novo)
aba_base, aba_matriz, aba_classificar = st.tabs(
    ["Planilha base", "Planilha matriz", "A classificar"], key="aba", on_change="rerun")

# Matriz sem tabela de bancos: entra cada conta dos extratos com o código escolhido
if matriz:
    faltam = [c for c in contas if codigos.get(c) and c not in bancos and "(sem banco)" not in c
              and c not in set(tabela_matriz.loc[tabela_matriz["Tabela"] == "Banco", "Descrição"])]
    if faltam:
        tabela_matriz = pd.concat([tabela_matriz, pd.DataFrame(
            {"Tabela": "Banco", "Descrição": faltam, "Conta": [codigos[c] for c in faltam]})],
            ignore_index=True)
        st.session_state[chave_matriz]["tabela"] = tabela_matriz


def baixar_matriz(onde):
    nome_matriz = matriz_enviada.name if matriz_enviada else matrizes[0][0]
    st.download_button("Baixar planilha matriz (.xlsx)",
                       matriz_para_excel(tabela_mais(tabela_matriz, novas)
                                         if novas is not None and len(novas) else tabela_matriz,
                                         matriz),
                       nome_matriz.rsplit(".", 1)[0] + ".xlsx", XLSX, type="primary",
                       key=f"baixar matriz {onde}",
                       help="Com o que já tinha, o que foi classificado aqui, os bancos e as "
                            "correções da planilha base. Substitua a matriz da empresa por esta.")


with aba_matriz:
    if not matriz:
        st.info("Envie a planilha matriz junto com os arquivos ou na barra lateral.")
    else:
        estado_matriz = st.session_state[chave_matriz]
        m_tabela, m_descricao, m_conta = st.columns([1.5, 3, 1.5])
        filtro_tabela = m_tabela.selectbox("Tabela", ["Todas"] + TABELAS, key="filtro tabela")
        filtro_descricao = m_descricao.text_input("Descrição", placeholder="contém...",
                                                  key="filtro descricao")
        filtro_conta = m_conta.text_input("Conta contábil", placeholder="contém...",
                                          key="filtro conta")
        vista_matriz = tabela_matriz
        if filtro_tabela != "Todas":
            vista_matriz = vista_matriz[vista_matriz["Tabela"] == filtro_tabela]
        for coluna, filtro in (("Descrição", filtro_descricao), ("Conta", filtro_conta)):
            if filtro.strip():
                vista_matriz = vista_matriz[vista_matriz[coluna].astype(str).str.contains(
                    filtro.strip(), case=False, regex=False)]
        st.caption(f"{len(vista_matriz)} de {len(tabela_matriz)} linhas. Altere, inclua (última "
                   "linha vazia) ou apague linhas: a planilha base é reclassificada na hora.")
        chave_editor = (f"editor {chave_matriz} {estado_matriz['versao']} "
                        f"{filtro_tabela} {filtro_descricao} {filtro_conta}")
        st.data_editor(
            vista_matriz, key=chave_editor, num_rows="dynamic", hide_index=True,
            width="stretch", height=min(520, 35 * (len(vista_matriz) + 3)),
            column_config={
                "Tabela": st.column_config.SelectboxColumn(
                    options=TABELAS, width="small",
                    help="Receita só vale para entradas e Despesa só para saídas; Participante "
                         "vale para os dois. Banco é a conta contábil de cada banco."),
                "Descrição": st.column_config.TextColumn(width="large"),
                "Conta": st.column_config.TextColumn("Conta contábil", width="small")})
        edicoes = st.session_state.get(chave_editor) or {}
        if any(edicoes.get(k) for k in ("edited_rows", "added_rows", "deleted_rows")):
            estado_matriz["tabela"] = aplicar_edicoes(
                tabela_matriz, vista_matriz, edicoes,
                filtro_tabela if filtro_tabela != "Todas" else "Participante")
            estado_matriz["versao"] += 1
            st.rerun()
        if novas is not None and len(novas):
            st.markdown(f"**Linhas novas, das correções na planilha base ({len(novas)})**")
            st.dataframe(tabela_mais(tabela_matriz.iloc[:0], novas), hide_index=True,
                         width="stretch")
        baixar_matriz("aqui")


with aba_classificar:
    if not matriz:
        st.info("Envie a planilha matriz para ver o que falta classificar.")
    elif pendentes.empty:
        st.success("Todos os lançamentos têm a conta do fornecedor ou cliente.")
        baixar_matriz("classificar")
    else:
        estado_matriz = st.session_state[chave_matriz]
        c_sentido, c_busca = st.columns([1, 3])
        p_sentido = c_sentido.selectbox("Entrada/Saída", ["Todas", "Entrada", "Saída"],
                                        key="pendentes sentido")
        p_busca = c_busca.text_input("Buscar", placeholder="nome, histórico...",
                                     key="pendentes busca")
        vista_p = pendentes
        if p_sentido != "Todas":
            vista_p = vista_p[vista_p["Entrada/Saída"] == p_sentido]
        if p_busca.strip():
            texto_p = (vista_p["Descrição"] + " " + vista_p["Exemplo"]).str.lower()
            vista_p = vista_p[texto_p.str.contains(p_busca.strip().lower(), regex=False)]
        st.caption(f"{len(vista_p)} nomes, {int(vista_p['Lançamentos'].sum())} lançamentos sem a "
                   "conta do fornecedor ou cliente. Digite a conta contábil"
                   + (" ou marque Usar para aceitar a sugestão do razão" if razao else "")
                   + ": a linha entra na planilha matriz e os lançamentos já são classificados. "
                   "Ajuste a descrição se quiser gravar outro nome.")
        if razao is None:
            st.caption("Envie também o razão do Domínio para receber sugestões de conta.")
        colunas_p = ["Descrição", "Conta", "Tabela", "Entrada/Saída", "Lançamentos", "Valor",
                     "Exemplo"]
        if razao is not None:
            colunas_p[2:2] = ["Usar", "Conta sugerida"]
        editada_p = st.data_editor(
            vista_p, key=f"pendentes {chave_matriz} {estado_matriz['versao']} {p_sentido} {p_busca}",
            hide_index=True, width="stretch", height=min(520, 35 * (len(vista_p) + 2)),
            column_order=colunas_p,
            disabled=["Entrada/Saída", "Lançamentos", "Valor", "Exemplo", "Sugestão",
                      "Conta sugerida"],
            column_config={
                "Descrição": st.column_config.TextColumn("Descrição na matriz", width="medium",
                                                         help="O nome que vai para a matriz."),
                "Valor": st.column_config.NumberColumn("Total", format="%.2f"),
                "Exemplo": st.column_config.TextColumn("Exemplo de histórico", width="medium"),
                "Tabela": st.column_config.SelectboxColumn(
                    options=TABELAS[:3], width="small",
                    help="Receita só vale para entradas e Despesa só para saídas; "
                         "Participante vale para os dois."),
                "Conta": st.column_config.TextColumn("Conta", width="small",
                                                     help="Conta contábil (código reduzido)."),
                "Usar": st.column_config.CheckboxColumn(
                    width="small", help="Classifica com a conta sugerida pelo razão."),
                "Conta sugerida": st.column_config.TextColumn(
                    "Sugestão do razão", width="medium",
                    help="Pelo nome no plano de contas ou pela conta usada nos históricos "
                         "parecidos do razão.")})
        if "Usar" in editada_p:
            vazia = editada_p["Conta"].fillna("").astype(str).str.strip() == ""
            editada_p.loc[editada_p["Usar"] & vazia, "Conta"] = editada_p["Sugestão"]
        prontas_p = editada_p[editada_p["Conta"].fillna("").astype(str).str.strip() != ""]
        prontas_p = prontas_p[prontas_p["Descrição"].fillna("").astype(str).str.strip() != ""]
        if len(prontas_p):
            estado_matriz["tabela"] = pd.concat([tabela_matriz, pd.DataFrame({
                "Tabela": prontas_p["Tabela"].fillna("Participante"),
                "Descrição": prontas_p["Descrição"].astype(str).str.strip(),
                "Conta": prontas_p["Conta"].astype(str).str.strip()})], ignore_index=True)
            estado_matriz["versao"] += 1
            st.rerun()
        baixar_matriz("classificar")

with aba_base:
    # Filtros da tabela
    nomes_contas = {curtos[c]: c for c in contas}
    if len(nomes_contas) < len(contas):   # nomes curtos repetidos: usa o nome inteiro
        nomes_contas = {c: c for c in contas}
    f_banco, f_periodo, f_tipo, f_situacao, f_busca = st.columns([2.2, 2, 1.2, 1.6, 2.2])
    banco_visto = f_banco.selectbox("Banco/Conta", ["Todos"] + list(nomes_contas))
    datas = pd.to_datetime(base["Data"])
    periodo = f_periodo.date_input("Período", (datas.min().date(), datas.max().date()),
                                   format="DD/MM/YYYY")
    tipo = f_tipo.selectbox("Entrada/Saída", ["Todas", "Entrada", "Saída"])
    situacao = f_situacao.selectbox("Classificação", ["Todos", "Com débito e crédito", "A conferir"])
    busca = f_busca.text_input("Buscar", placeholder="fornecedor, histórico, conta, valor...")
    vista = base
    if banco_visto != "Todos":
        vista = vista[vista["Banco/Conta"] == nomes_contas[banco_visto]]
    if len(periodo) == 2:
        dias = pd.to_datetime(vista["Data"]).dt.date
        vista = vista[(dias >= periodo[0]) & (dias <= periodo[1])]
    if tipo != "Todas":
        vista = vista[vista["Entrada/Saída"] == tipo]
    completo = (vista["Débito"] != "") & (vista["Crédito"] != "")
    if situacao == "Com débito e crédito":
        vista = vista[completo]
    elif situacao == "A conferir":
        vista = vista[~completo]
    if busca.strip():
        texto_linha = vista.astype(str).apply(" ".join, axis=1).str.lower()
        vista = vista[texto_linha.str.contains(busca.strip().lower(), regex=False)]
    st.caption(f"{len(vista)} de {len(base)} lançamentos na tabela.")
    extras = [c for c in ("Conta na matriz", "Conferir") if c in base]
    colunas_vista = COLUNAS_BASE[:1] + ["Entrada/Saída"] + COLUNAS_BASE[1:] + extras
    # Linhas sem débito ou crédito em amarelo-claro, para achar o que falta conferir
    amarelo = lambda linha: ["background-color:#FFF6C7" if not (linha["Débito"] and linha["Crédito"])
                             else ""] * len(linha)
    editada = st.data_editor(
        vista[colunas_vista].style.apply(amarelo, axis=1), hide_index=True, width="stretch",
        height=600, disabled=["Banco/Conta", "Data", "Descrição", "Valor", "Entrada/Saída"] + extras,
        column_config={"Data": st.column_config.DateColumn(format="DD/MM/YYYY"),
                       "Valor": st.column_config.NumberColumn(format="%.2f"),
                       "Conta na matriz": st.column_config.TextColumn(
                           help="Linha da matriz usada para a conta do fornecedor ou cliente. "
                                "Se estiver errada, corrija na aba Planilha matriz.")})
    editada.index = vista.index
    texto = lambda df: df[EDITAVEIS].fillna("").astype(str).apply(lambda c: c.str.strip())
    mudou = (texto(editada) != texto(base.loc[vista.index])).any(axis=1)
    mudou = list(mudou[mudou].index)
    if mudou:
        for i in mudou:
            correcoes[i] = list(texto(editada).loc[i])
        st.rerun()

    st.download_button("Baixar planilha base (.xlsx)", para_excel(base), "planilha_base.xlsx", XLSX,
                       help="Com filtro em todas as colunas.")
