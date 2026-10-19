"""
Leitores de extratos e faturas em PDF dos bancos (além da fatura BB e do Nubank,
que estão em financeiro.py).

Cada leitor recebe o texto do PDF e devolve a tabela padronizada de
montar_transacoes. Os extratos de conta usam o texto "em layout" do pypdf (as
colunas ficam na posição em que aparecem na página), porque é a posição que diz
se o valor é crédito, débito ou saldo.

Os PDFs que não têm lançamentos (extrato sem movimento, resumo de aplicação)
levantam SemLancamentos, para a tela dizer isso em vez de "não reconhecido".
"""

import io
import re

import pandas as pd
from pypdf import PdfReader

from financeiro import CATEGORIA_ESTORNO, converter_valor, montar_transacoes, normalizar

VALOR = r"-?[\d.]+,\d{2}-?"
_VALOR = re.compile(rf"(?<![\w.,]){VALOR}(?![\w,])")
_PARCELA = re.compile(r"\b(\d{2,3})/(\d{2,3})$")
_MESES = {"jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6, "jul": 7, "ago": 8,
          "set": 9, "out": 10, "nov": 11, "dez": 12}


class SemLancamentos(Exception):
    """O PDF foi reconhecido, mas não tem lançamentos (não é erro)."""

    def __init__(self, conta, motivo):
        super().__init__(motivo)
        self.conta = conta


def texto_em_layout(conteudo):
    paginas = PdfReader(io.BytesIO(conteudo)).pages
    return "\n".join(p.extract_text(extraction_mode="layout") or "" for p in paginas)


def _tabela(lancamentos, conta, cartao=False):
    if not lancamentos:
        raise SemLancamentos(conta, "nenhum lançamento no período")
    bruto = pd.DataFrame(lancamentos)
    bruto["Data"] = bruto["Data"].dt.strftime("%Y-%m-%d")
    t = montar_transacoes(bruto, {"data": "Data", "descricao": ["Descrição"], "valor": "Valor"},
                          conta)
    if cartao:  # no cartão, valor positivo é estorno, não receita
        t.loc[t["Valor"] > 0, "Categoria"] = CATEGORIA_ESTORNO
    return t


def _data(dia, mes, ano_ref, mes_ref):
    """dd/mm sem ano: o ano do extrato, ou o anterior se o mês passa do mês do extrato."""
    ano = ano_ref if int(mes) <= mes_ref else ano_ref - 1
    return pd.Timestamp(ano, int(mes), int(dia))


def _limpa(texto):
    return re.sub(r"\s+", " ", texto).strip()


# ============================================================================
# BANCO DO BRASIL - extrato de conta corrente (Consultas - Extrato de conta corrente)
# ============================================================================
_LINHA_BB = re.compile(r"^\s*(\d{2}/\d{2}/\d{4})\s+(?:\d{2}/\d{2}/\d{4}\s+)?\d{4}\s+\d{5}\s+\d{3}\s+"
                       r"(.+?)\s+([\d.]+,\d{2}) ([CD])(?:\s+[\d.]+,\d{2} [CD])?\s*$")


def eh_extrato_bb(texto):
    return "Extrato de conta corrente" in texto and "Dt. balancete" in texto


def ler_extrato_bb(texto):
    ag = re.search(r"Agência\s+(\S+)", texto)
    cc = re.search(r"Conta corrente\s+(\d[\d.]*-[\dXx])", texto)
    conta = f"BB Conta Ag {ag.group(1)} Cc {cc.group(1)}" if ag and cc else "BB Conta"
    lancamentos = []
    for linha in texto.splitlines():
        m = _LINHA_BB.match(linha)
        if m:
            data, historico, valor, dc = m.groups()
            historico = re.sub(r"\s+[\d.]+$", "", historico.strip())  # tira o documento
            lancamentos.append({"Data": pd.to_datetime(data, format="%d/%m/%Y"),
                                "Descrição": _limpa(historico),
                                "Valor": converter_valor(valor) * (1 if dc == "C" else -1)})
        elif lancamentos and linha.strip() and linha.startswith(" " * 20) \
                and not _VALOR.search(linha):
            # Linha de detalhe (favorecido/remetente) do lançamento de cima
            lancamentos[-1]["Descrição"] += " - " + _limpa(linha)
        elif "OBSERVAÇÕES" in linha:
            break
    return _tabela(lancamentos, conta)


