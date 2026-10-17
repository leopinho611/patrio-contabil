"""Testes do processamento de extratos. Rodar com: pytest"""

import io
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from financeiro import (  # noqa: E402
    categorizar,
    converter_valor,
    detectar_colunas,
    formatar_reais,
    ler_extrato,
    montar_transacoes,
    resumo_categorias,
    resumo_mensal,
)


def processar(nome, conteudo):
    bruto = ler_extrato(nome, conteudo)
    return montar_transacoes(bruto, detectar_colunas(bruto))


@pytest.mark.parametrize("entrada, esperado", [
    ("1.234,56", 1234.56),
    ("-1.234,56", -1234.56),
    ("R$ -10,00", -10.0),
    ("(50,00)", -50.0),
    ("123,45 D", -123.45),
    ("123,45 C", 123.45),
    ("123,45-", -123.45),
    ("-45.90", -45.9),
    ("1,234.56", 1234.56),
    ("1.234", 1234.0),
    (1500.5, 1500.5),
    ("", None),
    ("abc", None),
])
def test_converter_valor(entrada, esperado):
    assert converter_valor(entrada) == esperado


@pytest.mark.parametrize("descricao, categoria", [
    ("COMPRA CARTAO FARMÁCIA PAGUE MENOS", "SAÚDE"),
    ("PIX ENVIADO RESTAURANTE CAMARÕES", "ALIMENTAÇÃO"),
    ("PIX ENVIADO JOAO SILVA", "TRANSFERÊNCIA"),
    ("POSTO IPIRANGA", "TRANSPORTE"),
    ("COSERN CONTA DE LUZ", "CASA"),
    ("TARIFA PACOTE SERVIÇOS", "TARIFAS BANCÁRIAS"),
    ("PAGAMENTO DARF", "IMPOSTOS E TAXAS"),
    ("AMAZONMKTPLC*AMANASTOR SAO PAULO", "SHOPPING"),
    ("PAO & CIA - LAGOA NOVA NATAL", "ALIMENTAÇÃO"),
    ("ANTHROPIC* CLAUDE SUB", "ASSINATURAS"),
    ("PostoMacaco PARNAMIRIM", "TRANSPORTE"),
    ("PMSP MULTA TRANSITO", "TRANSPORTE"),          # multa de trânsito não é tarifa
    ("Pagamento de boleto efetuado - SEFAZ DISTRITO FEDERAL", "TRANSPORTE"),  # IPVA
    ("LAPV SERV EMPRESARIAIS FLORIANOPOLIS", "SERVIÇOS"),
    ("Brasas Food - Loja", "ALIMENTAÇÃO"),
    ("GASTOS DIVERSOS", "OUTRO"),        # "gas" não pode casar dentro de "gastos"
    ("APLICACAO CDB", "APLICAÇÃO/RESGATE"),
    ("BB Rende Fácil - Rende Facil", "APLICAÇÃO/RESGATE"),
    ("Pagto cartão crédito - VISA", "PAGAMENTO DE FATURA"),
    ("ALGO QUALQUER", "OUTRO"),
])
def test_categorizar(descricao, categoria):
    assert categorizar(descricao, -10) == categoria


def test_entrada_vira_receita():
    assert categorizar("PIX RECEBIDO CLIENTE", 500) == "RECEITA"


def test_csv_banco_do_brasil_latin1():
    conteudo = (
        "Data;Lançamento;Detalhes;Nº documento;Valor;Tipo Lançamento\n"
        "01/09/2026;Saldo Anterior;;;1.000,00;\n"
        "02/09/2026;Pix - Recebido;CLIENTE X;123;2.500,00;Entrada\n"
        "05/09/2026;Compra com Cartão;FARMACIA PAGUE MENOS;124;-89,90;Saída\n"
        "10/10/2026;Pagamento de Boleto;COSERN;125;-210,35;Saída\n"
        "10/10/2026;S A L D O;;;3.200,00;\n"
    ).encode("latin-1")
    t = processar("extrato.csv", conteudo)
    assert len(t) == 3  # linhas de saldo ignoradas
    assert t["Valor"].tolist() == [2500.0, -89.9, -210.35]
    assert t["Mês"].tolist() == ["2026-09", "2026-09", "2026-10"]
    # "Lançamento" + "Detalhes" juntos: o nome da empresa entra na categorização
    assert t["Descrição"].iloc[2] == "Pagamento de Boleto - COSERN"
    assert t["Categoria"].tolist() == ["RECEITA", "SAÚDE", "CASA"]


