import io
import zipfile

import openpyxl

from importacao import COLUNAS_BASE, importar, para_excel

OFX_CONTA = b"""OFXHEADER:100
<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><BANKACCTFROM><BANKID>0341<BRANCHID>1234<ACCTID>56789-0
</BANKACCTFROM><BANKTRANLIST>
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20260105<TRNAMT>-150.00<MEMO>PAGTO BOLETO ENERGIA</STMTTRN>
<STMTTRN><TRNTYPE>CREDIT<DTPOSTED>20260110<TRNAMT>3000.00<MEMO>PIX RECEBIDO CLIENTE X</STMTTRN>
</BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>"""

OFX_CARTAO = b"""OFXHEADER:100
<OFX><CREDITCARDMSGSRSV1><CCSTMTTRNRS><CCSTMTRS><CCACCTFROM><ACCTID>5502123412349876
</CCACCTFROM><BANKTRANLIST>
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20260203<TRNAMT>-89.90<MEMO>POSTO SHELL</STMTTRN>
</BANKTRANLIST></CCSTMTRS></CCSTMTTRNRS></CREDITCARDMSGSRSV1></OFX>""".replace(b"<OFX>", b"<OFX><SONRS><FI><FID>237</FI></SONRS>", 1)

CSV_BANCO = "Data;Histórico;Valor\n02/01/2026;TARIFA PACOTE;-45,00\n15/01/2026;TED RECEBIDA;1.200,50\n".encode("latin-1")


def _zip(arquivos):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for nome, dados in arquivos:
            z.writestr(nome, dados)
    return b.getvalue()


def test_zip_dentro_de_zip_com_dois_bancos_e_cartao():
    interno = _zip([("cartao_2026.ofx", OFX_CARTAO)])
    pacote = _zip([("itau_2026.ofx", OFX_CONTA), ("santander_2026.csv", CSV_BANCO),
                   ("cartoes.zip", interno), ("__MACOSX/._lixo", b"x")])
    base, leitura, _ = importar([("extratos.zip", pacote)])

    assert list(base.columns[:7]) == COLUNAS_BASE
    assert len(base) == 5
    contas = set(base["Banco/Conta"])
    assert "Itaú Ag 1234 Cc 56789-0" in contas
    assert "Bradesco Cartão final 9876" in contas
    assert "santander" in contas
    assert (base[["Débito", "Crédito", "Histórico"]] == "").all().all()
    assert base.loc[base["Descrição"] == "TED RECEBIDA", "Valor"].iat[0] == 1200.50
    assert not leitura["Situação"].str.startswith("ignorado").any()


def test_mesmo_extrato_enviado_duas_vezes_nao_duplica():
    base, _, _ = importar([("itau.ofx", OFX_CONTA), ("itau_copia.ofx", OFX_CONTA)])
    assert len(base) == 2


def test_arquivo_desconhecido_vira_aviso():
    base, leitura, _ = importar([("foto.pdf", b"%PDF-1.4 nada")])
    assert base.empty
    assert leitura["Situação"].iat[0].startswith("ignorado")


def test_excel_tem_data_e_numero():
    base, _, _ = importar([("itau.ofx", OFX_CONTA)])
    aba = openpyxl.load_workbook(io.BytesIO(para_excel(base)))["Base"]
    assert [c.value for c in aba[1]][:7] == COLUNAS_BASE
    assert aba["D2"].value == -150.0
    assert aba["B2"].number_format == "DD/MM/YYYY"


def test_iof_sobre_saldo_devedor_nao_e_linha_de_saldo():
    excel = ("Data;Lançamento;Detalhes;Valor\n20/08/2026;Saldo Anterior;;0,00\n"
             "01/09/2026;Cobrança de I.O.F.;IOF Saldo Devedor Conta;-0,18\n"
             "01/09/2026;Saldo do dia;;0,00\n30/09/2026;S A L D O;;0,00\n").encode()
    base, _, _ = importar([("extrato.csv", excel)])
    assert list(base["Valor"]) == [-0.18]


def test_numero_da_conta_nubank_vem_do_nome_do_arquivo():
    csv = b"Data,Valor,Identificador,Descri\xc3\xa7\xc3\xa3o\n02/09/2026,-10.00,x,Pix\n"
    base, _, _ = importar([("NU_31098633_01SET2026_30SET2026.csv", csv)])
    assert base["Banco/Conta"].iat[0] == "Nubank 31098633"


PLANILHA_CLIENTE = ("Data Emissão;Data Pagamento;Fornecedor;Histórico;Descrição;Valor Bruto;Valor Pago\n"
                    "02/01/2026;05/01/2026;Padaria Aurora;NF 10;Café da manhã;100,00;98,00\n"
                    "03/01/2026;06/01/2026;Gráfica Beta;NF 11;Cardápios;50,00;50,00\n").encode()


