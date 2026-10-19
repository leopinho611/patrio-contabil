"""Gera a planilha base pela linha de comando.

    python importar.py extratos.zip cartao.ofx -o base.xlsx
    python importar.py extratos.zip --matriz matriz.xlsx --contas contas_bancos.xlsx

Com --matriz, preenche Débito, Crédito e Histórico. --contas é uma planilha com duas
colunas: a Banco/Conta como aparece na planilha base e o código contábil dela. Sem
--contas, grava contas_bancos.csv com as contas lidas para preencher os códigos.
"""

import argparse
from pathlib import Path

import pandas as pd

from classificacao import classificar, ler_contas_bancos, ler_matriz
from importacao import importar, para_excel


def main():
    p = argparse.ArgumentParser(description="Junta extratos (zip, OFX, PDF, CSV, Excel) numa planilha base.")
    p.add_argument("arquivos", nargs="+", type=Path)
    p.add_argument("-o", "--saida", type=Path, default=Path("planilha_base.xlsx"))
    p.add_argument("--matriz", type=Path, help="planilha matriz (descrição -> conta contábil)")
    p.add_argument("--contas", type=Path, help="códigos contábeis dos bancos e cartões")
    a = p.parse_args()
    base, leitura, _ = importar([(f.name, f.read_bytes()) for f in a.arquivos])
    print(leitura.to_string(index=False))
    print(f"\n{len(base)} lançamentos em {base['Banco/Conta'].nunique()} conta(s)")
    if a.matriz:
        if a.contas:
            contas = ler_contas_bancos(a.contas)
        else:
            modelo = a.saida.with_name("contas_bancos.csv")
            pd.DataFrame({"Banco/Conta": sorted(base["Banco/Conta"].unique()), "Código": ""}) \
                .to_csv(modelo, index=False, sep=";")
            print(f"Sem --contas: preencha os códigos em {modelo} e rode de novo")
            contas = {}
        base = classificar(base, ler_matriz(a.matriz), contas)
        prontos = ((base["Débito"] != "") & (base["Crédito"] != "")).sum()
        print(f"{prontos} de {len(base)} lançamentos com débito e crédito")
    a.saida.write_bytes(para_excel(base))
    print(f"Planilha gravada em {a.saida}")


if __name__ == "__main__":
    main()
