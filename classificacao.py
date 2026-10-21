"""
Preenche Débito, Crédito e Histórico da planilha base.

- Saída do banco (despesa): débito na conta do fornecedor, crédito na conta do banco.
- Entrada no banco (receita): débito na conta do banco, crédito na conta do cliente.

A conta do fornecedor ou cliente vem da planilha matriz (descrição -> conta contábil),
procurando o nome que aparece no extrato. A conta de cada banco vem da tabela de contas
dos bancos (Banco/Conta da planilha base -> código). O que não dá para decidir fica em
branco, com o motivo na coluna "Conferir".
"""

import io
import re
import unicodedata

import pandas as pd

HIST_SAIDA = "VR RF A SAIDA DO BANCO - "
HIST_ENTRADA = "VR RF A ENTRADA NO BANCO - "
NAO_IMPORTAR = "não importar"

# Palavras que não ajudam a reconhecer o fornecedor
_VAZIAS = {"ltda", "me", "mei", "epp", "eireli", "sa", "s", "a", "de", "da", "do", "das", "dos",
           "e", "em", "cia", "comercio", "servicos", "filial", "nf", "pgto", "pg", "ref"}

# O que vem antes do nome do fornecedor/cliente na descrição da matriz
_PREFIXOS_MATRIZ = re.compile(
    r"^(?:dep[oó]sito \w+ \([^)]*\)? ?-|adiantamento reserva -|pix recep[cç][aã]o -|"
    r"cch -|bnb - nf -|bnb -|nf -|transf entre contas -> sunbay \|)\s*", re.IGNORECASE)

# O que vem antes do nome da contraparte na descrição do extrato
_PREFIXOS_EXTRATO = re.compile(
    r"^(?:.*? - )?(?:rem:|remet\.|dest\.|fav:?)\s*|"
    r"^(?:pix enviado|pix recebido|boleto pago|pagto eletron cobranca|sispag|"
    r"transf cc para cc pj -|ted-credito em conta - \d+ \d+ \d+|ted [\d.]+)\s*", re.IGNORECASE)

# Lançamentos de banco com nome genérico: o texto do extrato e o nome procurado na matriz
_APELIDOS = [
    (r"^pix qrs\b", "pix recepcao"),
    (r"^rede\s+(visa|mast|elo|amex|hiper)", "rede"),
    (r"^(cielo|getnet|stone)\b", r"\1"),
    (r"^(tarifa|tar)\b", "tarifa bancaria"),
    (r"^iof\b", "iof"),
]

# Aplicação e resgate na conta corrente: a outra conta é a aplicação do mesmo banco
_APLICACAO = re.compile(r"^(apl|aplic|res aplic|resgate|rend pago|rentab|resg)\b", re.IGNORECASE)
_APLICACAO_ESPELHO = {"Aplicação", "Resgate"}   # linhas da conta de aplicação


def _sem_acento(texto):
    t = unicodedata.normalize("NFKD", str(texto))
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


def _tokens(texto):
    palavras = re.findall(r"[a-z0-9]+", _sem_acento(texto))
    return [p for p in palavras if p not in _VAZIAS and not p.isdigit()]


def ler_matriz(arquivo):
    """Planilha matriz -> DataFrame com Descrição, Conta, Nome (tokens do nome)."""
    bruto = pd.read_excel(arquivo, header=None, dtype=str).iloc[:, :2]
    bruto.columns = ["Descrição", "Conta"]
    bruto = bruto.dropna(subset=["Descrição"])
    bruto["Descrição"] = bruto["Descrição"].str.strip()
    bruto["Conta"] = bruto["Conta"].fillna("").str.strip().str.replace(r"\.0$", "", regex=True)
    cabecalho = bruto["Conta"].str.lower().str.startswith("conta cont")
    m = bruto[~cabecalho & (bruto["Conta"] != "")].copy()
    m["Nome"] = m["Descrição"].map(_nome_na_matriz)
    m["Recebimento"] = m["Descrição"].str.match(_RECEBIMENTO)
    return m.reset_index(drop=True)


def parece_matriz(nome, conteudo):
    """O arquivo é a planilha matriz? Pelo nome ou pelo cabeçalho "Conta Contábil"."""
    nome = _sem_acento(nome)
    if not nome.endswith((".xlsx", ".xls")):
        return False
    if "matriz" in nome:
        return True
    try:
        topo = pd.read_excel(io.BytesIO(conteudo), header=None, dtype=str, nrows=10)
    except Exception:
        return False
    return topo.shape[1] >= 2 and topo.iloc[:, 1].fillna("").map(_sem_acento) \
        .str.startswith("conta cont").any()


def parece_planilha_base(nome, conteudo):
    """O arquivo é uma planilha base baixada deste app (não é extrato nem matriz)?"""
    if not _sem_acento(nome).endswith((".xlsx", ".xls")):
        return False
    try:
        topo = pd.read_excel(io.BytesIO(conteudo), header=None, dtype=str, nrows=1)
    except Exception:
        return False
    return {"Banco/Conta", "Débito", "Crédito", "Histórico"} <= set(topo.iloc[0].fillna(""))


