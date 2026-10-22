import io

import pandas as pd

from classificacao import (HIST_ENTRADA, HIST_SAIDA, bancos_na_matriz, classificar, codigos_na_matriz, ler_contas_bancos, ler_matriz,
                           linhas_novas_da_matriz, matriz_da_tabela, matriz_mais, matriz_para_excel, nome_para_matriz, tabela_da_matriz,
                           tabela_mais, parece_matriz, parece_planilha_base, procurar_na_matriz)

CC = "Itaú Ag 1234 Cc 55555-1"
APLIC = "Itaú Aplic Aut Mais Ag 1234 Cc 55555-1"
CARTAO = "Bradesco Cartão Visa final 0001"
CODIGOS = {CC: "100", APLIC: "101"}


def _matriz(linhas):
    saida = io.BytesIO()
    pd.DataFrame([("Descrição", "Conta Contábil")] + linhas).to_excel(saida, header=False, index=False)
    saida.seek(0)
    return ler_matriz(saida)


MATRIZ = _matriz([
    ("PADARIA AURORA - CAFE DA MANHA", "501"),
    ("Depósito Hotel (Banco: 999 Ag: 1 C/C: 2) - VIAGENS ALFA TURISMO", "14"),
    ("VIAGENS ALFA TURISMO - COMISSAO", "702"),
    ("CREDITO  - REDE", "14"),
    ("TARIFA BANCARIA", "360"),
    ("IOF -", "348"),
    ("IOF S OPERAÇÃO CREDITO", "9"),
    ("BANCO DO BRASIL - TARIFA", "360"),
    ("LOJA ANTIGA", "não importar"),
    ("MERCADO BOM PRECO", "610"),
    ("MERCADO BOM PRECO", "611"),
])


def _base(linhas):
    return pd.DataFrame(linhas, columns=["Banco/Conta", "Descrição", "Valor"]).assign(
        Data=pd.Timestamp("2026-01-05"), Débito="", Crédito="", Histórico="")


def test_despesa_debita_fornecedor_e_credita_banco():
    r = classificar(_base([(CC, "PIX ENVIADO PADARIA AURORA", -50.0)]), MATRIZ, CODIGOS).iloc[0]
    assert (r["Débito"], r["Crédito"]) == ("501", "100")
    assert r["Histórico"] == HIST_SAIDA + "PIX ENVIADO PADARIA AURORA"
    assert r["Conferir"] == ""


def test_receita_debita_banco_e_credita_cliente():
    r = classificar(_base([(CC, "TRANSFERENCIA PIX - REM: VIAGENS ALFA TURIS 05/01", 300.0)]),
                    MATRIZ, CODIGOS).iloc[0]
    # o mesmo nome também é fornecedor (702), mas na entrada vale a linha de depósito
    assert (r["Débito"], r["Crédito"]) == ("100", "14")
    assert r["Histórico"] == HIST_ENTRADA + "TRANSFERENCIA PIX - REM: VIAGENS ALFA TURIS 05/01"


def test_maquininha_e_tarifa_pelo_apelido():
    r = classificar(_base([(CC, "Rede VISA CD0000000001", 80.0), (CC, "TAR PIX QR", -2.0)]),
                    MATRIZ, CODIGOS)
    assert list(r["Crédito"]) == ["14", "100"]
    assert list(r["Débito"]) == ["100", "360"]


def test_nome_igual_ganha_de_nome_parecido():
    assert procurar_na_matriz("IOF", MATRIZ)[0] == "348"


def test_primeira_palavra_parecida_nao_basta():
    # BANCOALFA não é BANCO DO BRASIL
    assert procurar_na_matriz("TED - REMET.BANCOALFA TURISMO", MATRIZ)[0] is None


def test_aplicacao_usa_a_conta_de_aplicacao_e_espelho_nao_entra():
    r = classificar(_base([(CC, "Aplic Aut Mais", -1000.0), (APLIC, "Aplicação", 1000.0),
                           (APLIC, "Rendimento bruto", 5.0)]), MATRIZ, CODIGOS)
    assert (r.at[0, "Débito"], r.at[0, "Crédito"]) == ("101", "100")
    assert r.at[1, "Débito"] == r.at[1, "Crédito"] == ""
    assert r.at[1, "Conta na matriz"] == "não importar"
    assert r.at[2, "Débito"] == "101" and r.at[2, "Crédito"] == ""


