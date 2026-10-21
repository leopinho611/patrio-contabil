"""
Monta a planilha base a partir dos arquivos do banco e do cliente.

Recebe arquivos soltos ou compactados (.zip, inclusive zip dentro de zip), lê cada um
com os leitores de financeiro.py e devolve uma tabela só, com as colunas contábeis
(Débito, Crédito, Histórico) prontas para serem preenchidas.
"""

import io
import re
import zipfile

import pandas as pd

from financeiro import (
    _NOMES_DATA,
    _NOMES_VALOR,
    _casa,
    _tag_ofx,
    converter_datas,
    converter_valor,
    normalizar,
    carregar_arquivo,
    consolidar,
    detectar_colunas,
    expandir_zip,
    montar_transacoes,
)
from leitores_pdf import SemLancamentos

COLUNAS_BASE = ["Banco/Conta", "Data", "Descrição", "Valor", "Débito", "Crédito", "Histórico"]
PROFUNDIDADE_ZIP = 3
# Lidos primeiro: na duplicata entre formatos, fica a descrição do primeiro (a mais completa)
ORDEM_FORMATO = {"csv": 0, "xlsx": 0, "xls": 0, "excel": 0, "ofx": 1, "pdf": 2}


def _eh_zip(nome, conteudo):
    return nome.lower().endswith(".zip") or (conteudo[:4] == b"PK\x03\x04"
                                             and not _eh_xlsx(conteudo))


def _eh_xlsx(conteudo):
    try:
        with zipfile.ZipFile(io.BytesIO(conteudo)) as z:
            return "[Content_Types].xml" in z.namelist()
    except zipfile.BadZipFile:
        return False


def _eh_html(conteudo):
    inicio = conteudo[:2048].lstrip().lower()
    return inicio.startswith((b"<!doctype html", b"<html", b"<table")) or b"<table" in inicio


def html_para_csv(conteudo):
    """Relatório de sistema salvo como .xls que na verdade é uma página HTML (ex.: MGF):
    a maior tabela da página, em CSV, para seguir pelo mesmo caminho dos outros."""
    try:
        texto = conteudo.decode("utf-8")
    except UnicodeDecodeError:
        # Alguns sistemas misturam UTF-8 com bytes soltos: o que não decodifica vira "?"
        texto = conteudo.decode("utf-8", errors="replace").replace("\ufffd", "?")
    tabelas = pd.read_html(io.StringIO(texto), header=None)
    if not tabelas:
        raise ValueError("página HTML sem tabela.")
    maior = max(tabelas, key=len)
    return maior.to_csv(sep=";", index=False, header=False).encode("utf-8")


def expandir(arquivos, leitura, origem="", nivel=0):
    """Abre os .zip (até 3 níveis) e devolve a lista plana de (nome, bytes)."""
    soltos = []
    for nome, conteudo in arquivos:
        caminho = f"{origem}{nome}"
        if _eh_zip(nome, conteudo):
            if nivel >= PROFUNDIDADE_ZIP:
                leitura.append({"Arquivo": caminho, "Banco/Conta": "—",
                                "Situação": "ignorado: zip dentro de zip demais"})
                continue
            try:
                internos = expandir_zip(conteudo)
            except ValueError as e:
                leitura.append({"Arquivo": caminho, "Banco/Conta": "—", "Situação": f"ignorado: {e}"})
                continue
            leitura.append({"Arquivo": caminho, "Banco/Conta": "—",
                            "Situação": f"{len(internos)} arquivo(s) dentro"})
            soltos += expandir(internos, leitura, f"{caminho} › ", nivel + 1)
        else:
            soltos.append((caminho, conteudo))
    return soltos


def _mascarar(numero):
    """Número de cartão vira final ****1234; conta corrente fica como está."""
    digitos = re.sub(r"\D", "", numero)
    return f"final {digitos[-4:]}" if len(digitos) >= 13 else numero


def conta_do_ofx(conteudo, conta):
    """Acrescenta agência e conta (ou final do cartão) ao nome do banco lido do OFX."""
    texto = conteudo.decode("latin-1")
    agencia = _tag_ofx(texto, "BRANCHID")
    numero = _tag_ofx(texto, "ACCTID")
    if not numero:
        return conta
    if "Cartão" in conta:
        return f"{conta} {_mascarar(numero)}"
    return f"{conta} Ag {agencia} Cc {numero}" if agencia else f"{conta} Cc {numero}"


def conta_do_arquivo(nome, conteudo, conta):
    """Completa o nome da conta com o número, quando o arquivo traz.

    OFX: agência e conta (cartão: final). Fatura em PDF: "Final 1234".
    Nubank: o número da conta vem no nome do arquivo (NU_12345678_...).
    """
    if b"<OFX>" in conteudo[:4000].upper() or nome.lower().endswith(".ofx"):
        return conta_do_ofx(conteudo, conta)
    if conteudo[:4] == b"%PDF" and "Cartão" in conta and "final" not in conta:
        from pypdf import PdfReader
        texto = PdfReader(io.BytesIO(conteudo)).pages[0].extract_text() or ""
        m = re.search(r"\bFinal\s+(\d{4})\b", texto, re.IGNORECASE)
        return f"{conta} final {m.group(1)}" if m else conta
    m = re.match(r"NU_(\d+)_", nome.rsplit(" › ", 1)[-1], re.IGNORECASE)
    if m and conta.startswith("Nubank"):
        return f"{conta} {m.group(1)}"
    return conta


