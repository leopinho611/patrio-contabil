"""Gera a planilha base pela linha de comando.

    python importar.py extratos.zip cartao.ofx -o base.xlsx
"""

import argparse
from pathlib import Path

from importacao import importar, para_excel


def main():
    p = argparse.ArgumentParser(description="Junta extratos (zip, OFX, PDF, CSV, Excel) numa planilha base.")
    p.add_argument("arquivos", nargs="+", type=Path)
    p.add_argument("-o", "--saida", type=Path, default=Path("planilha_base.xlsx"))
    a = p.parse_args()
    base, leitura, _ = importar([(f.name, f.read_bytes()) for f in a.arquivos])
    print(leitura.to_string(index=False))
    print(f"\n{len(base)} lançamentos em {base['Banco/Conta'].nunique()} conta(s)")
    a.saida.write_bytes(para_excel(base))
    print(f"Planilha gravada em {a.saida}")


if __name__ == "__main__":
    main()
