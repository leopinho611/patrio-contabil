import io

import openpyxl

from razao import Razao, ler_razao, parece_razao


def _razao_ficticio():
    """Razão no leiaute do Domínio, com números e nomes inventados."""
    livro = openpyxl.Workbook()
    aba = livro.active
    linhas = [
        ["Empresa:", None, "EMPRESA FICTICIA"],
        ["RAZÃO"],
        ["Data", "Número", "Histórico", None, None, None, None, None, "Cta.C.Part.", "Débito",
         "Crédito", None, None, "Saldo-Exercício"],
        ["Conta:", "10", "1.1.01.002.001", None, None, "BANCO GAMA"],
        ["05/01/2025", "1", "VR. REF. SAÍDA DO BANCO - TARIFA DE LIQUIDAÇÃO", None, None, None,
         None, None, "360", None, "2.50"],
        ["06/01/2025", "2", "VR. REF. SAÍDA DO BANCO - TARIFA DE LIQUIDAÇÃO", None, None, None,
         None, None, "360", None, "2.50"],
        ["07/01/2025", "3", "VR. REF. PGTO OFICINA", None, None, None, None, None, "500", None,
         "100.00"],
        ["Conta:", "360", "4.1.02.001.001", None, None, "TARIFAS BANCARIAS"],
        ["Conta:", "500", "2.1.02.001.001", None, None, "OFICINA ZETA LTDA"],
        ["Conta:", "501", "1.1.03.001.001", None, None, "OFICINA ZETA LTDA"],
    ]
    for linha in linhas:
        aba.append(linha)
    saida = io.BytesIO()
    livro.save(saida)
    return saida.getvalue()


def test_le_o_razao_e_sugere_pelo_nome_e_pelo_historico():
    conteudo = _razao_ficticio()
    assert parece_razao("Razão.xlsx", conteudo)
    plano, lanc = ler_razao(conteudo)
    assert list(plano["Código"]) == ["10", "360", "500", "501"]
    assert len(lanc) == 3 and lanc["Crédito"].sum() == 105.0
    razao = Razao(plano, lanc)
    # Duas contas com o nome do fornecedor: a que foi usada nos pagamentos
    assert razao.sugerir("OFICINA ZETA - 12345678000199 - MANUTENÇÃO", False) == (
        "500", "nome no plano de contas")
    assert razao.sugerir("ALFA PAGAMENTOS - 98765432000155 - TARIFA DE LIQUIDAÇÃO", False) == (
        "360", "histórico do razão")
    assert razao.sugerir("TARIFA SEM PARECIDO", False) == (None, "")
    assert razao.nome("360") == "TARIFAS BANCARIAS"


def test_planilha_comum_nao_e_razao():
    livro = openpyxl.Workbook()
    livro.active.append(["Data", "Descrição", "Valor"])
    saida = io.BytesIO()
    livro.save(saida)
    assert not parece_razao("extrato.xlsx", saida.getvalue())
