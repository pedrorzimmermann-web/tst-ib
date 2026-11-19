#!/bin/bash
# 01 - Coleta: ibqueryerrors -r + metricas do infiniband-exporter numa pasta com data/hora.
# Rodar no no mestre (onde esta o exporter).
# Uso: bash 01-coleta.sh [porta_do_exporter]      (padrao 9683)

PORTA=${1:-9683}
D=~/ibbase/$(date +%Y%m%d-%H%M)
mkdir -p "$D" && cd "$D" || exit 1

ibqueryerrors -r > ibqe.txt
curl -s "localhost:$PORTA/metrics" | grep -v '^#' > exporter.txt

wc -l ibqe.txt exporter.txt
echo "coleta em: $D"
