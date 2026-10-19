import pandas as pd
import pytest

from leitores_pdf import (
    SemLancamentos,
    eh_extrato_bb,
    eh_extrato_bradesco,
    eh_extrato_itau,
    eh_extrato_santander,
    eh_fatura_bradesco,
    eh_fatura_itau,
    ler_extrato_bb,
    ler_extrato_bradesco,
    ler_extrato_itau,
    ler_extrato_santander,
    ler_fatura_bradesco,
    ler_fatura_itau,
    ler_pdf_de_banco,
    eh_resumo_invest_facil,
    ler_resumo_invest_facil,
)

# Textos no formato que o pypdf extrai (os extratos de conta em modo layout).
# Nomes e valores inventados.

BB = """\
                          Consultas - Extrato de conta corrente
Agência                   1234-5
Conta corrente            98765-1EMPRESA TESTE LTDA
 Dt. balancete    Dt. movimento    Ag. origem    Lote   Histórico                                     Documento         Valor R$     Saldo
  23/12/2025                           0000      00000  000 Saldo Anterior                                                        100,00 C
  12/01/2026                           0000      13113  435 Tarifa Pacote de Serviços        870.121.102.876.429         10,00 D   90,00 C
                                                        Cobrança referente 12/01/2026
  23/01/2026                           0000      14175  976 TED-Crédito em Conta                      100.131.618    1.000,00 C
                                                        341 1111 11222333000144 CLIENTE A
  30/01/2026                           0000      13105  375 Impostos                                        13.001      200,00 D
                                                        PREF MUN TESTE
  30/01/2026                           0000      13105  375 Impostos                                        13.002      200,00 D  690,00 C
                                                        PREF MUN TESTE
  31/01/2026                           0000      00000  999 S A L D O                                                            690,00 C
 ------------------------------------------------
                         OBSERVAÇÕES:
"""

BRADESCO = """\
Extrato de: Ag: 3333 | CC: 0123456-4 | Entre 01/01/2026 e 31/01/2026
 Data             Lançamento                                       Dcto.               Crédito (R$)              Débito (R$)                Saldo (R$)
30/12/2025       SALDO ANTERIOR                                                                                                                     1,00
02/01/2026       TRANSF CC PARA CC PJ                             3326199                    4.569,94                                           4.570,94
                 CLIENTE B LTDA
                 APLIC.INVEST FACIL                               7659039                                             -4.569,94                     1,00
                 TRANSFERENCIA PIX
                                                                                                                                                  Folha 2/4
                  REM: FULANO DE TAL 12/01                       1857247                       80,00                                              81,00
                  TED D CC HBANK*                                 2572973                                               -80,00                     1,00
                  DEST. EMPRESA TESTE
Total                                                                                      4.649,94               -4.649,94                       1,00
Últimos Lançamentos
 02/02/2026        TED-TRANSF ELET DISPON                           8238947                    1.054,37                                            1.055,37
"""

CARTAO_BRADESCO = """\
Empresa: EMPRESA TESTE LTDA | CNPJ: 00.000.000/0001-00
Nome: FULANO - ELO
Número do cartão: XXXX.XXXX.XXXX.1001
Data de vencimento: 10/01/2026
Detalhe do Extrato
Data Histórico US$ R$
10/02 LOJA A 011/012 0,00 322,41
27/11 LOJA B 0,00 1.671,20
05/01 SOFTWARE X 49,00 280,28
Total: 49,00 2.273,89
Banco Bradesco S/A
"""

ITAU = """\
                                                                                                 extrato mensal         ag 1111 cc 22222-2 jan 2026 001|018
   01. Conta Corrente e Aplicações Automáticas                                       R$ 5.366,62
   A = agendamento                    data                  descrição                                               entradas R$               saídas R$                          saldo  R$
       pela  Bolsa de Valores         31/12                 Saldo anterior                                                                                                     1,00
   D = débito a compensar             02/01                 Sispag Fornecedores                                                                 6.300,00-
                                                            PIX QRS FULANO SIL02/01                                     6.301,00
   Explicativas nofinal doextrato                           Apl Aplic Aut Mais                                                                       1,00-                     1,00
                                                            SALDO APLIC AUT MAIS                                                                                         6.924,15
data                  descrição                                                 entradas R$(créditos)     saídas R$(débitos)                   saldo  R$
05/01                 Parcela Giro    04/57                                                                 9.706,75-
                      Res Aplic Aut Mais                                            9.706,70
                      Rend Pago Aplic Aut Mais                                           0,05                                                     1,00
                                                          totalizador de aplicações automáticas                      entrada R$                  saída R$
05/01                 Não é lançamento                                                  1,00
"""

