"""
Onde ficam as regras de categoria por fornecedor, e consulta de CNPJ.

Regras compartilhadas (empresas) ficam em regras/regras_fornecedores.csv, dentro
do repositório. Com GITHUB_TOKEN e GITHUB_REPO nos Secrets do Streamlit, o app
lê e grava esse arquivo direto no GitHub: vale para todo mundo que usar o app e
cada alteração vira um commit (dá para ver quem mudou o quê e desfazer).
Sem o token, o app lê o arquivo do repositório e grava só na cópia local, que o
Streamlit Cloud apaga quando reinicia.

Regras pessoais (Pix para pessoas) nunca vão para o arquivo compartilhado.
"""

import base64
import io
import json
import re
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import pandas as pd

from financeiro import COLUNAS_REGRAS

CAMINHO_REGRAS = "regras/regras_fornecedores.csv"
ARQUIVO_LOCAL = Path(__file__).parent / CAMINHO_REGRAS
URL_BRASILAPI = "https://brasilapi.com.br/api/cnpj/v1/{cnpj}"
TIMEOUT = 10


def _ler_csv(texto):
    df = pd.read_csv(io.StringIO(texto), dtype=str).fillna("")
    for c in COLUNAS_REGRAS:
        if c not in df:
            df[c] = ""
    return df[COLUNAS_REGRAS]


def regras_vazias():
    return pd.DataFrame(columns=COLUNAS_REGRAS)


def juntar(regras, novas):
    """Acrescenta/atualiza regras; para a mesma chave, a mais nova vence."""
    tudo = pd.concat([regras, novas], ignore_index=True)
    return tudo.drop_duplicates("chave", keep="last").reset_index(drop=True)


def nova_regra(chave, fornecedor, categoria, subcategoria, origem, cnpj="", cnae=""):
    return pd.DataFrame([{
        "chave": chave, "fornecedor": fornecedor, "categoria": categoria,
        "subcategoria": subcategoria or "", "origem": origem, "cnpj": cnpj or "",
        "cnae": str(cnae or ""), "atualizado_em": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }])


class RepositorioRegras:
    """Lê e grava o arquivo de regras compartilhadas (GitHub ou cópia local)."""

    def __init__(self, token=None, repo=None, branch="main"):
        self.token = token
        self.repo = repo
        self.branch = branch or "main"

    @property
    def no_github(self):
        return bool(self.token and self.repo)

    def _url(self):
        return f"https://api.github.com/repos/{self.repo}/contents/{CAMINHO_REGRAS}"

    def _requisicao(self, metodo, url, corpo=None):
        dados = json.dumps(corpo).encode() if corpo is not None else None
        req = urllib.request.Request(url, data=dados, method=metodo, headers={
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode())

    def carregar(self):
        """Regras atuais. Se o GitHub falhar, usa a cópia do repositório."""
        if self.no_github:
            try:
                info = self._requisicao("GET", f"{self._url()}?ref={self.branch}")
                return _ler_csv(base64.b64decode(info["content"]).decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return regras_vazias()
            except Exception:
                pass
        if ARQUIVO_LOCAL.exists():
            return _ler_csv(ARQUIVO_LOCAL.read_text(encoding="utf-8"))
        return regras_vazias()

    def salvar(self, regras, mensagem):
        """Grava as regras. Retorna "github", "local" ou levanta erro."""
        texto = regras[COLUNAS_REGRAS].sort_values("chave").to_csv(index=False)
        if self.no_github:
            sha = None
            try:
                sha = self._requisicao("GET", f"{self._url()}?ref={self.branch}")["sha"]
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    raise RuntimeError(
                        "o token do GitHub venceu ou não tem permissão. Gere um novo e atualize "
                        "GITHUB_TOKEN em Settings → Secrets.") from e
                if e.code != 404:
                    raise
            corpo = {"message": mensagem, "branch": self.branch,
                     "content": base64.b64encode(texto.encode("utf-8")).decode()}
            if sha:
                corpo["sha"] = sha
            try:
                self._requisicao("PUT", self._url(), corpo)
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    raise RuntimeError(
                        "o token do GitHub venceu ou não tem permissão de escrita. Gere um novo "
                        "(Contents: Read and write) e atualize GITHUB_TOKEN em Settings → Secrets."
                    ) from e
                raise
            return "github"
        ARQUIVO_LOCAL.parent.mkdir(parents=True, exist_ok=True)
        ARQUIVO_LOCAL.write_text(texto, encoding="utf-8")
        return "local"


def consultar_cnpj(cnpj):
    """Dados públicos do CNPJ na Receita Federal (via BrasilAPI), ou None.

    Retorna {"nome": nome fantasia ou razão social, "cnae": código, "atividade": descrição,
    "mei": se é microempreendedor individual}.
    """
    req = urllib.request.Request(URL_BRASILAPI.format(cnpj=cnpj),
                                 headers={"User-Agent": "sistema-financeiro-pj"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            dados = json.loads(resp.read().decode())
    except Exception:
        return None
    razao = (dados.get("razao_social") or "").strip()
    return {
        # MEI tem o nome da pessoa na razão social: a regra fica só nas pessoais
        "mei": bool(dados.get("opcao_pelo_mei"))
        or bool(re.match(r"^\d{2}\.\d{3}\.\d{3}\s", razao)),
        "nome": (dados.get("nome_fantasia") or razao).strip(),
        "cnae": str(dados.get("cnae_fiscal") or ""),
        "atividade": (dados.get("cnae_fiscal_descricao") or "").strip(),
    }