_HISTORICO_ALTERNATIVO = ("descri", "histor", "memo", "titulo", "observ", "fornec",
                          "favorec", "cliente")
# Coluna que diz se é entrada ou saída (o valor vem sempre positivo)
_SENTIDO_ENTRADA = {"entrada", "receita", "credito", "c", "recebimento"}
_SENTIDO_SAIDA = {"saida", "despesa", "debito", "d", "pagamento"}
_CAMPOS = {"data": "Data", "valor": "Valor", "descricao": "Histórico"}


def colunas_em_duvida(df, colunas):
    """{campo: colunas} dos campos (data, valor, histórico) com mais de uma coluna possível.

    Vale o nome da coluna ("Data", "Data pagamento"); sem nenhum nome que case, o conteúdo
    (a maioria das linhas vira data ou número).
    """
    amostra = df.head(30)

    def pelo_nome(prefixos):
        return [c for c in df.columns if any(_casa(c, p) for p in prefixos)
                and not normalizar(c).startswith(("cnpj", "cpf"))]

    def parece_data(c):
        v = amostra[c].dropna()
        texto = v.astype(str)
        if v.empty or not (texto.str.contains(r"[/-]").mean() > 0.8
                           or all(hasattr(x, "year") for x in v)):
            return False
        return converter_datas(v).notna().mean() > 0.8

    def parece_valor(c):
        v = amostra[c].dropna()
        return not v.empty and v.map(converter_valor).notna().mean() > 0.8

    datas = pelo_nome(_NOMES_DATA) or [c for c in df.columns if parece_data(c)]
    # "Lançamento" + "Detalhes" (BB) se completam e são juntadas; já "Descrição" e
    # "Histórico" na mesma planilha são alternativas
    duvidas = {"data": datas, "descricao": pelo_nome(_HISTORICO_ALTERNATIVO)}
    if not colunas.get("credito"):   # crédito e débito em colunas separadas: não há "valor"
        duvidas["valor"] = pelo_nome(_NOMES_VALOR) or [
            c for c in df.columns if c not in datas and parece_valor(c)]
    return {k: v for k, v in duvidas.items() if len(v) > 1}


def coluna_de_sentido(df):
    """Coluna com ENTRADA/SAIDA (ou C/D, receita/despesa) na maioria das linhas (o resto
    costuma ser linha de total)."""
    for c in df.columns:
        v = df[c].dropna().astype(str).map(normalizar)
        if len(v) and v.isin(_SENTIDO_ENTRADA | _SENTIDO_SAIDA).mean() > 0.6 \
                and v.isin(_SENTIDO_SAIDA).any():
            return c
    return None


def _com_sinal(bruto, colunas):
    """Valor sempre positivo + coluna de sentido: saídas ficam negativas."""
    valor, sentido = colunas.get("valor"), coluna_de_sentido(bruto)
    if not valor or sentido is None:
        return bruto, None
    numeros = bruto[valor].map(converter_valor)
    tipo = bruto[sentido].astype(str).map(normalizar)
    lancamentos = tipo.isin(_SENTIDO_ENTRADA | _SENTIDO_SAIDA)   # fora as linhas de total
    if (numeros[lancamentos].dropna() < 0).any():
        return bruto, None
    saida = tipo.isin(_SENTIDO_SAIDA)
    bruto = bruto.copy()
    bruto[valor] = numeros.where(~saida, -numeros)
    return bruto, sentido