# ============================================================================
# BRADESCO - extrato de conta (Net Empresa)
# ============================================================================
_DETALHE_BRADESCO = re.compile(r"^(REM[:.]|REMET\.|DEST\.|CONTR |FAV[:.])")


def eh_extrato_bradesco(texto):
    return "Extrato de: Ag:" in texto and "Lançamento" in texto and "Dcto." in texto


def _colunas(cabecalho, nomes):
    return [cabecalho.index(n) for n in nomes]


_LINHA_BRADESCO = re.compile(rf"^\s*(?:(\d{{2}}/\d{{2}}/\d{{4}})\s+)?(.*?)(?:\s+\d+\s+({VALOR})"
                             rf"(?:\s+{VALOR})?)?\s*$")


def ler_extrato_bradesco(texto):
    m = re.search(r"Ag:\s*(\d+)\s*\|\s*CC:\s*([\d-]+)", texto)
    conta = f"Bradesco Ag {m.group(1)} Cc {m.group(2)}" if m else "Bradesco"
    lancamentos, pendentes, data, no_extrato = [], [], None, False
    for linha in texto.splitlines():
        if "Lançamento" in linha and "Dcto." in linha and "Saldo" in linha:
            no_extrato = True
            continue
        if not no_extrato or not linha.strip() or "Folha " in linha:
            continue
        if linha.strip().startswith("Total"):
            break  # o que vem depois ("Últimos Lançamentos") já é do mês seguinte
        d, descricao, valor = _LINHA_BRADESCO.match(linha).groups()
        if d:
            data = pd.to_datetime(d, format="%d/%m/%Y")
        descricao = _limpa(descricao)
        if normalizar(descricao).startswith("saldo"):
            continue
        if not valor:
            pendentes.append(descricao)
            continue
        # Linhas sem valor antes desta: o detalhe do lançamento anterior e/ou o título
        # deste (quando a quebra de página separou título e valor)
        if pendentes and lancamentos and (len(pendentes) > 1 or not _DETALHE_BRADESCO.match(descricao)):
            lancamentos[-1]["Descrição"] += " - " + pendentes.pop(0)
        lancamentos.append({"Data": data, "Descrição": " - ".join(pendentes + [descricao]),
                            "Valor": converter_valor(valor)})
        pendentes = []
    if pendentes and lancamentos:
        lancamentos[-1]["Descrição"] += " - " + " - ".join(pendentes)
    return _tabela(lancamentos, conta)


# ============================================================================
# BRADESCO - fatura de cartão empresarial (Net Empresa, "Detalhe do Extrato")
# ============================================================================
_LINHA_CARTAO_BRADESCO = re.compile(r"^(\d{2})/(\d{2}) (.+?) (-?[\d.]+,\d{2}) (-?[\d.]+,\d{2})$")


def eh_fatura_bradesco(texto):
    return "Detalhe do Extrato" in texto and "Número do cartão" in texto and "Bradesco" in texto


def _vencimento(texto):
    m = (re.search(r"Data de vencimento:\s*(\d{2}/\d{2}/\d{4})", texto)
         or re.search(r"(\d{2}/\d{2}/\d{4})\s*Data de vencimento", texto)
         or re.search(r"vencimento:?\s*(\d{2}/\d{2}/\d{4})", texto))
    if not m:
        raise ValueError("fatura sem data de vencimento.")
    return pd.to_datetime(m.group(1), format="%d/%m/%Y")


def _compras_do_cartao(itens, venc):
    """Parcela entra na data de vencimento (a impressa é a da compra original)."""
    lancamentos = []
    for dia, mes, descricao, valor in itens:
        descricao = _limpa(descricao)
        if normalizar(descricao).startswith(("pagamento", "pgto")):
            continue  # pagamento da fatura anterior (já está na conta corrente)
        parcela = _PARCELA.search(descricao)
        data = venc if parcela else _data(dia, mes, venc.year, venc.month)
        lancamentos.append({"Data": data, "Descrição": descricao, "Valor": -valor})
    return lancamentos