def test_avisos_no_conferir():
    r = classificar(_base([(CC, "PIX ENVIADO FULANO DESCONHECIDO", -10.0),
                           (CARTAO, "PADARIA AURORA", -20.0),
                           (CC, "PIX ENVIADO LOJA ANTIGA", -5.0),
                           (CC, "BOLETO PAGO MERCADO BOM PRECO", -7.0),
                           (CC, "Sem lançamentos no período", 0.0)]), MATRIZ, CODIGOS)
    assert r.at[0, "Conferir"] == "não achei na matriz" and r.at[0, "Débito"] == ""
    assert r.at[1, "Débito"] == "501" and "falta o código desta conta" in r.at[1, "Conferir"]
    assert r.at[2, "Conferir"] == "matriz diz para não importar" and r.at[2, "Histórico"] == ""
    assert r.at[3, "Débito"] == "610" and "contas diferentes: 610 (1x), 611 (1x)" in r.at[3, "Conferir"]
    assert r.at[4, "Conferir"] == "sem movimento no período"


def test_ler_contas_bancos_csv(tmp_path):
    f = tmp_path / "contas.csv"
    f.write_text("Banco/Conta;Código\nItaú Ag 1234 Cc 55555-1;100\n", encoding="utf-8")
    assert ler_contas_bancos(f) == {CC: "100"}


def test_reconhece_a_matriz_pelo_nome_ou_pelo_cabecalho():
    saida = io.BytesIO()
    pd.DataFrame([("Descrição", "Conta Contábil"), ("PADARIA", "501")]).to_excel(
        saida, header=False, index=False)
    assert parece_matriz("planilha cliente.xlsx", saida.getvalue())
    assert parece_matriz("Matriz cliente.xlsx", b"")
    extrato = io.BytesIO()
    pd.DataFrame({"Data": ["05/01/2026"], "Histórico": ["PIX"], "Valor": [10]}).to_excel(
        extrato, index=False)
    assert not parece_matriz("extrato.xlsx", extrato.getvalue())
    assert not parece_matriz("matriz.pdf", b"")


def test_reconhece_a_planilha_base_baixada_do_app():
    saida = io.BytesIO()
    pd.DataFrame(columns=["Banco/Conta", "Data", "Descrição", "Valor", "Débito", "Crédito",
                          "Histórico", "Arquivo"]).to_excel(saida, index=False)
    assert parece_planilha_base("planilha_base.xlsx", saida.getvalue())
    assert not parece_matriz("planilha_base.xlsx", saida.getvalue())


def test_codigos_das_contas_numa_aba_da_matriz():
    saida = io.BytesIO()
    with pd.ExcelWriter(saida) as w:
        pd.DataFrame([("Descrição", "Conta Contábil"), ("PADARIA", "501")]).to_excel(
            w, sheet_name="Planilha1", header=False, index=False)
        pd.DataFrame([("", "", ""), ("", "Banco/Conta", "Código"), ("", CC, 100),
                      ("", CARTAO, None)]).to_excel(w, sheet_name="Planilha2", header=False,
                                                    index=False)
    saida.seek(0)
    assert codigos_na_matriz(saida) == {CC: "100"}
    saida.seek(0)
    assert len(ler_matriz(saida)) == 1


def test_historico_com_varias_partes_procura_o_fornecedor_em_cada_uma():
    # Planilha do cliente: CATEGORIA - FORNECEDOR - CNPJ
    assert procurar_na_matriz("MATERIAL DE USO - PADARIA AURORA - 12345678000199", MATRIZ)[0] == "501"
    # Parte que só começa igual não vale ("TRANSF CC PARA CC PJ" não é "TRANSF ENTRE CONTAS")
    m = _matriz([("Transf Entre Contas - BANCO ALFA", "1482")])
    assert procurar_na_matriz("TRANSF CC PARA CC PJ - FULANO DE TAL", m)[0] is None


def test_matriz_com_varias_tabelas_lado_a_lado():
    saida = io.BytesIO()
    pd.DataFrame([
        ("DESCRIÇÃO/PARTICIPANTE", "CONTA CONTÁBIL", "", "BANCO", "CONTA", "",
         "DESCRIÇÃO DA RECEITA", "CONTA CONTÁBIL", "", "DESCRIÇÃO DA DESPESA", "CONTA CONTÁBIL"),
        ("PADARIA AURORA - 12345678000199", "501", "", "BANCO ALFA", "940", "",
         "TAXA DE ADESAO", "557", "", "SERVIÃ‡O DE LIMPEZA", "964"),
        ("", "", "", "BANCO BETA", "846", "", "", "", "", "COMBUSTIVEL", "328"),
    ]).to_excel(saida, header=False, index=False)
    saida.seek(0)
    m = ler_matriz(saida)
    assert dict(zip(m["Descrição"], m["Conta"])) == {
        "PADARIA AURORA - 12345678000199": "501", "TAXA DE ADESAO": "557",
        "SERVIÇO DE LIMPEZA": "964", "COMBUSTIVEL": "328"}
    assert list(m["Recebimento"]) == [None, True, False, False]
    saida.seek(0)
    assert bancos_na_matriz(saida) == {"BANCO ALFA": "940", "BANCO BETA": "846"}
    r = classificar(_base([(CC, "COMBUSTIVEL - POSTO X - 999", -50.0),
                           (CC, "TAXA DE ADESAO - CLIENTE Y", 30.0)]), m, CODIGOS)
    assert list(r["Débito"]) == ["328", "100"] and list(r["Crédito"]) == ["100", "557"]