# Linhas da matriz que são de dinheiro entrando (cliente pagando, depósito, cartão)
_RECEBIMENTO = re.compile(r"(?i)^(dep[oó]sito|pix recep|credito\b|adiantamento reserva|"
                          r"recupera)")

# Primeira parte que não é nome de ninguém ("CREDITO - REDE": o nome é REDE)
_GENERICOS = {"credito", "debito", "pagamento", "deposito", "recebimento", "banco", "bradesco",
              "itau", "bb", "bnb", "santander", "caixa", "sicredi"}


def _nome_na_matriz(descricao):
    d = _PREFIXOS_MATRIZ.sub("", descricao.strip())
    # "FORNECEDOR - CATEGORIA": o nome é a primeira parte que não é genérica
    partes = [x for x in d.split(" - ") if _tokens(x)]
    for parte in partes:
        nome = tuple(t for t in _tokens(parte) if not t.isdigit())
        if nome and not set(nome) <= _GENERICOS:
            return nome
    return tuple(_tokens(d))


def _nomes_no_extrato(descricao):
    """Nomes a procurar na matriz, do mais provável ao menos. O nome costuma ser a última
    parte ("TRANSFERENCIA PIX - REM: FULANO"); nas planilhas do cliente o histórico junta
    várias colunas ("CATEGORIA - FORNECEDOR - CNPJ"), e aí vale tentar cada parte."""
    d = re.sub(r"\s*\d{2}/\d{2}$", "", descricao.strip())     # data do Pix no fim
    d = re.sub(r"\s+\d{2,3}/\d{2,3}$", "", d)                   # parcela do cartão
    for padrao, apelido in _APELIDOS:
        m = re.search(padrao, _sem_acento(d))
        if m:
            return [tuple(_tokens(m.expand(apelido)))]
    partes = d.split(" - ")
    nomes = [tuple(_tokens(_PREFIXOS_EXTRATO.sub("", p))) for p in reversed(partes)]
    if len(partes) > 1:
        nomes.append(tuple(_tokens(d)))
    return [n for n in dict.fromkeys(nomes) if n]


def _nome_no_extrato(descricao):
    nomes = _nomes_no_extrato(descricao)
    return nomes[0] if nomes else ()


def _casa(a, b, cortado):
    """Palavra do extrato casa com a da matriz. Só a última palavra de um nome pode estar
    cortada ("ORINTER VIAGENS E TUR"), e aí basta ser o começo da outra."""
    if a == b:
        return True
    return cortado and min(len(a), len(b)) >= 3 and (a.startswith(b) or b.startswith(a))


def _pontua(extrato, matriz):
    if not extrato or not matriz:
        return 0.0
    junto_e, junto_m = "".join(extrato), "".join(matriz)
    if min(len(junto_e), len(junto_m)) >= 6 and (junto_e.startswith(junto_m)
                                                  or junto_m.startswith(junto_e)):
        return 1.0   # "DIGITALOCEAN" x "DIGITAL OCEAN"
    if not _casa(extrato[0], matriz[0], len(extrato) == 1 or len(matriz) == 1):
        return 0.0
    curto, longo = (extrato, matriz) if len(extrato) <= len(matriz) else (matriz, extrato)
    iguais = sum(any(_casa(p, q, i == len(curto) - 1 or j == len(longo) - 1)
                     for j, q in enumerate(longo)) for i, p in enumerate(curto))
    # Nome cortado no extrato ("ORINTER VIAGENS E TUR" x "ORINTER TOUR E TRAVEL"): a
    # primeira palavra, se for longa e igual, já identifica
    primeira = 0.75 if extrato[0] == matriz[0] and len(extrato[0]) >= 5 else 0
    return max(iguais / len(curto), primeira)


def procurar_na_matriz(descricao, matriz, minimo=0.75, entrada=None):
    """(conta, descrição da matriz, aviso) para a descrição do extrato.

    entrada: True/False para preferir as linhas da matriz do mesmo sentido (o mesmo cliente
    aparece como "Depósito ... - EUROPLUS" e o fornecedor como "EUROPLUS - ...")."""
    nomes = _nomes_no_extrato(descricao)
    if not nomes:
        return None, "", "sem nome do fornecedor no extrato"
    for k, nome in enumerate(nomes):
        notas = [(_pontua(nome, n), i) for i, n in enumerate(matriz["Nome"])]
        maior = max((s for s, _ in notas), default=0)
        # As outras partes só valem se casarem por inteiro ("TRANSF CC PARA CC PJ" não é
        # "TRANSF ENTRE CONTAS")
        if maior >= (minimo if k == 0 else 1.0):
            break
    else:
        return None, "", "não achei na matriz"
    melhores = [i for s, i in notas if s == maior]   # só os que casaram melhor votam
    iguais = [i for i in melhores if matriz.at[i, "Nome"] == nome]
    melhores = iguais or melhores
    if entrada is not None and "Recebimento" in matriz:
        mesmo_sentido = [i for i in melhores if bool(matriz.at[i, "Recebimento"]) == entrada]
        melhores = mesmo_sentido or melhores
    contas = matriz.loc[melhores, "Conta"]
    votos = contas.value_counts()
    conta = votos.index[0]
    exemplo = matriz.loc[contas[contas == conta].index[0], "Descrição"]
    aviso = ""
    if len(votos) > 1:
        aviso = "matriz com contas diferentes: " + ", ".join(f"{c} ({n}x)" for c, n in
                                                              votos.items())
    return conta, exemplo, aviso


