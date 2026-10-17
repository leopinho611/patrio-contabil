# Patrio · Planilha base

Junta extratos de banco, faturas de cartão e planilhas de clientes numa planilha base, com as
colunas **Banco/Conta, Data, Descrição, Valor, Débito, Crédito, Histórico**, para depois
classificar e importar no Domínio.

Lê arquivos soltos ou compactados (`.zip`, inclusive zip dentro de zip): OFX de qualquer banco,
CSV/Excel de qualquer banco (identifica as colunas sozinho, ajustável na tela), extrato Nubank
em PDF e fatura do cartão BB em PDF. O mesmo período enviado duas vezes, ou em formatos
diferentes, não duplica. Valor positivo é entrada, negativo é saída.

A leitura (`financeiro.py`, `regras.py`) veio do app Contas em Dia (sistema-financeiro-pj).

## Uso

```bash
pip install -r requirements.txt
streamlit run app.py                                    # tela
python importar.py extratos.zip -o planilha_base.xlsx   # linha de comando
pytest                                                  # testes
```

Arquivos de clientes nunca entram no repositório (o `.gitignore` bloqueia zip, ofx, pdf, csv e xlsx).