def test_csv_nubank_virgula_e_ponto_decimal():
    conteudo = (
        "Data,Valor,Identificador,Descrição\n"
        "01/09/2026,-45.90,abc,Compra no débito - IFOOD\n"
        "03/09/2026,1500.00,def,Transferência recebida pelo Pix\n"
    ).encode("utf-8")
    t = processar("nubank.csv", conteudo)
    assert t["Valor"].tolist() == [-45.9, 1500.0]
    assert t["Categoria"].tolist() == ["ALIMENTAÇÃO", "RECEITA"]


def test_csv_credito_e_debito_separados():
    conteudo = (
        "Data Lançamento;Histórico;Crédito;Débito\n"
        "01/09/2026;SALARIO;5.000,00;\n"
        "02/09/2026;UBER TRIP;;32,50\n"
    ).encode("utf-8")
    bruto = ler_extrato("inter.csv", conteudo)
    colunas = detectar_colunas(bruto)
    assert colunas["credito"] == "Crédito" and colunas["debito"] == "Débito"
    t = montar_transacoes(bruto, colunas)
    assert t["Valor"].tolist() == [5000.0, -32.5]
    assert t["Categoria"].tolist() == ["RECEITA", "TRANSPORTE"]


def test_xlsx_com_linhas_de_titulo_antes_do_cabecalho():
    linhas = [
        ["Banco Exemplo S.A.", None, None],
        ["Extrato de conta corrente", None, None],
        [None, None, None],
        ["Data", "Histórico", "Valor (R$)"],
        [pd.Timestamp("2026-09-01"), "SUPERMERCADO NORDESTÃO", -350.0],
        [pd.Timestamp("2026-09-15"), "PIX RECEBIDO", 1200.0],
    ]
    buf = io.BytesIO()
    pd.DataFrame(linhas).to_excel(buf, header=False, index=False)
    t = processar("extrato.xlsx", buf.getvalue())
    assert t["Descrição"].tolist() == ["SUPERMERCADO NORDESTÃO", "PIX RECEBIDO"]
    assert t["Categoria"].tolist() == ["ALIMENTAÇÃO", "RECEITA"]


def test_resumos():
    t = pd.DataFrame({
        "Data": pd.to_datetime(["2026-09-01", "2026-09-02", "2026-10-01"]),
        "Descrição": ["a", "b", "c"],
        "Valor": [1000.0, -300.0, -100.0],
        "Categoria": ["RECEITA", "CASA", "LAZER"],
        "Tipo": ["Entrada", "Saída", "Saída"],
        "Mês": ["2026-09", "2026-09", "2026-10"],
    })
    cats = resumo_categorias(t)
    assert cats["Categoria"].tolist() == ["CASA", "LAZER"]
    assert cats["% das saídas"].tolist() == [75.0, 25.0]
    mensal = resumo_mensal(t)
    assert mensal["Saldo"].tolist() == [700.0, -100.0]


def test_formatar_reais():
    assert formatar_reais(1234.5) == "R$ 1.234,50"
    assert formatar_reais(-10) == "-R$ 10,00"


# ----------------------------------------------------------------------------
# Fatura do cartão BB (texto como sai do PDF), vários arquivos e consolidação
# ----------------------------------------------------------------------------
TEXTO_FATURA = """Resumo da fatura
Datas fatura
Fatura fechada em 30/06/2026
Lançamentos nesta fatura
Data    D
    Pagamentos/Créditos
10/06     PGTO DEBITO CONTA 1234 000000001  200 BR R$ -1.000,00
    Supermercados
14/06     SUPERMERCADO EXEMPLO   NATAL BR R$ 45,00
    Serviços
25/06     NETFLIX.COM            SAO PAULO BR R$ 110,00
02/02     LOJAS EXEMPLO          SAO PAULO BR R$ -50,00
    Compras parceladas
30/09     BABY STORE PARC 09/10  NATAL BR R$ 300,00
Total da Fatura R$ 405,00
"""


def test_fatura_bb():
    from financeiro import ler_fatura_bb
    t = ler_fatura_bb(TEXTO_FATURA)
    assert len(t) == 4                       # pagamento da fatura anterior ignorado
    assert round(-t["Valor"].sum(), 2) == 405.00   # bate com o Total da Fatura
    assert t["Categoria"].tolist() == ["ALIMENTAÇÃO", "ASSINATURAS", "ESTORNO", "SHOPPING"]
    datas = t["Data"].dt.strftime("%d/%m/%Y").tolist()
    # 02/02 é anterior ao fechamento em 30/06 -> mesmo ano; parcela usa o fechamento
    assert datas == ["14/06/2026", "25/06/2026", "02/02/2026", "30/06/2026"]
    assert set(t["Conta"]) == {"BB Cartão"}