CARTAO_ITAU = """\
ITAU EMPRESAS MASTERCARD - 0005.XXXX.XXXX.2002
fatura do cartão:
Fechada
vencimento:
25/01/2026
Saldo da fatura anterior
PAGAMENTO EFETUADO 25/12 -R$104,70
Lançamentos
FULANO - FINAL 3003
Lançamentos nacionais
data descrição valor
01/01 IOF COMPRA INTERNACIONA R$3,69
Lançamentos internacionais
31/12 SERVIDOR.COM USD17,90 US$17,90 R$5,85 R$104,72
Encargos desta fatura
"""

SANTANDER = """\
EXTRATO CONSOLIDADO INTELIGENTE
janeiro/2026
5555                           13.000000-1
Movimentação
12/01 DEBITO AUT. FAT.CARTAO MASTER CARD                                                     -                             1,00-
          FINAL 4004
        RESGATE CONTAMAX AUTOMATICO  -                                                                               1,00                                        0,00
Extrato_PJ_A4_Inteligente 1.0
"""


def test_extrato_bb():
    assert eh_extrato_bb(BB)
    t = ler_extrato_bb(BB)
    assert t["Conta"].iat[0] == "BB Conta Ag 1234-5 Cc 98765-1"
    assert len(t) == 4
    assert t["Valor"].sum() == pytest.approx(590.00)  # 100,00 -> 690,00
    assert t["Descrição"].iat[1] == "TED-Crédito em Conta - 341 1111 11222333000144 CLIENTE A"
    assert t["Descrição"].iat[0].startswith("Tarifa Pacote de Serviços - Cobrança")


def test_extrato_bradesco_para_no_total_e_junta_quebra_de_pagina():
    assert eh_extrato_bradesco(BRADESCO)
    t = ler_extrato_bradesco(BRADESCO)
    assert t["Conta"].iat[0] == "Bradesco Ag 3333 Cc 0123456-4"
    assert list(t["Descrição"]) == [
        "TRANSF CC PARA CC PJ - CLIENTE B LTDA",
        "APLIC.INVEST FACIL",
        "TRANSFERENCIA PIX - REM: FULANO DE TAL 12/01",
        "TED D CC HBANK* - DEST. EMPRESA TESTE",
    ]
    assert list(t["Valor"]) == [4569.94, -4569.94, 80.00, -80.00]
    assert (t["Data"] == pd.Timestamp("2026-01-02")).all()  # fevereiro fica de fora


def test_fatura_bradesco_parcela_na_data_de_vencimento():
    assert eh_fatura_bradesco(CARTAO_BRADESCO + "Bradesco")
    t = ler_fatura_bradesco(CARTAO_BRADESCO)
    assert t["Conta"].iat[0] == "Bradesco Cartão Elo final 1001"
    assert t["Valor"].sum() == pytest.approx(-2273.89)
    datas = dict(zip(t["Descrição"], t["Data"]))
    assert datas["LOJA A 011/012"] == pd.Timestamp("2026-01-10")
    assert datas["LOJA B"] == pd.Timestamp("2025-11-27")
    assert datas["SOFTWARE X"] == pd.Timestamp("2026-01-05")


def test_extrato_itau_usa_o_primeiro_valor_e_ignora_saldos():
    assert eh_extrato_itau(ITAU + "Minha agência Minha conta")
    t = ler_extrato_itau(ITAU)
    assert t["Conta"].iat[0] == "Itaú Ag 1111 Cc 22222-2"
    assert list(t["Valor"]) == [-6300.00, 6301.00, -1.00, -9706.75, 9706.70, 0.05]
    assert "PIX QRS FULANO SIL 02/01" in list(t["Descrição"])
    assert "Parcela Giro 04/57" in list(t["Descrição"])
    assert t["Data"].iat[-1] == pd.Timestamp("2026-01-05")


def test_fatura_itau_ignora_pagamento_e_tira_cotacao():
    assert eh_fatura_itau(CARTAO_ITAU)
    t = ler_fatura_itau(CARTAO_ITAU)
    assert t["Conta"].iat[0] == "Itaú Cartão final 2002"
    assert list(t["Descrição"]) == ["SERVIDOR.COM", "IOF COMPRA INTERNACIONA"]
    assert t["Valor"].sum() == pytest.approx(-108.41)
    assert t["Data"].iat[0] == pd.Timestamp("2025-12-31")