def ler_fatura_bradesco(texto):
    venc = _vencimento(texto)
    final = re.search(r"XXXX\.XXXX\.XXXX\.(\d{4})", texto)
    bandeira = re.search(r"- (VISA|ELO|MASTERCARD|MASTER|AMEX)", texto)
    conta = "Bradesco Cartão"
    if bandeira:
        conta += f" {bandeira.group(1).title()}"
    if final:
        conta += f" final {final.group(1)}"
    itens = []
    for linha in texto.splitlines():
        m = _LINHA_CARTAO_BRADESCO.match(linha.strip())
        if m:
            dia, mes, descricao, _dolar, reais = m.groups()
            itens.append((dia, mes, descricao, converter_valor(reais)))
    return _tabela(_compras_do_cartao(itens, venc), conta, cartao=True)


# ============================================================================
# ITAÚ - extrato mensal (conta corrente e aplicações automáticas)
# ============================================================================
def eh_extrato_itau(texto):
    return "extrato" in texto and "Minha agência" in texto and "Minha conta" in texto


def ler_extrato_itau(texto):
    m = re.search(r"ag (\d+) cc ([\d-]+) (jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez) (\d{4})",
                  texto)
    if not m:
        raise ValueError("extrato Itaú sem agência, conta e mês.")
    conta = f"Itaú Ag {m.group(1)} Cc {m.group(2)}"
    mes_ref, ano_ref = _MESES[m.group(3)], int(m.group(4))
    lancamentos, data, x = [], None, None
    for linha in texto.splitlines():
        if "descrição" in linha and "entradas" in linha and "saídas" in linha:
            x = _colunas(linha, ["data", "descrição", "entradas"])
            continue
        if x is None:
            continue
        if "totalizador de aplicações" in linha or "Aplicações Automáticas" in linha:
            break
        d = re.search(r"(\d{2})/(\d{2})\s*$", linha[:x[1] - 1])
        if d and d.start() >= x[0] - 2:
            data = _data(d.group(1), d.group(2), ano_ref, mes_ref)
        resto = linha[x[1] - 1:]
        valores = list(_VALOR.finditer(resto))
        if not valores or data is None:
            continue
        # "PIX QRS FULANO02/01": separa a data do Pix do nome
        descricao = re.sub(r"(\S)(\d{2}/\d{2})$", r"\1 \2", _limpa(resto[:valores[0].start()]))
        if not descricao or normalizar(descricao).startswith("saldo"):
            continue
        # O primeiro valor é o lançamento (saída termina com "-"); o segundo, o saldo
        lancamentos.append({"Data": data, "Descrição": descricao,
                            "Valor": converter_valor(valores[0].group())})
    return _tabela(lancamentos, conta)


# ============================================================================
# ITAÚ - fatura do cartão empresarial
# ============================================================================
_LINHA_CARTAO_ITAU = re.compile(r"^(\d{2})/(\d{2}) (.+?) (-?R\$\s?[\d.]+,\d{2})$")


def eh_fatura_itau(texto):
    return "ITAU EMPRESAS" in texto.upper() and "fatura do cartão" in texto \
        and "Lançamentos nacionais" in texto


def ler_fatura_itau(texto):
    venc = _vencimento(texto)
    final = re.search(r"\d{4}\.XXXX\.XXXX\.(\d{4})", texto)
    conta = f"Itaú Cartão final {final.group(1)}" if final else "Itaú Cartão"
    inicio = texto.find("\nLançamentos\n")
    fim = texto.find("Encargos desta fatura", inicio)
    itens = []
    for linha in texto[inicio:fim if fim > 0 else None].splitlines():
        m = _LINHA_CARTAO_ITAU.match(linha.strip())
        if m:
            dia, mes, descricao, valor = m.groups()
            # Internacional: tira moeda local, dólar e cotação da descrição
            descricao = re.sub(r"(\s+(?:[A-Z]{3}|US\$|R\$)\s?[\d.,]+)+$", "", descricao)
            itens.append((dia, mes, descricao, converter_valor(valor)))
    return _tabela(_compras_do_cartao(itens, venc), conta, cartao=True)