def _tabela(conta, linhas):
    t = pd.DataFrame(linhas, columns=["Data", "Descrição", "Valor"])
    t["Data"] = pd.to_datetime(t["Data"])
    from financeiro import categorizar, definir_tipo
    t["Categoria"] = [categorizar(d, v) for d, v in zip(t["Descrição"], t["Valor"])]
    t["Tipo"] = [definir_tipo(c, v) for c, v in zip(t["Categoria"], t["Valor"])]
    t["Conta"] = conta
    t["Mês"] = t["Data"].dt.to_period("M").astype(str)
    return t


def test_consolidar_transferencia_entre_contas_e_rende_facil():
    from financeiro import consolidar
    nu = _tabela("Nubank", [
        ("2026-09-08", "Transferência enviada pelo Pix - FULANO - BB", -500.0),
        ("2026-09-09", "Transferência enviada pelo Pix - PADARIA", -20.0),
    ])
    bb = _tabela("BB Conta", [
        ("2026-09-09", "Pix - Recebido - FULANO", 500.0),
        ("2026-09-09", "BB Rende Fácil - Rende Facil", -480.0),
    ])
    t = consolidar([nu, bb])
    internas = t[t["Tipo"] == "Interna"]
    assert sorted(internas["Categoria"]) == [
        "APLICAÇÃO/RESGATE", "TRANSFERÊNCIA ENTRE CONTAS", "TRANSFERÊNCIA ENTRE CONTAS"]
    assert t.loc[t["Tipo"] == "Saída", "Valor"].tolist() == [-20.0]


def test_pagamento_de_fatura_so_e_interno_com_a_fatura():
    from financeiro import consolidar, ler_fatura_bb
    conta = _tabela("BB Conta", [("2026-07-10", "Pagto cartão crédito - VISA", -405.00)])
    sem_fatura = consolidar([conta.copy()])
    assert sem_fatura["Categoria"].tolist() == ["CARTÃO DE CRÉDITO"]
    assert sem_fatura["Tipo"].tolist() == ["Saída"]
    com_fatura = consolidar([conta.copy(), ler_fatura_bb(TEXTO_FATURA)])
    assert com_fatura.loc[com_fatura["Conta"] == "BB Conta", "Tipo"].tolist() == ["Interna"]


def test_transferencia_para_o_proprio_titular():
    from financeiro import consolidar
    bb = _tabela("BB Conta", [
        ("2026-09-01", "TED-Crédito em Conta - MARIA SILVA SANTOS", 4000.0),
        ("2026-09-02", "Pix - Enviado - JOAO SOUZA", -100.0),
    ])
    t = consolidar([bb], nome_titular="Maria Silva")
    assert t["Tipo"].tolist() == ["Interna", "Saída"]
    # só o primeiro nome não basta (evita homônimos)
    assert consolidar([bb], nome_titular="Maria")["Tipo"].tolist() == ["Entrada", "Saída"]


def test_arquivo_enviado_duas_vezes_nao_duplica():
    from financeiro import consolidar
    linhas = [("2026-09-01", "CAFE", -5.0), ("2026-09-01", "CAFE", -5.0)]  # 2 cafés no dia
    t = consolidar([_tabela("Nubank", linhas), _tabela("Nubank", linhas)])
    assert len(t) == 2


def test_excel_do_bb_com_extensao_excel():
    from financeiro import carregar_arquivo
    buf = io.BytesIO()
    pd.DataFrame([["Data", "Lançamento", "Detalhes", "Valor"],
                  ["01/09/2026", "Pagto Energia Elétrica", "COSERN", "-150,00"]]
                 ).to_excel(buf, header=False, index=False)
    r = carregar_arquivo("Extrato conta corrente - 092026.excel", buf.getvalue())
    assert r["tipo"] == "tabela" and r["conta"] == "BB Conta"


def test_ofx_e_pdf_desconhecido_dao_erro_claro():
    from financeiro import carregar_arquivo
    with pytest.raises(ValueError, match="OFX"):
        carregar_arquivo("extrato.ofx", b"OFXHEADER:100")


# ----------------------------------------------------------------------------
# Fornecedores
# ----------------------------------------------------------------------------
@pytest.mark.parametrize("descricao, fornecedor", [
    ("Transferência enviada pelo Pix - PADARIA EXEMPLO - 12.345.678/0001-90 - BANCO X (0001) "
     "Agência: 1 Conta: 123-4", "Padaria Exemplo"),
    ("Pagto Energia Elétrica - COSERN - DÉBITO ENERGIA", "Cosern"),
    ("Pix - Recebido - 09/09 08:05 12345678901 Fulano de Tal", "Fulano De Tal"),
    ("Compra com Cartão - 26/01 19:57 LOJA EXEMPLO", "Loja Exemplo"),
    ("Pagamento de boleto efetuado - CLINICA SORRISO", "Clinica Sorriso"),
    ("Transferência enviada pelo Pix - MARIA SOUZA (Transferência enviada) - •••.123.456-••",
     "Maria Souza"),
    ("Tarifa Pacote de Serviços - Cobrança referente 08/09/2026", "Tarifa Pacote De Serviços"),
])
def test_extrair_fornecedor(descricao, fornecedor):
    from financeiro import extrair_fornecedor
    assert extrair_fornecedor(descricao) == fornecedor


