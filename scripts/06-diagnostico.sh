#!/bin/bash
# 06 - Troubleshooting quando a comparacao volta vazia ou estranha.
# Rodar DENTRO da pasta da coleta.
# Uso: bash 06-diagnostico.sh

echo "== Tamanho dos arquivos (0 = coleta falhou) =="
wc -l ibqe.txt exporter.txt ibqe_portas.txt exp_portas.txt 2>/dev/null

echo
echo "== Labels usados pelo exporter (esperado: local_guid/local_port ou guid/port) =="
grep -v '^#' exporter.txt | head -200 | grep -oE '[a-z_]+=' | sort | uniq -c | sort -rn

echo
echo "== Metricas disponiveis no exporter =="
grep -v '^#' exporter.txt | cut -d'{' -f1 | sort | uniq -c | sort -rn | head -30

echo
echo "== Caracteres invisiveis (cada linha deve terminar em :<porta>\$) =="
echo "-- ibqe_portas.txt";  head -3 ibqe_portas.txt | cat -A
echo "-- exp_portas.txt";   head -3 exp_portas.txt  | cat -A

echo
echo "== Linhas do ibqueryerrors que o 02 reconhece =="
grep -ciE 'GUID +0x[0-9a-f]+ +port +[0-9]+' ibqe.txt
grep -iE 'GUID +0x[0-9a-f]+ +port' ibqe.txt | head -2

echo
echo "== Mesmo switch nas duas fontes? (GUIDs sem a porta) =="
cut -d: -f1 ibqe_portas.txt | sort -u > /tmp/g_ibqe.$$
cut -d: -f1 exp_portas.txt  | sort -u > /tmp/g_exp.$$
echo "GUIDs em comum: $(comm -12 /tmp/g_ibqe.$$ /tmp/g_exp.$$ | wc -l)"
rm -f /tmp/g_ibqe.$$ /tmp/g_exp.$$