def _matriz_varias_tabelas():
    saida = io.BytesIO()
    pd.DataFrame([
        ("PARTICIPANTE", "CONTA", "", "BANCO", "CONTA", "", "DESCRIÇÃO DA RECEITA", "CONTA", "",
         "DESCRIÇÃO DA DESPESA", "CONTA"),
        ("PADARIA AURORA", "501", "", "BANCO ALFA", "940", "", "TAXA DE ADESAO", "557", "",
         "COMBUSTIVEL", "328"),
        ("", "", "", "BANCO BETA", "846", "", "", "", "", "ALUGUEL", "330"),
    ]).to_excel(saida, header=False, index=False)
    return saida.getvalue()


def test_nome_para_matriz_pega_a_parte_com_o_nome():
    assert nome_para_matriz("GRAFICA BETA - 12345678000199 - TARIFA PIX") == "TARIFA PIX"
    assert nome_para_matriz("PIX ENVIADO FULANO DE TAL 12/04") == "FULANO DE TAL"
    assert nome_para_matriz("CLIENTE ALFA - 12345678000199") == "CLIENTE ALFA"


def test_correcoes_da_tabela_viram_linhas_novas_da_matriz():
    conteudo = _matriz_varias_tabelas()
    m = ler_matriz(io.BytesIO(conteudo))
    classificada = classificar(_base([(CC, "BANCO GAMA - TARIFA PIX", -1.5),
                                      (CC, "CLIENTE ALFA - MENSALIDADE", 90.0),
                                      (CC, "POSTO X - COMBUSTIVEL", -50.0)]), m, CODIGOS)
    corrigida = classificada.copy()
    corrigida.loc[0, "Débito"] = "360"      # tarifa: não estava na matriz
    corrigida.loc[1, "Crédito"] = "557"     # mensalidade: idem
    novas = linhas_novas_da_matriz(classificada, corrigida, m)
    assert novas.to_dict("records") == [
        {"Descrição": "TARIFA PIX", "Conta": "360", "Entrada": False},
        {"Descrição": "MENSALIDADE", "Conta": "557", "Entrada": True}]

    gravada = matriz_para_excel(tabela_mais(tabela_da_matriz(io.BytesIO(conteudo)), novas), conteudo)
    nova = ler_matriz(io.BytesIO(gravada))
    assert dict(zip(nova["Descrição"], zip(nova["Conta"], nova["Recebimento"]))) == {
        "PADARIA AURORA": ("501", None), "TAXA DE ADESAO": ("557", True),
        "MENSALIDADE": ("557", True), "COMBUSTIVEL": ("328", False),
        "ALUGUEL": ("330", False), "TARIFA PIX": ("360", False)}
    assert bancos_na_matriz(io.BytesIO(gravada)) == {
        "BANCO ALFA": "940", "BANCO BETA": "846"}
    # Já na matriz: não repete
    assert linhas_novas_da_matriz(classificada, corrigida, nova).empty

    # E já vale para os outros lançamentos iguais, antes de gravar a matriz
    outra = classificar(_base([(CC, "BANCO DELTA - TARIFA PIX", -2.0)]), matriz_mais(m, novas), CODIGOS)
    assert outra["Débito"].iat[0] == "360"


def test_matriz_editada_no_app_vale_para_a_classificacao():
    tabela = tabela_da_matriz(io.BytesIO(_matriz_varias_tabelas()))
    assert list(tabela["Tabela"]) == ["Participante", "Banco", "Banco", "Receita", "Despesa", "Despesa"]
    tabela.loc[tabela["Descrição"] == "COMBUSTIVEL", "Conta"] = "329"
    m, bancos = matriz_da_tabela(tabela)
    assert bancos == {"BANCO ALFA": "940", "BANCO BETA": "846"}
    r = classificar(_base([(CC, "POSTO X - COMBUSTIVEL", -50.0)]), m, CODIGOS)
    assert r["Débito"].iat[0] == "329"