@pytest.mark.parametrize("bruta, fornecedor", [
    ("PADARIA DO BAIRRO      NATAL BR", "Padaria Do Bairro"),
    ("MP*LOJINHA             SAO PAULO BR", "Lojinha"),
    ("AMAZONMKTPLC*VENDEDOR  SAO PAULO BR", "Amazon"),
    ("LOJA EXEMPLO PR PARC 09/10 NATAL BR", "Loja Exemplo Pr"),
    ("LOJAS AMERICANAS 647   NATAL BR", "Lojas Americanas"),
])
def test_fornecedor_da_fatura(bruta, fornecedor):
    from financeiro import fornecedor_da_fatura
    assert fornecedor_da_fatura(bruta) == fornecedor


def test_resumo_fornecedores_top_10_e_outros():
    from financeiro import resumo_fornecedores
    linhas = [(f"Loja {chr(65 + i)}", -float(100 - i)) for i in range(12)]  # 12 lojas
    linhas += [("LOJA A", -10.0), ("Ferreira Cost", -5.0), ("Ferreira Costa", -5.0)]
    t = pd.DataFrame({
        "Fornecedor": [f for f, _ in linhas], "Valor": [v for _, v in linhas],
        "Tipo": "Saída", "Categoria": "SHOPPING",
    })
    r = resumo_fornecedores(t, "SHOPPING")
    assert len(r) == 11
    assert r.iloc[0]["Fornecedor"] == "Loja A" and r.iloc[0]["Total"] == 110  # junta maiúsculas
    assert r.iloc[-1]["Fornecedor"] == "Outros fornecedores (3)"
    assert "Ferreira Costa" not in r["Fornecedor"].tolist()  # 10 + 10 = R$ 10, ficou em outros
    assert round(r["% da categoria"].sum(), 6) == 100


# ----------------------------------------------------------------------------
# Subcategorias
# ----------------------------------------------------------------------------
@pytest.mark.parametrize("categoria, descricao, sub", [
    ("ALIMENTAÇÃO", "SUPERMERCADO EXEMPLO NATAL", "Supermercado"),
    ("ALIMENTAÇÃO", "Pix - RESTAURANTE DO ZE", "Restaurante"),
    ("ALIMENTAÇÃO", "IFOOD *PEDIDO", "Restaurante"),
    ("ALIMENTAÇÃO", "PADARIA DO BAIRRO", "Padaria"),
    ("ALIMENTAÇÃO", "SACOLAO DO CENTRO", "Hortifruti e açougue"),
    ("ALIMENTAÇÃO", "LANCHONETE QUALQUER COISA", "Restaurante"),
    ("ALIMENTAÇÃO", "ITEM SEM PALAVRA-CHAVE", "Outros"),
    ("SAÚDE", "BARBEARIA DO CENTRO", "Cuidados pessoais"),
    ("SAÚDE", "WELLHUB", "Academia"),
    ("TRANSPORTE", "Pagamento de boleto efetuado - SEFAZ DISTRITO FEDERAL", "IPVA e multas"),
    ("TRANSPORTE", "ESTACIONAMENTO SHOPPING", "Estacionamento"),
    ("CASA", "CLARO S/A - MÓVEL", "Telefone e celular"),
    ("SHOPPING", "RIACHUELO NATAL", "Vestuário"),
    ("LAZER", "GOL LINHAS A* PARC 01/04", "Viagens"),
    ("IMPOSTOS E TAXAS", "RFB-DARF CODIGO DE BARRAS", "Federais"),
    ("TARIFAS BANCÁRIAS", "ANUIDADE DIFERENCIADA", "Anuidade do cartão"),
    ("SAÚDE", "DROGARIA EXEMPLO", "Drogaria"),
    ("SAÚDE", "Pagamento de boleto efetuado - UNIMED NATAL", "Plano de saúde"),
    ("TRANSPORTE", "PostoMacaco PARNAMIRIM", "Posto de gasolina"),
    ("TRANSPORTE", "YOUSE AUTO*2612", "Seguro"),
    ("TRANSPORTE", "DL*99 RIDE SAO PAULO", "Transporte por aplicativo"),
    ("CASA", "Pagto Energia Elétrica - COSERN", "Luz"),
    ("CASA", "Pagamento de boleto efetuado - CAERN", "Água"),
    ("CASA", "LEROY MERLIN PARNAMIRIM", "Manutenções"),
    ("CASA", "Pix - CONDOMINIO RESIDENCIAL", "Condomínio"),
    ("SHOPPING", "LOJA X", "Outros"),
    ("SERVIÇOS", "CONSULTORIA X", ""),   # categoria sem subcategorias
])
def test_subcategorizar(categoria, descricao, sub):
    from financeiro import subcategorizar
    assert subcategorizar(categoria, descricao) == sub


