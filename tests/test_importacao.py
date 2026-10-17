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