def test_planilha_do_cliente_com_colunas_em_duvida_espera_a_escolha():
    base, leitura, layouts = importar([("financeiro cliente.csv", PLANILHA_CLIENTE)])
    assert base.empty
    (cols, info), = layouts.items()
    # Histórico não é perguntado: vai a sugestão (fornecedor + descrição)
    assert info["duvidas"] == {"data": ["Data Emissão", "Data Pagamento"],
                               "valor": ["Valor Bruto", "Valor Pago"]}
    assert info["colunas"]["descricao"] == ["Fornecedor", "Descrição"]
    assert "escolha a coluna de" in leitura["Situação"].iat[0]

    escolha = {"data": "Data Pagamento", "descricao": ["Fornecedor", "Descrição"],
               "valor": "Valor Pago", "credito": None, "debito": None}
    base, _, layouts = importar([("financeiro cliente.csv", PLANILHA_CLIENTE)], {cols: escolha})
    assert layouts[cols]["duvidas"] == {}
    assert list(base["Valor"]) == [98.0, 50.0]
    assert str(base["Data"].iat[0])[:10] == "2026-01-05"
    assert "Padaria Aurora" in base["Descrição"].iat[0]


def test_sem_duvida_le_direto():
    csv = b"Data;Historico;Valor\n05/01/2026;Pix recebido;10,00\n"
    base, _, layouts = importar([("cliente.csv", csv)])
    assert len(base) == 1 and not next(iter(layouts.values()))["duvidas"]


RELATORIO_HTML = """
<!DOCTYPE html><html><head><meta charset="utf-8"/><title>Relatório</title></head><body>
<table><tr><td>Operação</td><td>Descrição</td><td>Valor Pagamento</td><td>Data Pagamento</td>
<td>CNPJ Fornecedor</td><td>Fornecedor</td><td>Observação</td></tr>
<tr><td>ENTRADA</td><td>RECEBIMENTOS</td><td>R$ 500,00</td><td>06/04/2026</td><td>1</td>
<td>CLIENTE ALFA</td><td>PARCELA 1</td></tr>
<tr><td>SAIDA</td><td>DESPESAS</td><td>R$ 1.200,50</td><td>07/04/2026</td><td>2</td>
<td>PADARIA AURORA</td><td>CAFE</td></tr>
<tr><td>Total recebido - total pago:</td><td>R$ -700,50</td><td>R$ -700,50</td><td>R$ -700,50</td>
<td></td><td></td><td></td></tr></table></body></html>""".encode()


def test_relatorio_html_salvo_como_xls_com_coluna_de_entrada_e_saida():
    base, leitura, layouts = importar([("relatorio.xls", RELATORIO_HTML)])
    (cols, info), = layouts.items()
    assert info["duvidas"] == {}
    assert info["colunas"]["descricao"] == ["Fornecedor", "Descrição"]
    assert base["Descrição"].iat[1] == "PADARIA AURORA - DESPESAS"
    escolha = dict(info["colunas"], descricao=["Fornecedor", "Observação"])
    base, leitura, _ = importar([("relatorio.xls", RELATORIO_HTML)], {cols: escolha})
    assert list(base["Valor"]) == [500.0, -1200.5]
    assert base["Descrição"].iat[1] == "PADARIA AURORA - CAFE"
    assert "entrada/saída pela coluna Operação" in leitura["Situação"].iat[0]


RELATORIO_COM_BANCO = ("Operação;Descrição;Valor Pagamento;Data Pagamento;Fornecedor;Banco\n"
                       "ENTRADA;RECEBIMENTOS;500,00;06/04/2026;CLIENTE ALFA;BANCO GAMA - CONTA 1\n"
                       "SAIDA;DESPESAS;120,50;07/04/2026;PADARIA AURORA;BANCO DELTA\n"
                       "SAIDA;DESPESAS;30,00;08/04/2026;GRAFICA BETA;\n"
                       "Total:;;349,50;;;R$ 349,50\n").encode()


def test_planilha_com_coluna_do_banco_separa_as_contas():
    base, leitura, layouts = importar([("financeiro.csv", RELATORIO_COM_BANCO)])
    assert next(iter(layouts.values()))["colunas"]["banco"] == "Banco"
    contas = dict(zip(base["Valor"], base["Banco/Conta"]))
    assert contas == {500.0: "BANCO GAMA - CONTA 1", -120.5: "BANCO DELTA",
                      -30.0: "financeiro (sem banco)"}
    assert leitura["Banco/Conta"].iat[0] == "pela coluna Banco"


def test_escolha_antiga_sem_banco_ainda_acha_a_coluna_do_banco():
    _, _, layouts = importar([("financeiro.csv", RELATORIO_COM_BANCO)])
    (cols, info), = layouts.items()
    antiga = {k: v for k, v in info["colunas"].items() if k != "banco"}
    base, _, _ = importar([("financeiro.csv", RELATORIO_COM_BANCO)], {cols: antiga})
    assert "BANCO DELTA" in set(base["Banco/Conta"])
