"""
Leitura, limpeza e categorização de extratos bancários.

Copiado de github.com/leopinho611/sistema-financeiro-pj (app Contas em Dia, commit b75efa0).
Mudanças feitas aqui não voltam para lá; mantenha as correções de leitura nos dois se precisar.
"""

import io
import re
import unicodedata
import warnings
import zipfile
from pathlib import PurePosixPath

import pandas as pd
from pypdf import PdfReader

# ============================================================================
# CATEGORIAS
# ============================================================================
# A ordem importa: a primeira categoria que casar vence. TRANSFERÊNCIA fica por
# último porque "PIX RESTAURANTE X" deve cair em ALIMENTAÇÃO, não em transferência.
# Palavras sem acento e em minúsculas; casam como palavra inteira (com plural "s").
# Terminada em "*", casa com o começo da palavra: "amazon*" pega "AMAZONMKTPLC".
CATEGORIAS = {
    "CASA": ["aluguel", "condominio", "agua", "luz", "energia", "cosern", "caern",
             "gas", "internet", "telefone", "celular", "claro", "vivo", "tim",
             "leroy merlin", "ferreira cost*", "tok stok", "casas bahia"],
    "TRANSPORTE": ["combustivel", "gasolina", "etanol", "posto*", "estacionamento",
                   "uber*", "99 ride", "99app", "99 tecnologia", "onibus", "pedagio",
                   "ipva", "detran",
                   "shell", "ipiranga", "youse", "seguro auto", "sefaz",
                   "multa transito", "multa de transito", "licenciamento"],
    "ALIMENTAÇÃO": ["restaurante", "lanchonete", "comida", "mercado", "supermercado",
                    "padaria", "panificadora", "pao", "delivery", "ifood", "rappi",
                    "acougue", "carnes", "frios", "hortifruti", "sacolao", "nordestao",
                    "carrefour", "atacadao", "assai", "favorito", "pizzaria", "bolo",
                    "camaroes", "queijo", "gelateria", "sorveteria", "crepe*", "cafe",
                    "cafeteria", "burger*", "hamburgueria", "food", "brasasfood",
                    "eloi chaves", "hortifruti"],
    "SAÚDE": ["farmacia", "drogaria", "pague menos", "drogasil", "raia", "medico",
              "clinica", "dentista", "hospital", "laboratorio", "academia", "wellhub",
              "gympass", "unimed", "hapvida", "plano de saude", "barbearia", "estetica"],
    "EDUCAÇÃO": ["escola", "faculdade", "universidade", "curso", "livro", "livraria",
                 "papelaria", "papelpapelar*"],
    "ASSINATURAS": ["netflix", "spotify", "anthropic*", "claude", "openai", "chatgpt",
                    "youtube", "disney*", "hbo", "prime video", "globoplay",
                    "deezer", "apple com", "icloud", "google one", "microsoft", "adobe"],
    "LAZER": ["cinema", "teatro", "show", "viagem", "hotel", "airbnb", "booking",
              "ingresso", "gol linhas", "latam", "azul linhas", "zupper", "decolar",
              "aquario"],
    "SHOPPING": ["roupa", "sapato", "loja", "lojas", "vestuario", "shopping", "magazine",
                 "amazon*", "mercadolivre", "mercado livre", "shopee", "shein",
                 "renner", "riachuelo", "baby"],
    "IMPOSTOS E TAXAS": ["irpf", "irpj", "icms", "pis", "cofins", "iss", "inss", "darf",
                         "gps", "csll", "simples nacional", "gru", "pref mun",
                         "prefeitura", "receita federal", "rfb", "iptu",
                         "conselho regional"],
    "TARIFAS BANCÁRIAS": ["tarifa", "iof", "juros", "anuidade", "cesta", "encargo",
                          "multa"],
    "SERVIÇOS": ["consultoria", "consulting", "assessoria", "contabilidade",
                 "serv empresariais", "servicos empresariais", "getninjas"],
    "TRANSFERÊNCIA": ["pix", "ted", "doc", "transferencia", "transf"],
}
CATEGORIA_ENTRADA = "RECEITA"
CATEGORIA_PADRAO = "OUTRO"
CATEGORIA_ESTORNO = "ESTORNO"
CATEGORIA_CARTAO = "CARTÃO DE CRÉDITO"   # pagamento de fatura quando a fatura não foi importada

# Movimentações internas: o dinheiro continua seu, só muda de lugar.
# Não contam como entrada nem saída.
CATEGORIA_APLICACAO = "APLICAÇÃO/RESGATE"
CATEGORIA_FATURA = "PAGAMENTO DE FATURA"
CATEGORIA_ENTRE_CONTAS = "TRANSFERÊNCIA ENTRE CONTAS"
CATEGORIAS_INTERNAS = {CATEGORIA_APLICACAO, CATEGORIA_FATURA, CATEGORIA_ENTRE_CONTAS}

PALAVRAS_APLICACAO = ["rende facil", "rdb", "nu reserva", "valor mensal programado",
                      "caixinha", "poupanca", "aplicacao", "resgate", "fii", "acao",
                      "acoes", "tesouro", "cdb", "lci", "lca", "corretora", "bitcoin"]
PALAVRAS_FATURA = ["pagto cartao", "pagamento de fatura", "pagamento fatura",
                   "pgto fatura", "fatura do cartao", "fatura cartao"]
PADRAO_TRANSFERENCIA = re.compile(r"\b(?:pix|ted|doc|transf|transferencia)")

# Linhas de saldo não são transações (aparecem em quase todo extrato brasileiro;
# o BB escreve "S A L D O", por isso a comparação também é feita sem espaços)
PADRAO_SALDO = re.compile(r"\bsaldo\b")


def eh_linha_de_saldo(descricao):
    texto = normalizar(descricao)
    return bool(PADRAO_SALDO.search(texto)) or texto.replace(" ", "").startswith("saldo")


