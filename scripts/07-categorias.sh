#!/bin/bash
# 07 - Erros do ibqueryerrors -r agrupados por categoria (tipo de contador).
# Rodar DENTRO da pasta da coleta (so precisa do ibqe.txt).
# Uso: bash 07-categorias.sh [N] [categoria]
#      N         = quantos ofensores mostrar por categoria (padrao 10)
#      categoria = mostra so essa (ex.: SymbolErrorCounter); sem ela, todas
#
# Gera ibqe_erros.tsv:  categoria  qtd  node  link  chave
#   node  = host do link (lado cujo nome parece HCA; senao o lado remoto)
#   link  = local[porta] -> remoto[porta]
#   chave = 0x<guid 16 hex>:<porta> (mesma chave dos outros scripts)

tabela() { if command -v column >/dev/null; then column -t -s$'\t'; else cat; fi; }  # alinha colunas

N=${1:-10}
CAT=${2:-}

awk '
  function ng(g){g=tolower(g); sub(/^0x/,"",g); while(length(g)<16) g="0"g; return "0x"g}
  function q(s){ return match(s,/"[^"]*"/) ? substr(s,RSTART+1,RLENGTH-2) : "" }
  function hca(s){ return tolower(s) ~ /hca|mlx/ }
  # cabecalho: Errors for 0x... "nome"
  /^[ \t]*Errors for/ { local=q($0); next }
  match($0,/GUID +0x[0-9a-fA-F]+ +port +[0-9]+/) {
    split(substr($0,RSTART,RLENGTH),f," "); chave=ng(f[2])":"f[4]; lport=f[4]
    rname="?"; rport="?"
    i=index($0,"==>")
    if (i) {
      r=substr($0,i+3); rname=q(r)
      if (match(r,/^ *0x[0-9a-fA-F]+ +[0-9]+ +[0-9]+/)) { split(substr(r,RSTART,RLENGTH),rf," "); rport=rf[3] }
    }
    ln=(local!="" ? local : f[2])
    split((hca(ln) || rname=="?") ? ln : rname, h, " "); node=h[1]
    link=ln"["lport"] -> "rname"["rport"]"
    s=$0
    while (match(s,/\[ *[A-Za-z]+ *== *[0-9]+ *\]/)) {
      c=substr(s,RSTART+1,RLENGTH-2); s=substr(s,RSTART+RLENGTH)
      split(c,cv,"=="); gsub(/ /,"",cv[1]); gsub(/ /,"",cv[2])
      print cv[1] "\t" cv[2] "\t" node "\t" link "\t" chave
    }
  }' ibqe.txt | LC_ALL=C sort -t$'\t' -k1,1 -k2,2nr > ibqe_erros.tsv

[ -s ibqe_erros.tsv ] || { echo "nenhum erro reconhecido em ibqe.txt (rode o 06-diagnostico.sh)"; exit 1; }

echo "== Resumo por categoria =="
awk -F'\t' '{p[$1]++; t[$1]+=$2; if ($2>m[$1]) {m[$1]=$2; w[$1]=$3}}
  END {for (c in p) print c "\t" p[c] "\t" t[c] "\t" m[c] "\t" w[c]}' ibqe_erros.tsv \
  | sort -t$'\t' -k3,3nr | (printf 'CATEGORIA\tPORTAS\tTOTAL\tMAIOR\tNODE_DO_MAIOR\n'; cat) | tabela

for c in $(cut -f1 ibqe_erros.tsv | sort -u); do
  [ -n "$CAT" ] && [ "$c" != "$CAT" ] && continue
  echo
  echo "== $c (top $N) =="
  awk -F'\t' -v c="$c" '$1==c {print $2 "\t" $3 "\t" $4 "\t" $5}' ibqe_erros.tsv | head -"$N" \
    | (printf 'QTD\tNODE\tLINK\tCHAVE\n'; cat) | tabela
done

echo
echo "== Nodes em mais categorias (node  categorias  total) =="
awk -F'\t' '{k=$3; if (!((k,$1) in v)) {v[k,$1]; n[k]++}; t[k]+=$2}
  END {for (k in n) print k "\t" n[k] "\t" t[k]}' ibqe_erros.tsv \
  | sort -t$'\t' -k2,2nr -k3,3nr | head -"$N" | tabela
