#!/bin/bash
# 02 - Extrai as portas com erro das duas coletas, no mesmo formato: 0x<16 hex>:<porta>
# Rodar DENTRO da pasta da coleta (onde estao ibqe.txt e exporter.txt).
# Uso: bash 02-extrair.sh
#
# Gera:
#   ibqe_portas.txt   portas com erro segundo o ibqueryerrors -r
#   ofensores.tsv     chave  host  contador  valor  chave_remota  (do exporter, piores primeiro)
#   exp_portas.txt    portas com erro segundo o exporter

# Contadores de erro considerados no exporter (regex sobre o nome da metrica)
tabela() { if command -v column >/dev/null; then column -t -s$'\t'; else cat; fi; }  # alinha colunas

FILTRO=${FILTRO:-'symbolerror|rcverror|linkdowned|discard|integrity|overrun|constraint'}

# --- ibqueryerrors -r: linhas "GUID 0x... port N: [Contador == X] ..."
grep -oiE 'GUID +0x[0-9a-f]+ +port +[0-9]+' ibqe.txt \
  | awk 'function ng(g){g=tolower(g); sub(/^0x/,"",g); while(length(g)<16) g="0"g; return "0x"g}
         {print ng($2)":"$4}' \
  | LC_ALL=C sort -u > ibqe_portas.txt

# --- exporter: aceita labels local_guid/local_port (atual) ou guid/port (antigo)
awk -v f="$FILTRO" '
  function ng(g){g=tolower(g); sub(/^0x/,"",g); while(length(g)<16) g="0"g; return "0x"g}
  function lab(s,k){ if (match(s,"[{,] *" k "=\"[^\"]*\"")) { s=substr(s,RSTART,RLENGTH); sub(/^[^"]*"/,"",s); sub(/"$/,"",s); return s } return "" }
  /^#/ {next}
  {
    split($1,m,"{"); n=tolower(m[1]); sub(/^infiniband_/,"",n); sub(/_total$/,"",n)
    if (n !~ f || $NF+0 <= 0) next
    g=lab($0,"local_guid"); if (g=="") g=lab($0,"guid")
    p=lab($0,"local_port"); if (p=="") p=lab($0,"port")
    if (g=="" || p=="") next
    h=lab($0,"remote_name"); split(h,hh," ")
    rg=lab($0,"remote_guid"); rp=lab($0,"remote_port")
    rk=(rg!="" && rp!="") ? ng(rg)":"rp : "-"
    print ng(g)":"p "\t" (hh[1]!="" ? hh[1] : "?") "\t" n "\t" $NF "\t" rk
  }' exporter.txt \
  | LC_ALL=C sort -t$'\t' -k4,4gr > ofensores.tsv

cut -f1 ofensores.tsv | LC_ALL=C sort -u > exp_portas.txt

wc -l ibqe_portas.txt exp_portas.txt ofensores.tsv
echo
echo "== 15 piores no exporter (chave  host  contador  valor) =="
head -15 ofensores.tsv | cut -f1-4 | tabela