def importar(arquivos, colunas_por_layout=None):
    """Lê todos os arquivos e devolve (base, leitura, layouts).

    arquivos: lista de (nome, bytes), soltos ou .zip.
    colunas_por_layout: {tupla de colunas: mapeamento} para corrigir a identificação
        automática de colunas de algum CSV/Excel (o mapeamento é o de detectar_colunas).
    base: DataFrame com COLUNAS_BASE (+ Arquivo), em ordem de conta e data.
    leitura: o que aconteceu com cada arquivo, para mostrar ao usuário.
    layouts: {tupla de colunas: {"amostra", "colunas", "arquivos", "duvidas"}} dos CSV/Excel
        lidos. "duvidas" ({campo: colunas possíveis}) fica preenchido quando há mais de uma
        coluna possível para data, valor ou histórico e ninguém escolheu ainda: esses
        arquivos só entram na base depois da escolha.
    """
    colunas_por_layout = colunas_por_layout or {}
    leitura, tabelas, layouts = [], [], {}
    soltos = expandir(arquivos, leitura)
    soltos.sort(key=lambda a: ORDEM_FORMATO.get(a[0].lower().rsplit(".", 1)[-1], 3))

    grupos = {}
    for nome, conteudo in soltos:
        if not nome.lower().endswith(".pdf") and _eh_html(conteudo):
            try:
                conteudo = html_para_csv(conteudo)
            except ValueError as e:
                leitura.append({"Arquivo": nome, "Banco/Conta": "—", "Situação": f"ignorado: {e}"})
                continue
            nome = nome.rsplit(".", 1)[0] + ".csv"
        try:
            r = carregar_arquivo(nome.rsplit(" › ", 1)[-1], conteudo)
        except SemLancamentos as e:
            leitura.append({"Arquivo": nome, "Banco/Conta": e.conta, "Situação": str(e)})
            continue
        except Exception as e:
            leitura.append({"Arquivo": nome, "Banco/Conta": "—", "Situação": f"ignorado: {e}"})
            continue
        if r["tipo"] == "transacoes":
            t = r["dados"].copy()
            # Um PDF pode trazer mais de uma conta (conta corrente e aplicação)
            contas = {c: conta_do_arquivo(nome, conteudo, c) for c in t["Conta"].unique()}
            t["Conta"] = t["Conta"].map(contas) if contas else r["conta"]
            t["Arquivo"] = nome
            tabelas.append(t)
            situacao = ", ".join(f"{c}: {n}" for c, n in t["Conta"].value_counts(sort=False).items())
            leitura.append({"Arquivo": nome,
                            "Banco/Conta": " + ".join(t["Conta"].unique()) or r["conta"],
                            "Situação": f"{len(t)} lançamentos"
                                        + (f" ({situacao})" if len(contas) > 1 else "")})
        else:
            grupos.setdefault(tuple(r["bruto"].columns), []).append((nome, r))

    for cols, grupo in grupos.items():
        bruto = grupo[0][1]["bruto"]
        escolhido = colunas_por_layout.get(cols)
        colunas = escolhido or detectar_colunas(bruto)
        duvidas = {} if escolhido else colunas_em_duvida(bruto, colunas)
        layouts[cols] = {"amostra": bruto.head(5), "colunas": colunas,
                         "arquivos": [n for n, _ in grupo], "duvidas": duvidas}
        if duvidas:
            pergunta = "; ".join(f"{_CAMPOS[k]}: {' ou '.join(map(str, v))}"
                                 for k, v in duvidas.items())
            for nome, r in grupo:
                leitura.append({"Arquivo": nome, "Banco/Conta": r["conta"],
                                "Situação": f"escolha a coluna de {pergunta}"})
            continue
        for nome, r in grupo:
            bruto, sentido = _com_sinal(r["bruto"], colunas)
            try:
                t = montar_transacoes(bruto, colunas,
                                      conta_do_arquivo(nome, b"", r["conta"]))
            except ValueError as e:
                leitura.append({"Arquivo": nome, "Banco/Conta": r["conta"],
                                "Situação": f"colunas não identificadas: {e}"})
                continue
            t["Arquivo"] = nome
            tabelas.append(t)
            leitura.append({"Arquivo": nome, "Banco/Conta": conta_do_arquivo(nome, b"", r["conta"]),
                            "Situação": f"{len(t)} lançamentos"
                                        + (f" (entrada/saída pela coluna {sentido})"
                                           if sentido else "")})

    base = planilha_base(consolidar(tabelas))
    return base, pd.DataFrame(leitura, columns=["Arquivo", "Banco/Conta", "Situação"]), layouts


def planilha_base(t):
    """Só as colunas da planilha base, com Débito, Crédito e Histórico em branco."""
    if t.empty:
        return pd.DataFrame(columns=COLUNAS_BASE + ["Arquivo"])
    base = pd.DataFrame({
        "Banco/Conta": t["Conta"],
        "Data": t["Data"].dt.date,
        "Descrição": t["Descrição"],
        "Valor": t["Valor"].round(2),
        "Débito": "",
        "Crédito": "",
        "Histórico": "",
        "Arquivo": t["Arquivo"] if "Arquivo" in t else "",
    })
    return base.sort_values(["Banco/Conta", "Data"], kind="stable").reset_index(drop=True)


def para_excel(base):
    """Planilha base em .xlsx (valores como número, datas como data)."""
    saida = io.BytesIO()
    with pd.ExcelWriter(saida, engine="openpyxl") as w:
        base.to_excel(w, index=False, sheet_name="Base")
        aba = w.sheets["Base"]
        larguras = {"A": 28, "B": 12, "C": 60, "D": 14, "E": 12, "F": 12, "G": 60, "H": 40, "I": 45, "J": 45}
        for col, largura in larguras.items():
            aba.column_dimensions[col].width = largura
        for (celula,) in aba.iter_rows(min_row=2, min_col=2, max_col=2):
            celula.number_format = "DD/MM/YYYY"
        for (celula,) in aba.iter_rows(min_row=2, min_col=4, max_col=4):
            celula.number_format = "#,##0.00;-#,##0.00"
        aba.freeze_panes = "A2"
        aba.auto_filter.ref = aba.dimensions
    return saida.getvalue()