def test_extrato_santander():
    assert eh_extrato_santander(SANTANDER)
    t = ler_extrato_santander(SANTANDER)
    assert t["Conta"].iat[0] == "Santander Ag 5555 Cc 13.000000-1"
    assert list(t["Descrição"]) == ["DEBITO AUT. FAT.CARTAO MASTER CARD FINAL 4004",
                                    "RESGATE CONTAMAX AUTOMATICO"]
    assert list(t["Valor"]) == [-1.0, 1.0]


def test_cdb_contamax_do_santander():
    texto = SANTANDER + (" 12/JAN/2626/NOV/25 100000000000    0,00      1,00       1,00          "
                         "0,00        0,00    0,00      1,00\n")
    t = ler_extrato_santander(texto)
    cdb = t[t["Conta"] == "Santander CDB ContaMax Ag 5555 Cc 13.000000-1"]
    assert list(cdb["Descrição"]) == ["Resgate"]
    assert list(cdb["Valor"]) == [-1.0]
    assert cdb["Data"].iat[0] == pd.Timestamp("2026-01-12")


def test_caixa_sem_movimento_entra_com_valor_zero():
    texto = ("EMPRESA TESTE\nAgência: 09999 Conta: 000111111111-1\n"
             "Saldo anterior ao período solicitado R$ 350,95 C\n"
             "Extrato no período de 01/01/2026 à 31/01/2026\n"
             "NÃO CONSTA LANÇAMENTO NESTE PERIODO\nSAC CAIXA\n")
    t = ler_pdf_de_banco(texto, b"")
    assert t["Conta"].iat[0] == "Caixa Ag 09999 Cc 000111111111-1"
    assert t["Data"].iat[0] == pd.Timestamp("2026-01-31")
    assert t["Valor"].iat[0] == 0
    assert "350,95 C" in t["Descrição"].iat[0]


INVEST_FACIL = """\
                   Conta:     3333 | 444444 - 4
              Período de:     01/01/2026 até 31/01/2026
 Modalidade                    Total Aplicado (R$)              Valor Principal (R$)              Resgate Bruto (R$)                Resgate Líquido (R$)
 INVEST FAC                               111.027,89                       104.124,39                        104.130,45                          104.125,35
      Renda Bruta (R$):      6,06                                IOF*:    4,92                             IR (R$):    0,18
"""


def test_resumo_invest_facil_vira_conta_de_aplicacao():
    assert eh_resumo_invest_facil("Investimentos Bradesco\nTotal Aplicado (R$)")
    t = ler_resumo_invest_facil(INVEST_FACIL)
    assert t["Conta"].iat[0] == "Bradesco Invest Fácil Ag 3333 Cc 444444-4"
    assert dict(zip(t["Descrição"], t["Valor"])) == {
        "Aplicação": 111027.89, "Rendimento bruto": 6.06, "IOF retido": -4.92,
        "IR retido": -0.18, "Resgate": -104125.35}
    assert t["Valor"].sum() == pytest.approx(7385.57 - 482.07)  # saldo de principal


def test_saldos_mensais_do_invest_facil_nao_e_erro():
    texto = "Modalidade: Invest Fácil - Em CCDI\nMês Valor Principal (R$)\nJaneiro 7.385,57\n"
    with pytest.raises(SemLancamentos):
        ler_pdf_de_banco(texto, b"")


def test_aplicacoes_automaticas_do_itau():
    texto = ITAU.replace("05/01                 Não é lançamento", "") + """
movimentação - aplicações/resgates antecipados e vencimentos
                          aplicações            valor principal                rendimento                       IOF                 IRRF              rendimento          saldo de principal
data                   realizadas R$           resgat./venc. R$             bruto pago R$               retido R$             retido R$          líquido pago R$                  aplicado R$
02/01                        1.558,53                         0,00                       0,00                  0,00                  0,00                       0,00                  6.924,15
05/01                             0,00                   5.365,62                        0,17                  0,13                  0,00                       0,04                  1.558,53
total                        1.558,53                   5.365,62                        0,17                  0,13                  0,00                       0,04
"""
    t = ler_extrato_itau(texto)
    aplic = t[t["Conta"] == "Itaú Aplic Aut Mais Ag 1111 Cc 22222-2"]
    assert list(aplic["Descrição"]) == ["Aplicação", "Rendimento bruto", "IOF retido", "Resgate"]
    assert list(aplic["Valor"]) == [1558.53, 0.17, -0.13, -5365.66]
