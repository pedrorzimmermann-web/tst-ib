#!/bin/bash
# 05 - Cruza os hosts ofensores com jobs do Slurm.
# Rodar DENTRO da pasta da coleta (depois do 03; se existir delta.tsv do 04, usa ele).
# Uso: bash 05-jobs.sh [N] [desde]     (N hosts, padrao 20; desde = 2026-11-18T00:00, padrao ontem)

N=${1:-20}
DESDE=${2:-$(date -d yesterday +%Y-%m-%dT00:00)}

if [ -s delta.tsv ]; then
  NODES=$(cut -f3 delta.tsv | grep -v '^?$' | awk '!v[$0]++' | head -"$N" | paste -sd,)
else
  NODES=$(sed 's/$/\t/' comuns.txt | grep -Ff - ofensores.tsv | cut -f2 | grep -v '^?$' \
          | awk '!v[$0]++' | head -"$N" | paste -sd,)
fi
[ -n "$NODES" ] || { echo "nenhum host ofensor encontrado"; exit 0; }

echo "hosts: $NODES"
echo
echo "== Rodando agora =="
squeue -w "$NODES" -o '%.10i %.10u %.20j %.8T %S %N'
echo
echo "== Historico desde $DESDE =="
sacct -N "$NODES" -S "$DESDE" -X -o JobID,User,JobName%20,State,Start,End,NodeList%40
# PBS: qstat -n1 | grep -E "$(echo $NODES | tr , '|')"
