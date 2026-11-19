#!/bin/bash
# 03 - Compara ibqueryerrors x exporter (depois do 02).
# Rodar DENTRO da pasta da coleta.
# Uso: bash 03-comparar.sh
#
# Gera: comuns.txt (nas duas), so_ibqe.txt, so_exp.txt

tabela() { if command -v column >/dev/null; then column -t -s$'\t'; else cat; fi; }  # alinha colunas

LC_ALL=C comm -12 ibqe_portas.txt exp_portas.txt > comuns.txt
LC_ALL=C comm -23 ibqe_portas.txt exp_portas.txt > so_ibqe.txt
LC_ALL=C comm -13 ibqe_portas.txt exp_portas.txt > so_exp.txt

# O erro pode estar registrado na outra ponta do link (porta do HCA no ibqueryerrors,
# porta do switch no exporter). Casa pela chave_remota (coluna 5) do exporter.
cut -f5 ofensores.tsv | grep -v '^-$' | LC_ALL=C sort -u > exp_remotas.txt
LC_ALL=C comm -12 so_ibqe.txt exp_remotas.txt > outra_ponta.txt

# portas do switch que ja casaram pela outra ponta saem do "so exporter"
awk -F'\t' 'NR==FNR {k[$1]; next} ($5 in k) {print $1}' outra_ponta.txt ofensores.tsv | LC_ALL=C sort -u > outra_ponta_sw.txt
LC_ALL=C comm -23 so_exp.txt outra_ponta_sw.txt > so_exp.tmp && mv so_exp.tmp so_exp.txt

echo "nas duas: $(wc -l < comuns.txt) | outra ponta: $(wc -l < outra_ponta.txt) | so ibqueryerrors: $(( $(wc -l < so_ibqe.txt) - $(wc -l < outra_ponta.txt) )) | so exporter: $(wc -l < so_exp.txt)"

echo
echo "== Confirmadas pelas duas fontes (chave  host  contador  valor) =="
sed 's/$/\t/' comuns.txt | grep -Ff - ofensores.tsv | cut -f1-4 | tabela

if [ -s outra_ponta.txt ]; then
  echo
  echo "== Erro registrado na outra ponta (chave_hca -> porta do switch  host  contador  valor) =="
  while read -r k; do
    awk -F'\t' -v k="$k" '$5==k {print k" -> "$1"\t"$2"\t"$3"\t"$4}' ofensores.tsv
  done < outra_ponta.txt | tabela
fi