def test_secao_restaurantes_da_fatura_vira_subcategoria():
    from financeiro import subcategorizar
    assert subcategorizar("ALIMENTAÇÃO", "CASA DA VOVO NATAL", secao="restaurantes") == "Restaurante"
    # palavra-chave vence a seção da fatura
    assert subcategorizar("ALIMENTAÇÃO", "HORA DO PAO", secao="restaurantes") == "Padaria"


def test_resumo_subcategorias_outros_por_ultimo():
    from financeiro import resumo_subcategorias
    t = pd.DataFrame({
        "Categoria": "ALIMENTAÇÃO", "Tipo": "Saída",
        "Subcategoria": ["Outros", "Supermercado", "Restaurante", "Restaurante"],
        "Valor": [-500.0, -100.0, -30.0, -20.0],
    })
    r = resumo_subcategorias(t, "ALIMENTAÇÃO")
    assert r["Subcategoria"].tolist() == ["Supermercado", "Restaurante", "Outros"]
    assert r["Total"].tolist() == [100.0, 50.0, 500.0]
    assert resumo_subcategorias(t.assign(Categoria="LAZER", Subcategoria=""), "LAZER").empty


# ----------------------------------------------------------------------------
# CNPJ, CNAE e regras lembradas
# ----------------------------------------------------------------------------
def test_extrair_cnpj():
    from financeiro import extrair_cnpj
    assert extrair_cnpj("Pix - LOJA - 12.345.678/0001-90 - BANCO") == "12345678000190"
    assert extrair_cnpj("Pix - FULANA - •••.123.456-•• - BANCO") is None   # CPF mascarado


@pytest.mark.parametrize("cnae, esperado", [
    ("4721102", ("ALIMENTAÇÃO", "Padaria")),
    (5611201, ("ALIMENTAÇÃO", "Restaurante")),
    ("8640-2/05", ("SAÚDE", "Clínicas, consultas e exames")),
    ("4711302", ("ALIMENTAÇÃO", "Supermercado")),
    ("6920601", ("SERVIÇOS", "")),
    ("0111301", None),
    ("", None),
])
def test_categoria_do_cnae(cnae, esperado):
    from financeiro import categoria_do_cnae
    assert categoria_do_cnae(cnae) == esperado


def _regras(linhas):
    from regras import nova_regra
    return pd.concat([nova_regra(*l) for l in linhas], ignore_index=True)


def test_aplicar_regras_vence_palavras_chave_e_casa_nome_cortado():
    from financeiro import aplicar_regras
    t = pd.DataFrame({
        "Fornecedor": ["Loja Exemplo", "Loja Exemplo Natal", "Outra", "Loja Exemplo"],
        "Categoria": ["OUTRO", "OUTRO", "OUTRO", "RECEITA"],
        "Subcategoria": ["", "", "", ""],
        "Tipo": ["Saída", "Saída", "Saída", "Entrada"],
        "Valor": [-10.0, -20.0, -5.0, 50.0],
    })
    regras = _regras([("loja exemplo", "Loja Exemplo", "ALIMENTAÇÃO", "Padaria", "usuario")])
    r = aplicar_regras(t, regras)
    assert r["Categoria"].tolist() == ["ALIMENTAÇÃO", "ALIMENTAÇÃO", "OUTRO", "RECEITA"]
    assert r["Subcategoria"].tolist()[:2] == ["Padaria", "Padaria"]


def test_regra_com_subcategoria_invalida_vira_outros():
    from financeiro import aplicar_regras
    t = pd.DataFrame({"Fornecedor": ["Loja X"], "Categoria": ["OUTRO"], "Subcategoria": [""],
                      "Tipo": ["Saída"], "Valor": [-10.0]})
    r = aplicar_regras(t, _regras([("loja x", "Loja X", "SAÚDE", "Padaria", "usuario")]))
    assert (r["Categoria"].iat[0], r["Subcategoria"].iat[0]) == ("SAÚDE", "Outros")


def test_eh_empresa():
    from financeiro import eh_empresa
    assert eh_empresa(["LOJA X NATAL"], ["BB Cartão"])
    assert eh_empresa(["Transferência enviada pelo Pix - LOJA - 12.345.678/0001-90"], ["Nubank"])
    assert not eh_empresa(["Transferência enviada pelo Pix - FULANA - •••.123.456-••"], ["Nubank"])