def normalizar(texto):
    """Minúsculas, sem acentos e com espaços simples."""
    sem_acento = unicodedata.normalize("NFKD", str(texto))
    sem_acento = "".join(c for c in sem_acento if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[.&*@/]", " ", sem_acento.lower())).strip()


def _padrao(palavras):
    partes = [re.escape(p[:-1]) + r"\w*" if p.endswith("*") else re.escape(p) + r"s?\b"
              for p in palavras]
    return re.compile(r"\b(?:" + "|".join(partes) + r")")


_PADROES = {categoria: _padrao(palavras) for categoria, palavras in CATEGORIAS.items()}
_PADRAO_APLICACAO = _padrao(PALAVRAS_APLICACAO)
_PADRAO_FATURA = _padrao(PALAVRAS_FATURA)


_DOC_PIX = re.compile(r"\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}|•••\.?\s*\d{3}\.\d{3}-••")


def sem_dados_bancarios(descricao):
    """Descrição sem o CPF/CNPJ e o banco de destino do Pix.

    "Pix - REDE DE POSTOS - 34.615.965/0001-00 - PAGSEGURO INTERNET IP S.A. (0290)..."
    -> "Pix - REDE DE POSTOS". Sem isso, "PAGSEGURO INTERNET" virava Casa/Internet e
    "MERCADO PAGO" virava supermercado.
    """
    texto = str(descricao)
    m = _DOC_PIX.search(texto)
    if m:
        texto = texto[:m.start()]
    else:
        texto = re.sub(r"\s-\s[^-]*Agência:.*$", "", texto)
    return texto.strip(" -")


def categorizar(descricao, valor=None):
    """Categoria de uma transação. Entradas (valor > 0) viram RECEITA.

    Aplicações/resgates e pagamento de fatura são identificados primeiro,
    nos dois sentidos, porque são movimentações internas.
    """
    texto = normalizar(sem_dados_bancarios(descricao)).replace("mercado livre", "mercadolivre")
    if _PADRAO_APLICACAO.search(texto):
        return CATEGORIA_APLICACAO
    if _PADRAO_FATURA.search(texto):
        return CATEGORIA_FATURA
    if valor is not None and valor > 0:
        return CATEGORIA_ENTRADA
    for categoria, padrao in _PADROES.items():
        if padrao.search(texto):
            return categoria
    return CATEGORIA_PADRAO


# ============================================================================
# LEITURA DO ARQUIVO
# ============================================================================
_NOMES_DATA = ("data", "dt ", "dt.", "date")
_NOMES_DESCRICAO = ("descri", "histor", "lancamento", "estabelecimento", "detalhe",
                    "memo", "titulo")
_NOMES_VALOR = ("valor", "quantia", "montante", "amount")
_NOMES_CREDITO = ("credito", "entrada")
_NOMES_DEBITO = ("debito", "saida")


def _eh_cabecalho(celulas):
    """Linha parece cabeçalho se tem 'data' e algo de descrição ou valor."""
    textos = [normalizar(c) for c in celulas if isinstance(c, str)]
    tem_data = any(t.startswith("data") or t in ("dt", "date") for t in textos)
    tem_outro = any(t.startswith(_NOMES_DESCRICAO + _NOMES_VALOR + _NOMES_CREDITO + _NOMES_DEBITO)
                    for t in textos)
    return tem_data and tem_outro


def _aplicar_cabecalho(bruto):
    """Bancos costumam pôr linhas de título antes do cabeçalho: procura a linha certa."""
    for i in range(min(len(bruto), 30)):
        if _eh_cabecalho(bruto.iloc[i].tolist()):
            df = bruto.iloc[i + 1:].copy()
            nomes = [str(c).strip() if pd.notna(c) else f"coluna_{j + 1}"
                     for j, c in enumerate(bruto.iloc[i])]
            df.columns = _nomes_unicos(nomes)
            return df.dropna(how="all").reset_index(drop=True)
    # Sem cabeçalho reconhecível: usa a primeira linha
    df = bruto.iloc[1:].copy()
    df.columns = _nomes_unicos([str(c) for c in bruto.iloc[0]])
    return df.dropna(how="all").reset_index(drop=True)


def _nomes_unicos(nomes):
    vistos = {}
    saida = []
    for n in nomes:
        if n in vistos:
            vistos[n] += 1
            saida.append(f"{n}_{vistos[n]}")
        else:
            vistos[n] = 0
            saida.append(n)
    return saida


def ler_extrato(nome, conteudo):
    """Lê CSV ou XLSX de qualquer banco e devolve um DataFrame com o cabeçalho certo."""
    if nome.lower().endswith((".xlsx", ".xls", ".excel")) or conteudo[:2] == b"PK":
        # O BB exporta XLSX com extensão ".excel"; XLSX sempre começa com "PK"
        with warnings.catch_warnings():
            # O Excel do BB não tem estilo padrão; o aviso do openpyxl é inofensivo
            warnings.simplefilter("ignore", UserWarning)
            bruto = pd.read_excel(io.BytesIO(conteudo), header=None, dtype=object,
                                  engine="openpyxl")
    else:
        try:
            texto = conteudo.decode("utf-8-sig")
        except UnicodeDecodeError:
            texto = conteudo.decode("latin-1")  # padrão de vários bancos brasileiros
        # Separador: o que aparece mais vezes numa mesma linha entre as primeiras
        amostra = texto.splitlines()[:30]
        separador = max([";", ",", "\t", "|"],
                        key=lambda s: max((l.count(s) for l in amostra), default=0))
        bruto = pd.read_csv(io.StringIO(texto), sep=separador, header=None, dtype=str,
                            engine="python", skip_blank_lines=True, on_bad_lines="skip")
    if bruto.empty:
        raise ValueError("O arquivo está vazio.")
    return _aplicar_cabecalho(bruto)


# ============================================================================
# CONVERSÃO DE VALORES E DATAS
# ============================================================================
def converter_valor(v):
    """Converte '1.234,56', 'R$ -10,00', '(50,00)', '123,45 D', 1234.5 em float."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return None

    negativo = False
    # Sufixo de débito/crédito usado por alguns bancos: "123,45 D", "123,45 C", "123,45-"
    sufixo = re.fullmatch(r"(.*?)\s*([DdCc-])", s)
    if sufixo and re.search(r"\d", sufixo.group(1)):
        s = sufixo.group(1)
        negativo = sufixo.group(2) in "Dd-"
    if s.startswith("(") and s.endswith(")"):
        negativo, s = True, s[1:-1]
    s = s.replace("R$", "").replace(" ", "").replace(" ", "")
    if s.startswith("-"):
        negativo, s = True, s[1:]
    elif s.startswith("+"):
        s = s[1:]

    if "," in s and "." in s:
        # O separador que aparece por último é o decimal
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "")  # 1.234 = mil duzentos e trinta e quatro

    try:
        numero = float(s)
    except ValueError:
        return None
    return -numero if negativo else numero


def converter_datas(serie):
    """Datas em dd/mm/aaaa (padrão brasileiro), aaaa-mm-dd ou datas do Excel."""
    def uma(v):
        if isinstance(v, pd.Timestamp):
            return v
        if hasattr(v, "year"):  # datetime/date vindo do Excel
            return pd.Timestamp(v)
        s = str(v).strip()
        if re.match(r"\d{4}-\d{2}-\d{2}", s):
            return pd.to_datetime(s[:10], format="%Y-%m-%d", errors="coerce")
        return pd.to_datetime(s, dayfirst=True, errors="coerce")
    return serie.map(uma)


# ============================================================================
# IDENTIFICAÇÃO DAS COLUNAS
# ============================================================================
def _casa(coluna, prefixo):
    n = normalizar(coluna)
    return n.startswith(prefixo) or f" {prefixo}" in f" {n}"


def _procura(colunas, prefixos, exceto=()):
    """Primeira coluna que casa, respeitando a ordem de prioridade dos prefixos."""
    for p in prefixos:
        for c in colunas:
            if c not in exceto and _casa(c, p):
                return c
    return None


def detectar_colunas(df):
    """Sugere quais colunas são data, descrição (lista), valor (ou crédito/débito)."""
    colunas = list(df.columns)
    data = _procura(colunas, _NOMES_DATA)
    if data is None:
        # Coluna onde a maioria das linhas vira data
        for c in colunas:
            if converter_datas(df[c].head(20)).notna().mean() > 0.8:
                data = c
                break

    credito = _procura(colunas, _NOMES_CREDITO, exceto=(data,))
    debito = _procura(colunas, _NOMES_DEBITO, exceto=(data, credito))
    valor = _procura(colunas, _NOMES_VALOR, exceto=(data, credito, debito))
    if valor is None and not (credito and debito):
        for c in colunas:
            if c == data:
                continue
            if df[c].head(20).map(converter_valor).notna().mean() > 0.8:
                valor = c
                break

    # Descrição pode ocupar mais de uma coluna (BB: "Lançamento" + "Detalhes")
    usadas = (data, valor, credito, debito)
    # (só nomes que COMEÇAM com a palavra: "Tipo Lançamento" não é descrição)
    descricao = [c for p in _NOMES_DESCRICAO for c in colunas
                 if c not in usadas and normalizar(c).startswith(p)]
    descricao = list(dict.fromkeys(descricao))  # sem repetir, na ordem de prioridade
    if not descricao:
        # Coluna de texto mais longa
        candidatas = [c for c in colunas if c not in usadas]
        if candidatas:
            descricao = [max(candidatas, key=lambda c: df[c].astype(str).str.len().mean())]

    usa_credito_debito = valor is None and credito is not None and debito is not None
    return {
        "data": data,
        "descricao": descricao,
        "valor": None if usa_credito_debito else valor,
        "credito": credito if usa_credito_debito else None,
        "debito": debito if usa_credito_debito else None,
    }


# ============================================================================
# MONTAGEM DAS TRANSAÇÕES
# ============================================================================
def definir_tipo(categoria, valor):
    if categoria in CATEGORIAS_INTERNAS:
        return "Interna"
    return "Entrada" if valor > 0 else "Saída"


def montar_transacoes(df, colunas, conta=""):
    """Gera a tabela padronizada: Data, Descrição, Valor, Tipo, Categoria, Conta, Mês."""
    if not colunas.get("data") or not colunas.get("descricao"):
        raise ValueError("Escolha as colunas de data e de descrição.")

    if colunas.get("valor"):
        valores = df[colunas["valor"]].map(converter_valor)
    elif colunas.get("credito") and colunas.get("debito"):
        cred = df[colunas["credito"]].map(converter_valor).fillna(0).abs()
        deb = df[colunas["debito"]].map(converter_valor).fillna(0).abs()
        valores = cred - deb
        valores[(df[colunas["credito"]].isna()) & (df[colunas["debito"]].isna())] = None
    else:
        raise ValueError("Escolha a coluna de valor (ou as de crédito e débito).")

    t = pd.DataFrame({
        "Data": converter_datas(df[colunas["data"]]),
        "Descrição": df[colunas["descricao"]].fillna("").astype(str).apply(
            lambda linha: " - ".join(v.strip() for v in linha if v.strip()), axis=1),
        "Valor": pd.to_numeric(valores, errors="coerce"),
    })
    t = t.dropna(subset=["Data", "Valor"])
    t = t[t["Valor"] != 0]
    t = t[~t["Descrição"].map(eh_linha_de_saldo)]

    t["Categoria"] = [categorizar(d, v) for d, v in zip(t["Descrição"], t["Valor"])]
    t["Tipo"] = [definir_tipo(c, v) for c, v in zip(t["Categoria"], t["Valor"])]
    t["Fornecedor"] = t["Descrição"].map(extrair_fornecedor)
    t["Conta"] = conta
    t["Mês"] = t["Data"].dt.to_period("M").astype(str)
    return t.sort_values("Data").reset_index(drop=True)


def coluna_pct(tipo):
    return "% das saídas" if tipo == "Saída" else "% das entradas"


def resumo_categorias(t, tipo="Saída"):
    """Total por categoria (valores positivos), do maior para o menor.

    tipo="Saída" (padrão) ou "Entrada".
    """
    saidas = t[t["Tipo"] == tipo]
    tabela = (saidas.groupby("Categoria")["Valor"].agg(["sum", "count"])
              .rename(columns={"sum": "Total", "count": "Transações"}))
    tabela["Total"] = tabela["Total"].abs()
    total = tabela["Total"].sum()
    tabela[coluna_pct(tipo)] = (tabela["Total"] / total * 100) if total else 0
    return tabela.sort_values("Total", ascending=False).reset_index()


def resumo_mensal(t):
    """Entradas, saídas e saldo por mês."""
    entradas = t[t["Tipo"] == "Entrada"].groupby("Mês")["Valor"].sum()
    saidas = -t[t["Tipo"] == "Saída"].groupby("Mês")["Valor"].sum()
    tabela = pd.DataFrame({"Entradas": entradas, "Saídas": saidas}).fillna(0)
    tabela["Saldo"] = tabela["Entradas"] - tabela["Saídas"]
    return tabela.sort_index().reset_index().rename(columns={"index": "Mês"})


# ============================================================================
# SUBCATEGORIAS
# ============================================================================
# Só as categorias listadas aqui têm subcategorias; nas demais o detalhe vai
# direto para os fornecedores. O que não casar vira "Outros".
# Mesmas regras de palavras de CATEGORIAS (palavra inteira; "*" = começo da palavra).
SUBCATEGORIAS = {
    "ALIMENTAÇÃO": {
        "Padaria": ["padaria", "panificadora", "pao", "bolo", "confeitaria", "doce*"],
        "Feira": ["feira", "feirante"],
        "Hortifruti e açougue": ["sacolao", "hortifruti", "acougue", "carnes",
                                 "frios", "queijo", "peixaria"],
        "Supermercado": ["supermercado*", "mercado", "favorito", "nordestao", "carrefour",
                         "atacadao", "assai", "pao de acucar", "extra", "eloi chaves"],
        "Restaurante": ["restaurante", "restau*", "lanchonete", "pizzaria", "burger*",
                        "burguer*", "hamburgueria", "camaroes", "ifood", "rappi", "delivery",
                        "gelateria", "sorveteria", "crepe*", "cafeteria", "churrascaria",
                        "brasasfood", "food", "bar"],
    },
    "SAÚDE": {
        "Drogaria": ["farmacia", "drogaria", "pague menos", "drogasil", "raia"],
        "Plano de saúde": ["unimed", "hapvida", "plano de saude", "amil", "sulamerica",
                           "bradesco saude"],
        "Academia": ["academia", "wellhub", "gympass", "crossfit", "pilates"],
        "Cuidados pessoais": ["barbearia", "salao", "cabeleireiro", "manicure", "estetica"],
        "Clínicas, consultas e exames": ["clinica", "clinic*", "medico", "dentista",
                                         "consultorio", "laboratorio", "hospital", "exame*",
                                         "imagem", "diagnostic*", "radiolog*", "psicolog*",
                                         "fisioterap*"],
    },
    "TRANSPORTE": {
        "Posto de gasolina": ["posto*", "combustivel", "gasolina", "etanol", "shell",
                              "ipiranga", "petrobras"],
        "Seguro": ["seguro*", "youse"],
        "Transporte por aplicativo": ["uber*", "99 ride", "99app", "99pop", "99 tecnologia",
                                      "indriver"],
        "Estacionamento": ["estacionamento*", "estapar", "zona azul"],
        "IPVA e multas": ["ipva", "sefaz", "detran", "licenciamento", "multa"],
    },
    "CASA": {
        "Água": ["agua", "caern"],
        "Luz": ["luz", "energia", "cosern", "neoenergia"],
        "Internet": ["internet", "fibra"],
        "Gás": ["gas"],
        "Condomínio": ["condominio"],
        "Aluguel": ["aluguel"],
        "Telefone e celular": ["claro", "vivo", "tim", "telefone", "celular", "oi movel"],
        "Manutenções": ["leroy merlin", "ferreira cost*", "manutencao", "reforma",
                        "material de construcao", "eletricista", "encanador"],
    },
    "SHOPPING": {
        "Compras online": ["amazon*", "mercadolivre", "mercado livre", "shopee", "shein",
                           "americanas", "magalu", "magazine"],
        "Vestuário": ["riachuelo", "renner", "c&a", "cea", "zara", "roupa", "sapato",
                      "vestuario", "hering"],
        "Infantil": ["baby", "infantil", "kids", "bebe"],
    },
    "LAZER": {
        "Viagens": ["gol linhas", "latam", "azul linhas", "zupper", "decolar", "hotel",
                    "airbnb", "booking", "viagem"],
        "Passeios e eventos": ["aquario", "cinema", "teatro", "show", "ingresso",
                               "futebol", "clube", "parque"],
    },
    "IMPOSTOS E TAXAS": {
        "Federais": ["darf", "gru", "receita federal", "rfb", "irpf", "irpj", "inss",
                     "simples nacional", "gps", "csll", "cofins", "pis"],
        "Estaduais": ["icms", "sefaz"],
        "Municipais": ["pref mun", "prefeitura", "iptu", "iss", "pmsp"],
        "Conselho profissional": ["conselho regional", "crc", "oab", "crea", "crm"],
    },
    "TARIFAS BANCÁRIAS": {
        "Anuidade do cartão": ["anuidade"],
        "Pacote de serviços": ["tarifa", "cesta", "pacote de servicos"],
        "IOF e juros": ["iof", "juros", "encargo"],
    },
}
SUBCATEGORIA_OUTROS = "Outros"
# Tipos de entrada reconhecidos pelas palavras; o resto é separado por quem pagou
TIPOS_ENTRADA = {
    "Salário": ["salario", "folha", "proventos", "pro labore", "prolabore", "vencimentos"],
    "Rendimentos": ["rendimento*", "dividendo*", "juros sobre capital", "jcp", "cupom"],
    "Estornos e reembolsos": ["estorno*", "reembolso*", "devolucao", "cashback"],
}
_PADROES_ENTRADA = {}
_CPF_ABERTO = re.compile(r"(?<![\d/.])\d{11}(?![\d/])")
# Seções da fatura do BB que já dizem a subcategoria
SECOES_FATURA_SUB = {"supermercados": "Supermercado", "restaurantes": "Restaurante"}

_PADROES_SUB = {cat: {sub: None for sub in subs} for cat, subs in SUBCATEGORIAS.items()}


def tipo_de_entrada(descricao):
    """Tipo de uma entrada: salário, rendimentos, estornos ou quem pagou (empresa/pessoa)."""
    if not _PADROES_ENTRADA:
        _PADROES_ENTRADA.update({k: _padrao(v) for k, v in TIPOS_ENTRADA.items()})
    texto = normalizar(sem_dados_bancarios(descricao))
    for tipo, padrao in _PADROES_ENTRADA.items():
        if padrao.search(texto):
            return tipo
    bruto = str(descricao)
    if eh_mei(bruto) or "•••" in bruto or _CPF_ABERTO.search(bruto):
        return "Recebido de pessoas"
    if _PADRAO_CNPJ.search(bruto):
        return "Recebido de empresas"
    return "Outras entradas"


def subcategorizar(categoria, descricao, fornecedor="", secao=""):
    """Subcategoria de uma saída, ou "" quando a categoria não tem subcategorias.

    Para entradas (RECEITA), devolve o tipo da entrada (ver tipo_de_entrada).
    """
    if categoria == CATEGORIA_ENTRADA:
        return tipo_de_entrada(descricao)
    if categoria == CATEGORIA_ESTORNO:
        return "Estornos e reembolsos"
    regras = SUBCATEGORIAS.get(categoria)
    if not regras:
        return ""
    texto = normalizar(f"{sem_dados_bancarios(descricao)} {fornecedor}")
    for sub, palavras in regras.items():
        if _PADROES_SUB[categoria][sub] is None:
            _PADROES_SUB[categoria][sub] = _padrao(palavras)
        if _PADROES_SUB[categoria][sub].search(texto):
            return sub
    # A seção da fatura do BB só decide quando nenhuma palavra-chave casou
    if categoria == "ALIMENTAÇÃO" and secao in SECOES_FATURA_SUB:
        return SECOES_FATURA_SUB[secao]
    return SUBCATEGORIA_OUTROS


# ============================================================================
# CNPJ E CNAE
# ============================================================================
_PADRAO_CNPJ = re.compile(r"\b(\d{2})\.?(\d{3})\.?(\d{3})/?(\d{4})-?(\d{2})\b")

# CNAE principal (Receita Federal) -> categoria e subcategoria. Casa pelo começo
# do código: o mais específico vence ("4721102" antes de "4721").
CNAE_CATEGORIAS = {
    "1091": ("ALIMENTAÇÃO", "Padaria"),
    "4721102": ("ALIMENTAÇÃO", "Padaria"),
    "4721104": ("ALIMENTAÇÃO", "Padaria"),          # doces, balas e semelhantes
    "4721103": ("ALIMENTAÇÃO", "Hortifruti e açougue"),  # laticínios e frios
    "4722": ("ALIMENTAÇÃO", "Hortifruti e açougue"),
    "4724": ("ALIMENTAÇÃO", "Hortifruti e açougue"),
    "4711": ("ALIMENTAÇÃO", "Supermercado"),
    "4712": ("ALIMENTAÇÃO", "Supermercado"),
    "4723": ("ALIMENTAÇÃO", "Supermercado"),
    "4729": ("ALIMENTAÇÃO", "Supermercado"),
    "5611": ("ALIMENTAÇÃO", "Restaurante"),
    "5612": ("ALIMENTAÇÃO", "Restaurante"),
    "5620": ("ALIMENTAÇÃO", "Restaurante"),
    "4771": ("SAÚDE", "Drogaria"),
    "6550": ("SAÚDE", "Plano de saúde"),
    "86": ("SAÚDE", "Clínicas, consultas e exames"),
    "9313": ("SAÚDE", "Academia"),
    "9602": ("SAÚDE", "Cuidados pessoais"),
    "4731": ("TRANSPORTE", "Posto de gasolina"),
    "5223": ("TRANSPORTE", "Estacionamento"),
    "4923": ("TRANSPORTE", "Transporte por aplicativo"),
    "35": ("CASA", "Luz"),
    "36": ("CASA", "Água"),
    "4784": ("CASA", "Gás"),
    "61": ("CASA", "Telefone e celular"),
    "6190": ("CASA", "Internet"),
    "8112": ("CASA", "Condomínio"),
    "6822": ("CASA", "Aluguel"),
    "4741": ("CASA", "Manutenções"),
    "4742": ("CASA", "Manutenções"),
    "4743": ("CASA", "Manutenções"),
    "4744": ("CASA", "Manutenções"),
    "43": ("CASA", "Manutenções"),
    "4781": ("SHOPPING", "Vestuário"),
    "4782": ("SHOPPING", "Vestuário"),
    "4713": ("SHOPPING", "Outros"),
    "4759": ("SHOPPING", "Outros"),
    "4763": ("SHOPPING", "Outros"),
    "4789": ("SHOPPING", "Outros"),
    "5510": ("LAZER", "Viagens"),
    "5590": ("LAZER", "Viagens"),
    "5111": ("LAZER", "Viagens"),
    "7911": ("LAZER", "Viagens"),
    "5914": ("LAZER", "Passeios e eventos"),
    "90": ("LAZER", "Passeios e eventos"),
    "91": ("LAZER", "Passeios e eventos"),
    "9329": ("LAZER", "Passeios e eventos"),
    "85": ("EDUCAÇÃO", ""),
    "4761": ("EDUCAÇÃO", ""),
    "6911": ("SERVIÇOS", ""),
    "6920": ("SERVIÇOS", ""),
    "70": ("SERVIÇOS", ""),
    "74": ("SERVIÇOS", ""),
    "82": ("SERVIÇOS", ""),
}


def extrair_cnpj(descricao):
    """CNPJ (só números) citado na descrição, ou None. CPF mascarado não casa."""
    m = _PADRAO_CNPJ.search(str(descricao))
    return "".join(m.groups()) if m else None


def categoria_do_cnae(cnae):
    """(categoria, subcategoria) para um CNAE, ou None se não houver mapeamento."""
    codigo = re.sub(r"\D", "", str(cnae or ""))
    for tamanho in range(len(codigo), 1, -1):
        if codigo[:tamanho] in CNAE_CATEGORIAS:
            return CNAE_CATEGORIAS[codigo[:tamanho]]
    return None


# ============================================================================
# REGRAS (o app lembra as correções por fornecedor)
# ============================================================================
COLUNAS_REGRAS = ["chave", "fornecedor", "categoria", "subcategoria", "origem", "cnpj",
                  "cnae", "atualizado_em"]


def chave_fornecedor(fornecedor):
    return normalizar(fornecedor)


def categorias_editaveis():
    """Categorias que o usuário pode escolher para uma saída."""
    return list(CATEGORIAS) + [CATEGORIA_PADRAO]


def subcategorias_de(categoria):
    subs = list(SUBCATEGORIAS.get(categoria, {}))
    return subs + [SUBCATEGORIA_OUTROS] if subs else [""]


def aplicar_regras(t, regras):
    """Aplica as regras às saídas do fornecedor (a regra vence as palavras-chave).

    Casa pelo nome do fornecedor sem acento/maiúsculas, também quando o nome no
    extrato vem cortado ou mais longo ("lc comercio" pega "lc comercio natal").
    """
    if regras is None or regras.empty or t.empty:
        return t
    t = t.copy()
    if "FornecedorOriginal" not in t:
        t["FornecedorOriginal"] = t["Fornecedor"]
    regras = regras.drop_duplicates("chave", keep="last")
    por_chave = {r.chave: r for r in regras.itertuples() if r.chave}
    chaves = sorted(por_chave, key=len, reverse=True)
    saidas = t["Tipo"] == "Saída"
    for i in t.index[saidas]:
        k = chave_fornecedor(t.at[i, "FornecedorOriginal"])
        regra = por_chave.get(k) or next(
            (por_chave[c] for c in chaves if len(c) >= 5 and (k.startswith(c) or c.startswith(k)
                                                              and len(k) >= 5)), None)
        if regra is None:
            continue
        t.at[i, "Categoria"] = regra.categoria
        if isinstance(regra.fornecedor, str) and regra.fornecedor.strip():
            # a regra também unifica o nome ("Lapv Consulting" e "Lapv Serv..." -> "Lapv")
            t.at[i, "Fornecedor"] = regra.fornecedor.strip()
        sub = regra.subcategoria if isinstance(regra.subcategoria, str) else ""
        validas = subcategorias_de(regra.categoria)
        t.at[i, "Subcategoria"] = sub if sub in validas else validas[-1] if validas != [""] else ""
    return t


_PADRAO_MEI = re.compile(r"(?:^|[-\s])\d{2}\.\d{3}\.\d{3}\s+[A-Za-zÀ-ú]")


def eh_mei(texto):
    """MEI tem o nome da pessoa no CNPJ ("49.463.039 MARIA DA SILVA")."""
    return bool(_PADRAO_MEI.search(str(texto)))


def eh_empresa(descricoes, contas):
    """O fornecedor é empresa? (compra no cartão, boleto/débito ou CNPJ na descrição).

    Pix para pessoas (inclusive MEI, que tem o nome da pessoa no CNPJ) não entra nas
    regras compartilhadas, para não expor nomes.
    """
    if any(eh_mei(d) for d in descricoes):
        return False
    for d, c in zip(descricoes, contas):
        n = normalizar(d)
        if "Cartão" in str(c) or extrair_cnpj(d) or n.startswith(("pagamento de boleto", "pagto",
                                                                   "pagamento de", "compra",
                                                                   "debito")):
            return True
    return False


# ============================================================================
# FORNECEDORES
# ============================================================================
# Partes da descrição que dizem o tipo do lançamento, não quem recebeu
_PARTES_GENERICAS = ("transferencia", "pix", "ted", "doc", "pagamento", "pagto", "pgto",
                     "compra", "debito", "credito", "recebid", "enviad", "tarifa",
                     "cobranca", "boleto")
# Prefixos de maquininha/intermediador: "MP*LOJA", "PG *LOJA", "DL*99 RIDE"
_PREFIXO_INTERMEDIADOR = re.compile(r"^[A-Z0-9]{1,4}\s?\*\s*", re.IGNORECASE)


def _limpar_nome(nome):
    nome = re.sub(r"\([^)]*\)", " ", nome)                       # "(Transferência enviada)"
    nome = re.sub(r"\b\d{2}/\d{2}(/\d{2,4})?\b|\b\d{2}:\d{2}\b", " ", nome)  # datas e horas
    nome = re.sub(r"[\d.\-/•*]{5,}", " ", nome)                   # CPF, CNPJ, nº documento
    nome = re.sub(r"\s+", " ", nome).strip(" -*.,")
    return nome


def extrair_fornecedor(descricao):
    """Quem recebeu o dinheiro, a partir da descrição do extrato.

    "Transferência enviada pelo Pix - PADARIA X - 12.345.678/0001-90 - BANCO" -> "Padaria X"
    "Pagto Energia Elétrica - COSERN - DÉBITO ENERGIA" -> "Cosern"
    """
    partes = [p.strip() for p in str(descricao).split(" - ") if p.strip()]
    for parte in partes:
        if normalizar(parte).startswith(_PARTES_GENERICAS):
            continue
        nome = _limpar_nome(parte)
        nome = _PREFIXO_INTERMEDIADOR.sub("", nome).strip()   # "MP*LOJA" -> "LOJA"
        if len(nome) >= 2:
            return _apresentar(nome)
    return _apresentar(_limpar_nome(partes[0]) if partes else "") or "Não identificado"


def fornecedor_da_fatura(descricao_bruta):
    """Na fatura do BB, o nome da loja ocupa as primeiras 22 posições (13 nas parcelas)."""
    if re.search(r"\bPARC \d{2}/\d{2}\b", descricao_bruta):
        nome = descricao_bruta.split(" PARC ")[0]
    else:
        nome = descricao_bruta[:22]
    nome = nome.strip()
    if _PREFIXO_INTERMEDIADOR.match(nome):
        nome = _PREFIXO_INTERMEDIADOR.sub("", nome)
    elif "*" in nome:
        nome = nome.split("*")[0]  # "AMAZONMKTPLC*AMANASTOR" -> "AMAZONMKTPLC"
    return _apresentar(_limpar_nome(nome)) or "Não identificado"


# Mesma empresa com nomes diferentes no extrato e na fatura (começo do nome, sem acento)
APELIDOS_FORNECEDOR = [
    ("amazon", "Amazon"), ("mercadoli", "Mercado Livre"), ("mercado livre", "Mercado Livre"),
    ("lojas riachue", "Riachuelo"), ("riachuelo", "Riachuelo"), ("neoenergia cosern", "Cosern"),
    ("leroy merlin", "Leroy Merlin"), ("lojas americanas", "Lojas Americanas"),
    ("ifood", "iFood"), ("uber", "Uber"), ("99 ride", "99"), ("netflix", "Netflix"),
    ("spotify", "Spotify"), ("pague menos", "Pague Menos"),
]


def _apresentar(nome):
    if not nome:
        return ""
    nome = re.sub(r"(\s+\d+)+$", "", nome)  # número da filial: "LOJAS AMERICANAS 647"
    chave = normalizar(nome)
    for prefixo, apelido in APELIDOS_FORNECEDOR:
        if chave.startswith(prefixo):
            return apelido
    return " ".join(p if "/" in p else p.capitalize() for p in nome.split())


def resumo_subcategorias(t, categoria, tipo="Saída"):
    """Total por subcategoria dentro de uma categoria ("Outros" por último)."""
    saidas = t[(t["Tipo"] == tipo) & (t["Categoria"] == categoria)]
    if saidas.empty or "Subcategoria" not in saidas or saidas["Subcategoria"].eq("").all():
        return pd.DataFrame(columns=["Subcategoria", "Total", "Transações", "% da categoria"])
    tabela = (saidas.groupby("Subcategoria")["Valor"].agg(["sum", "count"])
              .rename(columns={"sum": "Total", "count": "Transações"}))
    tabela["Total"] = tabela["Total"].abs()
    tabela["% da categoria"] = tabela["Total"] / tabela["Total"].sum() * 100
    tabela["_outros"] = tabela.index == SUBCATEGORIA_OUTROS
    tabela = tabela.sort_values(["_outros", "Total"], ascending=[True, False])
    return tabela.drop(columns="_outros").reset_index()


def resumo_fornecedores(t, categoria, n=10, subcategoria=None, tipo="Saída"):
    """Os n fornecedores que mais receberam numa categoria (ou subcategoria); o resto
    em "Outros fornecedores". O % é sobre o total do grupo detalhado.

    Com tipo="Entrada", são as fontes (quem pagou) em vez dos fornecedores.
    """
    filtro = (t["Tipo"] == tipo) & (t["Categoria"] == categoria)
    if subcategoria is not None:
        filtro &= t["Subcategoria"] == subcategoria
    saidas = t[filtro].copy()
    if saidas.empty:
        return pd.DataFrame(columns=["Fornecedor", "Total", "Transações", "% da categoria"])
    # Agrupa ignorando maiúsculas e acentos ("VIVIANE" = "Viviane")
    saidas["_chave"] = saidas["Fornecedor"].map(normalizar)
    # Nome cortado é a mesma empresa: "ferreira cost" -> "ferreira costa"
    chaves = sorted(saidas["_chave"].unique(), key=len, reverse=True)
    junta = {}
    for curta in chaves:
        longa = next((c for c in chaves if len(c) > len(curta) >= 6 and c.startswith(curta)), None)
        if longa:
            junta[curta] = junta.get(longa, longa)
    saidas["_chave"] = saidas["_chave"].replace(junta)
    nome = saidas.groupby("_chave")["Fornecedor"].agg(lambda s: max(s.unique(), key=len))
    tabela = (saidas.groupby("_chave")["Valor"].agg(["sum", "count"])
              .rename(columns={"sum": "Total", "count": "Transações"}))
    tabela["Total"] = tabela["Total"].abs()
    tabela["Fornecedor"] = nome
    tabela = tabela.sort_values("Total", ascending=False)
    if len(tabela) > n:
        resto = tabela.iloc[n:]
        rotulo = "Outros fornecedores" if tipo == "Saída" else "Outras fontes"
        outros = pd.DataFrame([{"Fornecedor": f"{rotulo} ({len(resto)})",
                                "Total": resto["Total"].sum(),
                                "Transações": resto["Transações"].sum()}])
        tabela = pd.concat([tabela.iloc[:n], outros])
    total = tabela["Total"].sum()
    tabela["% da categoria"] = tabela["Total"] / total * 100 if total else 0
    return tabela[["Fornecedor", "Total", "Transações", "% da categoria"]].reset_index(drop=True)


# ============================================================================
# FATURA DE CARTÃO DO BB (PDF)
# ============================================================================
# Seções da fatura do BB que já dizem a categoria
SECOES_FATURA_BB = {
    "supermercados": "ALIMENTAÇÃO",
    "restaurantes": "ALIMENTAÇÃO",
    "transporte": "TRANSPORTE",
    "vestuario": "SHOPPING",
    "viagens": "LAZER",
    "lazer": "LAZER",
    "saude": "SAÚDE",
    "educacao": "EDUCAÇÃO",
}
_LINHA_FATURA = re.compile(r"^(\d{2})/(\d{2})\s+(.+?)\s+R\$\s*(-?[\d.]+,\d{2})\s*$")


def eh_fatura_bb(texto):
    return "Lançamentos nesta fatura" in texto and "Total da Fatura" in texto


def ler_fatura_bb(texto, conta="BB Cartão"):
    """Lê as compras de uma fatura do cartão BB (texto extraído do PDF).

    O pagamento da fatura anterior é ignorado (já aparece na conta corrente).
    Parcelas usam a data de fechamento da fatura, porque a data impressa é a
    da compra original, que pode ser de meses atrás.
    """
    fechamento = re.search(r"Fatura fechada em (\d{2}/\d{2}/\d{4})", texto)
    if not fechamento:
        raise ValueError("Fatura sem data de fechamento.")
    data_fech = pd.to_datetime(fechamento.group(1), format="%d/%m/%Y")

    inicio = texto.find("Lançamentos nesta fatura")
    fim = texto.find("Total da Fatura", inicio)
    secao = None
    linhas = []
    for linha in texto[inicio:fim].splitlines():
        conteudo = linha.strip()
        m = _LINHA_FATURA.match(conteudo)
        if not m:
            if conteudo and len(conteudo) < 40 and not re.search(r"\d", conteudo):
                secao = normalizar(conteudo)  # cabeçalho de seção ("Supermercados")
            continue
        dia, mes, descricao, valor = m.groups()
        if normalizar(descricao).startswith("pgto"):
            continue  # pagamento da fatura anterior
        valor = -converter_valor(valor)  # compra = saída (negativo); estorno = positivo
        ano = data_fech.year if (int(mes), int(dia)) <= (data_fech.month, data_fech.day) \
            else data_fech.year - 1
        data_impressa = pd.Timestamp(year=ano, month=int(mes), day=int(dia))
        parc = re.search(r"\bPARC (\d{2})/(\d{2})\b", descricao)
        # Parcela: a data impressa é a da compra original; o gasto entra no fechamento
        data = data_fech if parc else data_impressa
        fornecedor = fornecedor_da_fatura(descricao)
        descricao = re.sub(r"\s+BR$", "", re.sub(r"\s+", " ", descricao))
        if valor > 0:
            categoria = CATEGORIA_ESTORNO
        else:
            # Palavras-chave primeiro: a seção da fatura às vezes erra
            # (sacolão em "Saúde"); ela só decide quando nada foi reconhecido
            categoria = categorizar(descricao, valor)
            if categoria in (CATEGORIA_PADRAO, "TRANSFERÊNCIA"):
                categoria = SECOES_FATURA_BB.get(secao, CATEGORIA_PADRAO)
        linhas.append({"Data": data, "Descrição": descricao, "Valor": valor,
                       "Categoria": categoria, "Fornecedor": fornecedor,
                       "_secao": secao or "",
                       "Parcela": int(parc.group(1)) if parc else None,
                       "Parcelas": int(parc.group(2)) if parc else None,
                       "DataCompra": data_impressa if parc else pd.NaT})

    t = pd.DataFrame(linhas, columns=["Data", "Descrição", "Valor", "Categoria", "Fornecedor",
                                      "_secao", "Parcela", "Parcelas", "DataCompra"])
    t["Tipo"] = ["Entrada" if v > 0 else "Saída" for v in t["Valor"]]
    t["Conta"] = conta
    t["Mês"] = t["Data"].dt.to_period("M").astype(str)
    return t


# ============================================================================
# OFX (qualquer banco) E EXTRATO NUBANK EM PDF
# ============================================================================
BANCOS_OFX = {"1": "BB", "260": "Nubank", "341": "Itaú", "237": "Bradesco", "104": "Caixa",
              "33": "Santander", "77": "Inter", "336": "C6", "748": "Sicredi", "756": "Sicoob"}


def _tag_ofx(bloco, tag):
    """Valor de <TAG> no OFX (funciona no formato SGML, sem fechamento, e no XML)."""
    m = re.search(rf"<{tag}>([^<\r\n]*)", bloco, re.IGNORECASE)
    return m.group(1).strip() if m else ""


def ler_ofx(conteudo):
    """Lançamentos de um arquivo OFX (Money/Quicken), formato aceito pela maioria dos bancos."""
    try:
        texto = conteudo.decode("utf-8")
    except UnicodeDecodeError:
        texto = conteudo.decode("latin-1")
    banco_id = (_tag_ofx(texto, "BANKID") or _tag_ofx(texto, "FID")).lstrip("0")
    banco = BANCOS_OFX.get(banco_id) or _tag_ofx(texto, "ORG").title() or "OFX"
    cartao = "<CCSTMTRS>" in texto.upper()
    if banco == "BB":
        conta = "BB Cartão" if cartao else "BB Conta"
    else:
        conta = f"{banco} Cartão" if cartao else banco

    linhas = []
    for bloco in re.findall(r"<STMTTRN>(.*?)(?=</STMTTRN>|<STMTTRN>|</BANKTRANLIST>)", texto,
                            re.IGNORECASE | re.DOTALL):
        data = _tag_ofx(bloco, "DTPOSTED")[:8]
        valor = _tag_ofx(bloco, "TRNAMT").replace(",", ".")
        descricao = _tag_ofx(bloco, "MEMO") or _tag_ofx(bloco, "NAME")
        if len(data) == 8 and valor:
            linhas.append({"Data": f"{data[:4]}-{data[4:6]}-{data[6:]}", "Descrição": descricao,
                           "Valor": valor})
    if not linhas:
        raise ValueError("nenhum lançamento encontrado no OFX.")
    bruto = pd.DataFrame(linhas)
    t = montar_transacoes(bruto, {"data": "Data", "descricao": ["Descrição"], "valor": "Valor"},
                          conta)
    if cartao:  # no cartão, valor positivo no OFX é estorno, não receita
        t.loc[t["Valor"] > 0, "Categoria"] = CATEGORIA_ESTORNO
    return t


_MESES_PDF = {"JAN": 1, "FEV": 2, "MAR": 3, "ABR": 4, "MAI": 5, "JUN": 6, "JUL": 7,
              "AGO": 8, "SET": 9, "OUT": 10, "NOV": 11, "DEZ": 12}
_DIA_NUBANK = re.compile(r"^(\d{2}) (JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ) (\d{4})"
                         r"\s+(Total de (?:entradas|saídas).*)$")
_GRUPO_NUBANK = re.compile(r"^Total de (entradas|saídas)\b")
_VALOR_NUBANK = re.compile(r"^[\d.]+,\d{2}$")
_VALOR_NO_FIM = re.compile(r"^(.*\S)\s+(?:- )?([\d.]+,\d{2})$")
_INICIO_NUBANK = re.compile(r"^(Transfer|Pagamento|Pagto|Compra|Aplicação|Resgate|Valor Mensal|"
                            r"Plano|Crédito|Débito|Estorno|Reembolso|Rendimento|Depósito|"
                            r"Saque|Tarifa|IOF|Juros|Boleto|Pix)", re.IGNORECASE)
# O PDF não tem o " - " depois do tipo de lançamento que o CSV tem; recoloca para a
# descrição (e o fornecedor) ficarem iguais aos do CSV
_TIPO_NUBANK = re.compile(r"^(Transferência (?:enviada|recebida) pelo Pix|Transferência Recebida|"
                          r"Transferência enviada|Pagamento de boleto efetuado|"
                          r"Compra no débito|Pagamento da fatura|Estorno|Reembolso)\s+(?!-)")


def eh_extrato_nubank(texto):
    return "Movimentações" in texto and "nubank.com.br" in texto and "Total de" in texto


def ler_extrato_nubank_pdf(texto):
    """Lançamentos do extrato da conta Nubank em PDF."""
    linhas_pdf = texto.splitlines()
    try:
        inicio = next(i for i, l in enumerate(linhas_pdf) if l.strip() == "Movimentações") + 1
    except StopIteration:
        raise ValueError("extrato Nubank sem a seção Movimentações.")

    lancamentos, buffer = [], []
    data, sinal, pulando, apos_quebra = None, -1, False, False
    for linha in linhas_pdf[inicio:]:
        l = linha.strip()
        if l.startswith("O saldo líquido corresponde"):
            break  # fim das movimentações (avisos legais da última página)
        # rodapé e cabeçalho de página: de "Tem alguma dúvida?" até "VALORES EM R$"
        if l.startswith("Tem alguma dúvida"):
            pulando = True
        if pulando:
            if "VALORES EM R$" in l:
                pulando, apos_quebra = False, True
            continue
        if not l or re.fullmatch(r"\d+ de \d+", l) or l.startswith("Extrato gerado"):
            continue
        m = _DIA_NUBANK.match(l)
        if m:
            data = pd.Timestamp(int(m.group(3)), _MESES_PDF[m.group(2)], int(m.group(1)))
            l = m.group(4)
        g = _GRUPO_NUBANK.match(l)
        if g:
            sinal = 1 if g.group(1) == "entradas" else -1
            buffer, apos_quebra = [], False
            continue
        # Descrição que continua depois da quebra de página (o valor já foi lido):
        # não começa com um tipo de lançamento, então pertence ao lançamento anterior
        if apos_quebra and not buffer and lancamentos and not _INICIO_NUBANK.match(l) \
                and not _VALOR_NUBANK.match(l) and not _DIA_NUBANK.match(l) \
                and not _GRUPO_NUBANK.match(l):
            lancamentos[-1]["Descrição"] += " " + l
            continue
        apos_quebra = False
        # O valor vem sozinho na linha ou no fim da última linha da descrição
        if _VALOR_NUBANK.match(l):
            valor = l
        else:
            fim = _VALOR_NO_FIM.match(l)
            valor = fim.group(2) if fim else None
            buffer.append(fim.group(1) if fim else l)
        if valor and buffer and data is not None:
            lancamentos.append({"Data": data.strftime("%Y-%m-%d"), "Descrição": " ".join(buffer),
                                "Valor": sinal * converter_valor(valor)})
            buffer = []

    if not lancamentos:
        raise ValueError("nenhum lançamento encontrado no extrato Nubank.")
    for x in lancamentos:
        x["Descrição"] = _TIPO_NUBANK.sub(r"\1 - ", re.sub(r"\s+", " ", x["Descrição"]).strip())
    bruto = pd.DataFrame(lancamentos)
    return montar_transacoes(bruto, {"data": "Data", "descricao": ["Descrição"],
                                     "valor": "Valor"}, "Nubank")


# ============================================================================
# CARREGAMENTO DE VÁRIOS ARQUIVOS
# ============================================================================
def identificar_conta(nome, colunas=()):
    """Nome da conta a partir do nome do arquivo ou do formato do extrato."""
    n = normalizar(nome)
    if n.startswith("nu_") or "nubank" in n:
        return "Nubank"
    if {"Lançamento", "Detalhes"} <= set(colunas):
        return "BB Conta"
    if re.search(r"\b(bb|banco do brasil)\b", n):
        return "BB Conta"
    base = re.sub(r"\.[a-z]+$", "", nome)
    return re.sub(r"[\d_\-\s]+$", "", base).strip() or base


LIMITE_ZIP_ARQUIVOS = 300
LIMITE_ZIP_BYTES = 100 * 1024 * 1024   # 100 MB descompactados


def expandir_zip(conteudo):
    """Arquivos de dentro de um .zip, como [(nome, bytes)]. Tudo em memória.

    Ignora pastas, arquivos ocultos e o lixo do macOS (__MACOSX). Recusa zip com
    senha e zip que descompactado passaria dos limites (proteção contra "zip-bomba").
    """
    try:
        z = zipfile.ZipFile(io.BytesIO(conteudo))
    except zipfile.BadZipFile:
        raise ValueError("o arquivo .zip está corrompido.")
    with z:
        membros = [m for m in z.infolist()
                   if not m.is_dir() and not m.filename.startswith("__MACOSX/")
                   and not PurePosixPath(m.filename).name.startswith(".")]
        if len(membros) > LIMITE_ZIP_ARQUIVOS:
            raise ValueError(f"o .zip tem mais de {LIMITE_ZIP_ARQUIVOS} arquivos.")
        if sum(m.file_size for m in membros) > LIMITE_ZIP_BYTES:
            raise ValueError("o .zip descompactado passa de 100 MB.")
        if any(m.flag_bits & 0x1 for m in membros):
            raise ValueError("o .zip está protegido por senha.")
        return [(PurePosixPath(m.filename).name, z.read(m)) for m in membros]


def carregar_arquivo(nome, conteudo):
    """Lê um arquivo enviado.

    Retorna {"tipo": "tabela", "bruto": DataFrame, "conta": str} para extratos
    em CSV/Excel (as colunas ainda precisam ser identificadas) ou
    {"tipo": "transacoes", "dados": DataFrame, "conta": str} para faturas em PDF.
    """
    ext = nome.lower().rsplit(".", 1)[-1]
    if ext == "ofx" or conteudo.lstrip()[:9] == b"OFXHEADER" or b"<OFX>" in conteudo[:2000]:
        dados = ler_ofx(conteudo)
        conta = dados["Conta"].iat[0] if not dados.empty else "OFX"
        return {"tipo": "transacoes", "dados": dados, "conta": conta}
    if ext == "pdf" or conteudo[:4] == b"%PDF":
        texto = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(conteudo)).pages)
        if eh_fatura_bb(texto):
            return {"tipo": "transacoes", "dados": ler_fatura_bb(texto), "conta": "BB Cartão"}
        if eh_extrato_nubank(texto):
            return {"tipo": "transacoes", "dados": ler_extrato_nubank_pdf(texto),
                    "conta": "Nubank"}
        raise ValueError("PDF não reconhecido (são lidos a fatura do cartão BB e o extrato da "
                         "conta Nubank); envie o extrato em CSV, Excel ou OFX.")
    bruto = ler_extrato(nome, conteudo)
    return {"tipo": "tabela", "bruto": bruto, "conta": identificar_conta(nome, bruto.columns)}


def _marcar_transferencias_entre_contas(t, dias=2):
    """Pix/TED que sai de uma conta sua e entra em outra conta sua no mesmo valor."""
    candidatas = t[(t["Tipo"] != "Interna")
                   & ~t["Conta"].str.contains("Cartão")
                   & t["Descrição"].map(lambda d: bool(PADRAO_TRANSFERENCIA.search(normalizar(d))))]
    saidas = candidatas[candidatas["Valor"] < 0].sort_values("Data")
    entradas = candidatas[candidatas["Valor"] > 0]
    usadas = set()
    for i, s in saidas.iterrows():
        par = entradas[(entradas["Conta"] != s["Conta"])
                       & (entradas["Valor"].round(2) == round(-s["Valor"], 2))
                       & ((entradas["Data"] - s["Data"]).abs() <= pd.Timedelta(days=dias))
                       & ~entradas.index.isin(usadas)]
        if len(par):
            j = (par["Data"] - s["Data"]).abs().idxmin()
            usadas.add(j)
            t.loc[[i, j], "Categoria"] = CATEGORIA_ENTRE_CONTAS
            t.loc[[i, j], "Tipo"] = "Interna"
    return t


def _marcar_transferencias_para_si(t, nome_titular):
    """Pix/TED para (ou de) você mesmo em outro banco: dinheiro continua seu."""
    nome = normalizar(nome_titular)
    if len(nome.split()) < 2:
        return t  # exige nome e sobrenome para não pegar homônimos
    alvo = (t["Tipo"] != "Interna") & ~t["Conta"].str.contains("Cartão") & t["Descrição"].map(
        lambda d: nome in normalizar(d) and bool(PADRAO_TRANSFERENCIA.search(normalizar(d))))
    t.loc[alvo, "Categoria"] = CATEGORIA_ENTRE_CONTAS
    t.loc[alvo, "Tipo"] = "Interna"
    return t


def consolidar(tabelas, nome_titular=""):
    """Junta as transações de vários arquivos e trata o que envolve mais de uma conta.

    - Remove duplicatas entre arquivos (o mesmo período enviado duas vezes)
    - Marca transferências entre as suas contas como movimentação interna
    - Pix/TED para o próprio titular (nome informado) também é interno
    - Pagamento de fatura só é interno se a fatura do cartão foi enviada;
      senão ele é a única informação do gasto e conta como saída
    """
    tabelas = [x for x in tabelas if not x.empty]
    if not tabelas:
        return pd.DataFrame(columns=["Data", "Descrição", "Valor", "Tipo", "Categoria",
                                     "Subcategoria", "Fornecedor", "Conta", "Mês"])
    for n, x in enumerate(tabelas):
        x["_arquivo"] = n
    t = pd.concat(tabelas, ignore_index=True)

    # Duplicatas: o mesmo período enviado de novo, ou em outro formato (CSV, OFX e PDF
    # do mesmo mês). Arquivos de uma mesma conta com datas em comum são o mesmo extrato,
    # então a chave ignora a descrição (que muda de formato para formato). Mantém, para
    # cada lançamento, a maior quantidade vista num mesmo arquivo, e a versão do
    # primeiro arquivo (a tela manda CSV/Excel primeiro, que tem a melhor descrição).
    chave = ["Conta", "Data", "Valor"]
    t["_ocorrencia"] = t.groupby(chave + ["_arquivo"]).cumcount()
    t = t.drop_duplicates(subset=chave + ["_ocorrencia"]).drop(columns=["_arquivo", "_ocorrencia"])
    t = t.reset_index(drop=True)

    if not t["Conta"].str.contains("Cartão").any():
        fatura = t["Categoria"] == CATEGORIA_FATURA
        t.loc[fatura, "Categoria"] = CATEGORIA_CARTAO
        t.loc[fatura, "Tipo"] = [definir_tipo(CATEGORIA_CARTAO, v) for v in t.loc[fatura, "Valor"]]

    t = _marcar_transferencias_entre_contas(t)
    if nome_titular:
        t = _marcar_transferencias_para_si(t, nome_titular)
    if "Fornecedor" not in t:
        t["Fornecedor"] = t["Descrição"].map(extrair_fornecedor)
    t["Fornecedor"] = t["Fornecedor"].fillna(t["Descrição"].map(extrair_fornecedor))
    secoes = t["_secao"].fillna("") if "_secao" in t else pd.Series("", index=t.index)
    t["Subcategoria"] = [subcategorizar(c, d, f, sec) for c, d, f, sec in
                         zip(t["Categoria"], t["Descrição"], t["Fornecedor"], secoes)]
    t = t.drop(columns=["_secao"], errors="ignore")
    return t.sort_values(["Data", "Conta"]).reset_index(drop=True)


def formatar_reais(v):
    """1234.5 -> 'R$ 1.234,50'"""
    sinal = "-" if v < 0 else ""
    texto = f"{abs(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{sinal}R$ {texto}"
