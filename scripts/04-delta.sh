#!/bin/bash
# 04 - O que CRESCEU desde uma coleta anterior (contadores sao cumulativos).
# Rodar DENTRO da pasta da coleta atual; as duas pastas precisam ter passado pelo 02.
# Uso: bash 04-delta.sh ../20261118-0900 [N]      (N = quantas linhas mostrar, padrao 20)

tabela() { if command -v column >/dev/null; then column -t -s$'\t'; else cat; fi; }  # alinha colunas

ANT=${1:?informe a pasta da coleta anterior}
N=${2:-20}

chave() { awk -F'\t' '{print $1"|"$3"\t"$2"\t"$4}' "$1" | LC_ALL=C sort; }

LC_ALL=C join -t$'\t' -a2 -e 0 -o 0,2.2,1.3,2.3 <(chave "$ANT/ofensores.tsv") <(chave ofensores.tsv) \
  | awk -F'\t' '{d=$4-$3; if (d>0) {split($1,k,"|"); print d"\t"k[1]"\t"$2"\t"k[2]"\t"$3" -> "$4}}' \
  | sort -t$'\t' -k1,1gr > delta.tsv

echo "== Cresceu desde $ANT (delta  chave  host  contador  antes -> agora) =="
head -"$N" delta.tsv | tabela