def test_repositorio_local_salva_e_carrega(tmp_path, monkeypatch):
    import regras
    monkeypatch.setattr(regras, "ARQUIVO_LOCAL", tmp_path / "regras.csv")
    repo = regras.RepositorioRegras()
    assert repo.carregar().empty
    novas = _regras([("loja x", "Loja X", "SAÚDE", "Drogaria", "usuario")])
    assert repo.salvar(novas, "teste") == "local"
    lidas = repo.carregar()
    assert lidas[["chave", "categoria", "subcategoria"]].values.tolist() == \
        [["loja x", "SAÚDE", "Drogaria"]]


def test_repositorio_github_envia_commit(monkeypatch):
    import base64
    import json
    import regras
    chamadas = []

    class Resposta:
        def __init__(self, dados):
            self.dados = dados

        def read(self):
            return json.dumps(self.dados).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def falso_urlopen(req, timeout):
        chamadas.append((req.get_method(), req.full_url, req.data))
        if req.get_method() == "GET":
            csv = "chave,fornecedor,categoria,subcategoria\nantiga,Antiga,LAZER,Viagens\n"
            return Resposta({"sha": "abc", "content": base64.b64encode(csv.encode()).decode()})
        return Resposta({"content": {}})

    monkeypatch.setattr(regras.urllib.request, "urlopen", falso_urlopen)
    repo = regras.RepositorioRegras("token", "dono/repo", "main")
    assert repo.carregar()["chave"].tolist() == ["antiga"]
    juntas = regras.juntar(repo.carregar(), _regras([("nova", "Nova", "SAÚDE", "", "usuario")]))
    assert repo.salvar(juntas, "Regras: Nova") == "github"
    metodo, url, corpo = chamadas[-1]
    enviado = json.loads(corpo)
    assert metodo == "PUT" and url.endswith("/repos/dono/repo/contents/regras/regras_fornecedores.csv")
    assert enviado["sha"] == "abc" and enviado["branch"] == "main"
    assert "antiga" in base64.b64decode(enviado["content"]).decode()   # não apaga as antigas