# ============================================================================
# SANTANDER - extrato consolidado inteligente (PJ)
# ============================================================================
_LINHA_SANTANDER = re.compile(rf"^\s*(?:(\d{{2}})/(\d{{2}})\s+)?(.+?)\s+-?\s*({VALOR})"
                              rf"(?:\s+{VALOR})?\s*$")


def eh_extrato_santander(texto):
    return "EXTRATO CONSOLIDADO INTELIGENTE" in texto


def ler_extrato_santander(texto):
    m = re.search(r"(janeiro|fevereiro|março|abril|maio|junho|julho|agosto|setembro|outubro|"
                  r"novembro|dezembro)/(\d{4})", texto)
    if not m:
        raise ValueError("extrato Santander sem o mês.")
    mes_ref, ano_ref = _MESES[normalizar(m.group(1))[:3]], int(m.group(2))
    c = re.search(r"\b(\d{4})\s+(\d{2}\.\d{6}-\d)\b", texto)
    conta = f"Santander Ag {c.group(1)} Cc {c.group(2)}" if c else "Santander"
    inicio = texto.find("Movimentação\n")
    if inicio < 0:
        raise SemLancamentos(conta, "extrato sem movimentação")
    lancamentos, data = [], None
    for linha in texto[inicio + 13:].splitlines():
        if linha.startswith(("Extrato_PJ", "Saldos por")) or "Pagina:" in linha:
            break
        if not linha.strip():
            continue
        m = _LINHA_SANTANDER.match(linha)
        if not m:
            if lancamentos:
                lancamentos[-1]["Descrição"] += " " + _limpa(linha)
            continue
        if m.group(1):
            data = _data(m.group(1), m.group(2), ano_ref, mes_ref)
        descricao = _limpa(m.group(3)).rstrip(" -")
        if data is None or normalizar(descricao).startswith("saldo"):
            continue
        lancamentos.append({"Data": data, "Descrição": descricao,
                            "Valor": converter_valor(m.group(4))})
    return _tabela(lancamentos, conta)


# ============================================================================
# PDFs sem lançamentos
# ============================================================================
def sem_lancamentos(texto):
    """(conta, motivo) quando o PDF é reconhecido mas não tem o que importar."""
    if "NÃO CONSTA LANÇAMENTO" in texto.upper() and "CAIXA" in texto.upper():
        c = re.search(r"Agência:\s*(\d+)\s+Conta:\s*([\d-]+)", texto)
        conta = f"Caixa Ag {c.group(1)} Cc {c.group(2)}" if c else "Caixa"
        return conta, "extrato sem lançamentos no período"
    if re.search(r"Invest Fácil|INVEST FAC", texto) and "Valor Principal" in texto \
            and "Lançamento" not in texto:
        return "Bradesco Invest Fácil", ("resumo da aplicação, sem lançamentos (as aplicações e "
                                         "resgates já estão no extrato da conta)")
    return None


LEITORES = [
    (eh_extrato_bb, ler_extrato_bb, True),
    (eh_extrato_bradesco, ler_extrato_bradesco, True),
    (eh_fatura_bradesco, ler_fatura_bradesco, False),
    (eh_extrato_itau, ler_extrato_itau, True),
    (eh_fatura_itau, ler_fatura_itau, False),
    (eh_extrato_santander, ler_extrato_santander, True),
]


def ler_pdf_de_banco(texto, conteudo):
    """Tabela de transações do PDF, ou None se nenhum leitor daqui reconhece o layout.

    texto: o texto simples do pypdf (usado para reconhecer o banco).
    """
    vazio = sem_lancamentos(texto)
    if vazio:
        raise SemLancamentos(*vazio)
    for reconhece, ler, layout in LEITORES:
        if reconhece(texto):
            return ler(texto_em_layout(conteudo) if layout else texto)
    return None
