"""
Razão contábil exportado do Domínio: o plano de contas usado pela empresa e, nos
lançamentos dos bancos, a conta que ficou do outro lado de cada histórico.

Serve para sugerir a conta dos lançamentos que a planilha matriz ainda não conhece:
pelo nome do fornecedor/cliente no plano de contas ou pela descrição (categoria) que já
apareceu nos históricos do razão.
"""

import io
import re

import pandas as pd

from classificacao import _nome_na_matriz, _nome_no_extrato, _sem_acento, procurar_na_matriz

_DATA = re.compile(r"\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2}")


def _tabela(conteudo):
    # O .xls do Domínio não abre no xlrd; o calamine lê
    try:
        return pd.read_excel(io.BytesIO(conteudo), header=None, dtype=str, engine="calamine")
    except Exception:
        return pd.read_csv(io.BytesIO(conteudo), sep=";", header=None, dtype=str,
                           encoding="latin-1")


def parece_razao(nome, conteudo):
    """O arquivo é um razão do Domínio? Pelo título "RAZÃO" e a coluna "Cta.C.Part."."""
    if not nome.lower().endswith((".xls", ".xlsx", ".csv")):
        return False
    try:
        topo = _tabela(conteudo).head(15).fillna("").astype(str)
    except Exception:
        return False
    textos = {_sem_acento(t).strip() for t in topo.values.ravel()}
    return "razao" in textos and "cta.c.part." in textos


def _numero(v):
    v = str(v or "").strip()
    if not v or v == "nan":
        return 0.0
    if "," in v and "." in v:
        v = v.replace(",", "") if v.rfind(".") > v.rfind(",") else v.replace(".", "").replace(",", ".")
    return float(v.replace(",", "."))


def ler_razao(conteudo):
    """(plano, lançamentos). plano: Código, Classificação, Nome. lançamentos: Conta, Data,
    Histórico, Contrapartida, Débito, Crédito."""
    t = _tabela(conteudo).fillna("").astype(str)
    cab = t.index[t.iloc[:, 0].str.strip() == "Data"]
    linha = list(t.loc[cab[0]]) if len(cab) else []
    col = lambda nome, padrao: linha.index(nome) if nome in linha else padrao
    c_hist, c_cp = col("Histórico", 2), col("Cta.C.Part.", 8)
    c_deb, c_cred = col("Débito", 9), col("Crédito", 10)
    contas, lancamentos, atual = [], [], None
    for r in t.itertuples(index=False):
        if r[0].strip() == "Conta:":
            nome = next((v.strip() for v in r[3:] if v.strip()), "")
            atual = r[1].strip()
            contas.append((atual, r[2].strip(), nome))
        elif atual and _DATA.match(r[0].strip()):
            lancamentos.append((atual, r[0][:10], r[c_hist].strip(), r[c_cp].strip(),
                                _numero(r[c_deb]), _numero(r[c_cred])))
    plano = pd.DataFrame(contas, columns=["Código", "Classificação", "Nome"]) \
        .drop_duplicates("Código")
    lanc = pd.DataFrame(lancamentos, columns=["Conta", "Data", "Histórico", "Contrapartida",
                                              "Débito", "Crédito"])
    return plano, lanc


class Razao:
    """O razão pronto para sugerir contas."""

    def __init__(self, plano, lanc):
        self.plano = plano
        self.nomes = dict(zip(plano["Código"], plano["Nome"]))
        # Bancos e caixas (1.1.01): seus lançamentos mostram a conta do outro lado
        disponivel = set(plano.loc[plano["Classificação"].str.startswith("1.1.01"), "Código"])
        b = lanc[lanc["Conta"].isin(disponivel) & ~lanc["Contrapartida"].isin(disponivel)
                 & (lanc["Contrapartida"] != "")]
        self.movimento = pd.DataFrame({"texto": b["Histórico"].map(_sem_acento),
                                       "entrada": b["Débito"] > 0,
                                       "conta": b["Contrapartida"]})
        # Quanto cada conta foi usada: desempata nomes iguais no plano
        self.uso = self.movimento["conta"].value_counts()
        self.contas = pd.DataFrame({"Descrição": plano["Nome"], "Conta": plano["Código"],
                                    "Recebimento": None})
        self.contas["Nome"] = self.contas["Descrição"].map(_nome_na_matriz)
        self._categorias = {}

    def _pelo_nome(self, nome):
        conta, achado, _ = procurar_na_matriz(nome, self.contas, minimo=0.9)
        # A primeira palavra tem de ser a mesma ("SALÁRIOS" não é "SAL E BRASA LTDA")
        primeira = lambda t: (_nome_no_extrato(t) or ("",))[0]
        if not conta or primeira(nome) != primeira(achado):
            return None
        # Duas contas com o mesmo nome (fornecedor e adiantamento): a mais usada
        iguais = self.contas.loc[self.contas["Descrição"] == achado, "Conta"]
        return max(iguais, key=lambda c: self.uso.get(c, 0))

    def _pela_categoria(self, categoria, entrada):
        chave = (categoria, entrada)
        if chave not in self._categorias:
            texto, conta = _sem_acento(categoria).strip(), None
            if len(texto) >= 4:
                m = self.movimento[(self.movimento["entrada"] == entrada)
                                   & self.movimento["texto"].str.contains(texto, regex=False)]
                votos = m["conta"].value_counts()
                # Só quando a mesma conta aparece na maioria das vezes
                if len(votos) and votos.iloc[0] / len(m) >= 0.6:
                    conta = votos.index[0]
            self._categorias[chave] = conta
        return self._categorias[chave]

    def sugerir(self, descricao, entrada):
        """(código, de onde veio) para um lançamento do extrato/planilha, ou (None, "")."""
        partes = [p.strip() for p in str(descricao).split(" - ") if p.strip()]
        if not partes:
            return None, ""
        conta = self._pelo_nome(partes[0])
        if conta:
            return conta, "nome no plano de contas"
        for parte in reversed(partes[1:]):
            if re.fullmatch(r"[\d./-]+", parte):
                continue
            conta = self._pela_categoria(parte, entrada)
            if conta:
                return conta, "histórico do razão"
        return None, ""

    def nome(self, codigo):
        return self.nomes.get(str(codigo).strip(), "")