def test_consultar_cnpj_formata_resposta(monkeypatch):
    import json
    import regras

    class Resposta:
        def read(self):
            return json.dumps({"razao_social": "LC COMERCIO LTDA", "nome_fantasia": "PADARIA X",
                               "cnae_fiscal": 4721102,
                               "cnae_fiscal_descricao": "Padaria e confeitaria"}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(regras.urllib.request, "urlopen", lambda req, timeout: Resposta())
    assert regras.consultar_cnpj("12345678000190") == {
        "nome": "PADARIA X", "cnae": "4721102", "atividade": "Padaria e confeitaria",
        "mei": False}

    def falha(req, timeout):
        raise OSError("sem rede")
    monkeypatch.setattr(regras.urllib.request, "urlopen", falha)
    assert regras.consultar_cnpj("12345678000190") is None


def test_token_vencido_da_mensagem_clara(monkeypatch):
    import urllib.error
    import regras

    def negado(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 401, "Bad credentials", {}, None)

    monkeypatch.setattr(regras.urllib.request, "urlopen", negado)
    repo = regras.RepositorioRegras("token-vencido", "dono/repo")
    with pytest.raises(RuntimeError, match="token do GitHub venceu"):
        repo.salvar(regras.regras_vazias(), "teste")
    # leitura não quebra: cai para a cópia do repositório
    assert list(repo.carregar().columns) == regras.COLUNAS_REGRAS


# ----------------------------------------------------------------------------
# Nubank em OFX e PDF, duplicatas entre formatos, banco de destino do Pix
# ----------------------------------------------------------------------------
OFX_EXEMPLO = """OFXHEADER:100
DATA:OFXSGML
<OFX>
<BANKMSGSRSV1><STMTTRNRS><STMTRS>
<BANKACCTFROM>
<BANKID>0260</BANKID>
<ACCTID>123-4</ACCTID>
</BANKACCTFROM>
<BANKTRANLIST>
<STMTTRN>
<TRNTYPE>DEBIT</TRNTYPE>
<DTPOSTED>20260102000000[-3:BRT]</DTPOSTED>
<TRNAMT>-42.00</TRNAMT>
<MEMO>Transferência enviada pelo Pix - PADARIA EXEMPLO - 12.345.678/0001-90 - BANCO X (0001) Agência: 1 Conta: 1-1</MEMO>
</STMTTRN>
<STMTTRN>
<TRNTYPE>CREDIT</TRNTYPE>
<DTPOSTED>20260105000000[-3:BRT]</DTPOSTED>
<TRNAMT>1500.00</TRNAMT>
<MEMO>Transferência recebida pelo Pix - CLIENTE X - 98.765.432/0001-10 - BANCO Y</MEMO>
</STMTTRN>
</BANKTRANLIST>
</STMTRS></STMTTRNRS></BANKMSGSRSV1>
</OFX>"""

PDF_NUBANK_TEXTO = """Fulano de Tal
 •••.111.222-••  0001 CPF Agência Conta
 a 01 DE JANEIRO DE 2026 31 DE JANEIRO DE 2026 VALORES EM R$
Movimentações
02 JAN 2026 Total de saídas - 42,00
Transferência enviada pelo Pix PADARIA EXEMPLO - 12.345.678/0001-90 - BANCO X 
(0001) Agência: 1 Conta: 1-1
42,00
05 JAN 2026 Total de entradas + 1.500,00
Transferência recebida pelo Pix CLIENTE X - 98.765.432/0001-10 - 
Tem alguma dúvida? Mande uma mensagem para nosso time
disponíveis em nubank.com.br/contatos#ouvidoria .
Extrato gerado dia 06 de outubro de 2026 às 13:15 1 de 2
Fulano de Tal
 a 01 DE JANEIRO DE 2026 31 DE JANEIRO DE 2026 VALORES EM R$
BANCO Y 1.500,00
Total de saídas - 1.000,00
Aplicação RDB 1.000,00
O saldo líquido corresponde ao total de depósitos e rendimentos
Tem alguma dúvida? Mande uma mensagem
Extrato gerado dia 06 de outubro de 2026 às 13:15 2 de 2"""


def test_ler_ofx_nubank():
    from financeiro import carregar_arquivo
    r = carregar_arquivo("extrato.ofx", OFX_EXEMPLO.encode("utf-8"))
    t = r["dados"]
    assert r["conta"] == "Nubank"
    assert t["Valor"].tolist() == [-42.0, 1500.0]
    assert t["Data"].dt.strftime("%d/%m/%Y").tolist() == ["02/01/2026", "05/01/2026"]
    assert t["Fornecedor"].tolist() == ["Padaria Exemplo", "Cliente X"]


def test_ler_extrato_nubank_pdf():
    from financeiro import eh_extrato_nubank, ler_extrato_nubank_pdf
    assert eh_extrato_nubank(PDF_NUBANK_TEXTO)
    t = ler_extrato_nubank_pdf(PDF_NUBANK_TEXTO)
    assert t["Valor"].tolist() == [-42.0, 1500.0, -1000.0]   # inclui o que vem no fim da linha
    assert t["Fornecedor"].tolist()[:2] == ["Padaria Exemplo", "Cliente X"]
    assert t["Descrição"].iat[1].endswith("BANCO Y")          # continuou na página seguinte
    assert t["Categoria"].iat[2] == "APLICAÇÃO/RESGATE"


def test_mesmo_mes_em_csv_ofx_e_pdf_nao_duplica():
    from financeiro import (carregar_arquivo, consolidar, detectar_colunas, ler_extrato_nubank_pdf,
                            montar_transacoes)
    csv = ("Data,Valor,Identificador,Descrição\n"
           "02/01/2026,-42.00,a,Transferência enviada pelo Pix - PADARIA EXEMPLO - "
           "12.345.678/0001-90 - BANCO X\n"
           "05/01/2026,1500.00,b,Transferência recebida pelo Pix - CLIENTE X\n").encode()
    r = carregar_arquivo("NU_extrato.csv", csv)
    t_csv = montar_transacoes(r["bruto"], detectar_colunas(r["bruto"]), r["conta"])
    t_ofx = carregar_arquivo("extrato.ofx", OFX_EXEMPLO.encode())["dados"]
    t_pdf = ler_extrato_nubank_pdf(PDF_NUBANK_TEXTO)
    t = consolidar([t_csv, t_ofx, t_pdf])
    assert sorted(t["Valor"].tolist()) == [-1000.0, -42.0, 1500.0]   # RDB só existe no PDF


@pytest.mark.parametrize("descricao, esperado", [
    ("Transferência enviada pelo Pix - REDE DE POSTOS - 34.615.965/0001-00 - "
     "PAGSEGURO INTERNET IP S.A. (0290) Agência: 1", "TRANSPORTE"),
    ("Transferência enviada pelo Pix - FULANA - •••.123.456-•• - MERCADO PAGO IP LTDA. (0323)",
     "TRANSFERÊNCIA"),
])
def test_banco_de_destino_nao_define_categoria(descricao, esperado):
    from financeiro import categorizar
    assert categorizar(descricao, -10) == esperado


def test_mp_asterisco_sai_do_fornecedor():
    from financeiro import extrair_fornecedor
    assert extrair_fornecedor("Compra com Cartão - 23/01 13:09 MP*LOJINHA") == "Lojinha"


def test_regra_unifica_nome_do_fornecedor():
    from financeiro import aplicar_regras
    t = pd.DataFrame({"Fornecedor": ["Empresa X Consulting", "Empresa X Serv"],
                      "Categoria": ["SERVIÇOS", "SERVIÇOS"], "Subcategoria": ["", ""],
                      "Tipo": ["Saída", "Saída"], "Valor": [-10.0, -20.0]})
    regras = _regras([("empresa x consulting", "Empresa X", "EDUCAÇÃO", "", "usuario"),
                      ("empresa x serv", "Empresa X", "EDUCAÇÃO", "", "usuario")])
    r = aplicar_regras(t, regras)
    assert r["Fornecedor"].tolist() == ["Empresa X", "Empresa X"]
    assert r["FornecedorOriginal"].tolist() == ["Empresa X Consulting", "Empresa X Serv"]


def test_subcategoria_feira():
    from financeiro import subcategorizar
    assert subcategorizar("ALIMENTAÇÃO", "FEIRA DO BAIRRO") == "Feira"


# ----------------------------------------------------------------------------
# Arquivos compactados e MEI
# ----------------------------------------------------------------------------
def _zip(arquivos):
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for nome, dados in arquivos:
            z.writestr(nome, dados)
    return buf.getvalue()


def test_expandir_zip_ignora_pastas_e_lixo_do_mac():
    from financeiro import expandir_zip
    dados = _zip([("Extratos/Nubank/NU_jan.csv", b"Data,Valor\n"),
                  ("Extratos/BB/fatura.pdf", b"%PDF-1.4"),
                  ("__MACOSX/Extratos/._NU_jan.csv", b"lixo"),
                  ("Extratos/.DS_Store", b"lixo")])
    assert [n for n, _ in expandir_zip(dados)] == ["NU_jan.csv", "fatura.pdf"]


def test_expandir_zip_recusa_zip_bomba_e_corrompido(monkeypatch):
    import financeiro
    monkeypatch.setattr(financeiro, "LIMITE_ZIP_BYTES", 100)
    with pytest.raises(ValueError, match="100 MB"):
        financeiro.expandir_zip(_zip([("grande.csv", b"x" * 1000)]))
    with pytest.raises(ValueError, match="corrompido"):
        financeiro.expandir_zip(b"isto nao e um zip")


def test_mei_nao_vai_para_regras_compartilhadas():
    from financeiro import eh_empresa, eh_mei
    pix_mei = ("Transferência enviada pelo Pix - 12.345.678 MARIA DA SILVA - "
               "12.345.678/0001-90 - BANCO X")
    assert eh_mei(pix_mei)
    assert not eh_empresa([pix_mei], ["Nubank"])
    assert not eh_mei("Transferência enviada pelo Pix - PADARIA X - 12.345.678/0001-90")


# ----------------------------------------------------------------------------
# Entradas
# ----------------------------------------------------------------------------
@pytest.mark.parametrize("descricao, tipo", [
    ("Transferência recebida pelo Pix - CLIENTE LTDA - 12.345.678/0001-90 - BANCO X", "Recebido de empresas"),
    ("Transferência Recebida - Fulano de Tal - •••.123.456-•• - NU PAGAMENTOS", "Recebido de pessoas"),
    ("Pix - Recebido - 09/09 08:05 12345678901 Fulano de Tal", "Recebido de pessoas"),
    ("Pix recebido - 12.345.678 MARIA DA SILVA - 12.345.678/0001-90", "Recebido de pessoas"),   # MEI
    ("CRÉDITO DE SALÁRIO EMPRESA X", "Salário"),
    ("Rendimento líquido", "Rendimentos"),
    ("Estorno de compra", "Estornos e reembolsos"),
    ("Transferência Periódica - 20/01 FULANO", "Outras entradas"),
])
def test_tipo_de_entrada(descricao, tipo):
    from financeiro import tipo_de_entrada
    assert tipo_de_entrada(descricao) == tipo


def test_resumos_de_entradas():
    from financeiro import resumo_categorias, resumo_fornecedores
    t = pd.DataFrame({
        "Categoria": ["Recebido de empresas"] * 3 + ["Recebido de pessoas"],
        "Subcategoria": "", "Tipo": "Entrada",
        "Fornecedor": ["Cliente A", "Cliente A", "Cliente B", "Fulano"],
        "Valor": [1000.0, 500.0, 500.0, 200.0],
    })
    cats = resumo_categorias(t, tipo="Entrada")
    assert cats["Categoria"].tolist() == ["Recebido de empresas", "Recebido de pessoas"]
    assert cats["Total"].tolist() == [2000.0, 200.0]           # entradas ficam positivas
    assert "% das entradas" in cats
    fontes = resumo_fornecedores(t, "Recebido de empresas", tipo="Entrada", n=1)
    assert fontes["Fornecedor"].tolist() == ["Cliente A", "Outras fontes (1)"]
    assert fontes["% da categoria"].tolist() == [75.0, 25.0]
