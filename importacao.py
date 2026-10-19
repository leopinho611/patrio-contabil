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
    _tag_ofx,
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


def importar(arquivos, colunas_por_layout=None):
    """Lê todos os arquivos e devolve (base, leitura, layouts).

    arquivos: lista de (nome, bytes), soltos ou .zip.
    colunas_por_layout: {tupla de colunas: mapeamento} para corrigir a identificação
        automática de colunas de algum CSV/Excel (o mapeamento é o de detectar_colunas).
    base: DataFrame com COLUNAS_BASE (+ Arquivo), em ordem de conta e data.
    leitura: o que aconteceu com cada arquivo, para mostrar ao usuário.
    layouts: {tupla de colunas: {"amostra", "colunas", "arquivos"}} dos CSV/Excel lidos.
    """
    colunas_por_layout = colunas_por_layout or {}
    leitura, tabelas, layouts = [], [], {}
    soltos = expandir(arquivos, leitura)
    soltos.sort(key=lambda a: ORDEM_FORMATO.get(a[0].lower().rsplit(".", 1)[-1], 3))

    grupos = {}
    for nome, conteudo in soltos:
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
        colunas = colunas_por_layout.get(cols) or detectar_colunas(grupo[0][1]["bruto"])
        layouts[cols] = {"amostra": grupo[0][1]["bruto"].head(5), "colunas": colunas,
                         "arquivos": [n for n, _ in grupo]}
        for nome, r in grupo:
            try:
                t = montar_transacoes(r["bruto"], colunas,
                                      conta_do_arquivo(nome, b"", r["conta"]))
            except ValueError as e:
                leitura.append({"Arquivo": nome, "Banco/Conta": r["conta"],
                                "Situação": f"colunas não identificadas: {e}"})
                continue
            t["Arquivo"] = nome
            tabelas.append(t)
            leitura.append({"Arquivo": nome, "Banco/Conta": conta_do_arquivo(nome, b"", r["conta"]),
                            "Situação": f"{len(t)} lançamentos"})

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