def ler_contas_bancos(arquivo):
    """Planilha (xlsx ou csv) com Banco/Conta e o código contábil -> {Banco/Conta: código}."""
    nome = str(getattr(arquivo, "name", arquivo)).lower()
    if nome.endswith(".csv"):
        t = pd.read_csv(arquivo, sep=None, engine="python", dtype=str)
    else:
        t = pd.read_excel(arquivo, dtype=str)
    t = t.iloc[:, :2].dropna()
    return {str(c).strip(): str(v).strip().removesuffix(".0") for c, v in t.itertuples(index=False)}


def codigos_na_matriz(arquivo):
    """Códigos das contas guardados na matriz: uma aba com as colunas Banco/Conta e Código
    (Banco/Conta escrita como aparece na planilha base). {} se a matriz não tiver."""
    for aba in pd.read_excel(arquivo, sheet_name=None, header=None, dtype=str).values():
        aba = aba.fillna("").map(lambda v: str(v).strip())
        for i, linha in aba.iterrows():
            celulas = list(linha)
            if "Banco/Conta" in celulas and "Código" in celulas:
                c_conta, c_codigo = celulas.index("Banco/Conta"), celulas.index("Código")
                abaixo = aba.iloc[i + 1:]
                return {conta: codigo.removesuffix(".0") for conta, codigo in
                        zip(abaixo[c_conta], abaixo[c_codigo]) if conta and codigo}
    return {}


def _codigo_do_banco(conta, contas_bancos):
    return str(contas_bancos.get(conta, "") or "").strip()


def _aplicacao_do_banco(conta, contas_bancos):
    """Código da aplicação ligada à conta corrente (mesmo banco, mesmo número de conta)."""
    def numero(nome):
        m = re.search(r"Cc (\S+)$", nome)
        return re.sub(r"\D", "", m.group(1)).lstrip("0") if m else None

    proprio = numero(conta)
    if not proprio:
        return ""
    for outra, codigo in contas_bancos.items():
        if outra != conta and numero(outra) == proprio and codigo and "Cartão" not in outra:
            return str(codigo)
    return ""


def classificar(base, matriz, contas_bancos):
    """Base com Débito, Crédito e Histórico preenchidos, mais "Conta na matriz" e "Conferir".

    contas_bancos: {Banco/Conta como está na base: código contábil}.
    """
    b = base.copy()
    debitos, creditos, historicos, origens, conferir = [], [], [], [], []
    for conta_banco, descricao, valor in zip(b["Banco/Conta"], b["Descrição"], b["Valor"]):
        banco = _codigo_do_banco(conta_banco, contas_bancos)
        avisos = []
        outra, origem = None, ""
        if valor == 0:
            debitos.append(""), creditos.append(""), historicos.append("")
            origens.append(""), conferir.append("sem movimento no período")
            continue
        if descricao in _APLICACAO_ESPELHO and "Aplic" in conta_banco or \
                descricao in _APLICACAO_ESPELHO and any(k in conta_banco for k in
                                                         ("Invest", "CDB", "Aplic")):
            debitos.append(""), creditos.append(""), historicos.append("")
            origens.append(NAO_IMPORTAR)
            conferir.append("já lançado pela conta corrente (aplicação/resgate)")
            continue
        if _APLICACAO.match(descricao) and "Cartão" not in conta_banco:
            outra = _aplicacao_do_banco(conta_banco, contas_bancos)
            origem = "aplicação do mesmo banco"
            if not outra:
                avisos.append("falta o código da aplicação deste banco")
        if not outra and not (origem and avisos):
            outra, origem, aviso = procurar_na_matriz(descricao, matriz, entrada=valor > 0)
            if aviso:
                avisos.append(aviso)
        if outra and str(outra).lower() == NAO_IMPORTAR:
            debitos.append(""), creditos.append(""), historicos.append("")
            origens.append(origem), conferir.append("matriz diz para não importar")
            continue
        if not banco:
            avisos.append("falta o código desta conta (banco ou cartão)")
        outra = outra or ""
        if valor < 0:
            debitos.append(outra), creditos.append(banco)
            historicos.append(HIST_SAIDA + descricao)
        else:
            debitos.append(banco), creditos.append(outra)
            historicos.append(HIST_ENTRADA + descricao)
        origens.append(origem)
        conferir.append("; ".join(avisos))
    b["Débito"], b["Crédito"], b["Histórico"] = debitos, creditos, historicos
    b["Conta na matriz"], b["Conferir"] = origens, conferir
    return b
