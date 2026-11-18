#!/bin/bash
# Instalacao em RHEL 8.10: copia para /opt/ibmon, cria config e unidades systemd.
# Uso: sudo ./install.sh [--enable]
set -euo pipefail
PREFIX=/opt/ibmon
SRC="$(cd "$(dirname "$0")" && pwd)"

[[ $EUID -eq 0 ]] || { echo "execute como root"; exit 1; }

rpm -q infiniband-diags >/dev/null 2>&1 || echo "AVISO: instale 'infiniband-diags' (dnf install infiniband-diags)"
command -v python3 >/dev/null || { echo "python3 ausente (dnf install python36)"; exit 1; }

install -d -m 0755 "$PREFIX/bin" "$PREFIX/ibmon" "$PREFIX/tools"
install -m 0644 "$SRC"/ibmon/*.py "$PREFIX/ibmon/"
install -m 0755 "$SRC/bin/ibmon" "$PREFIX/bin/ibmon"
install -m 0755 "$SRC/tools/mkdemo.py" "$PREFIX/tools/"
ln -sf "$PREFIX/bin/ibmon" /usr/local/bin/ibmon

install -d -m 0755 /etc/ibmon
[[ -f /etc/ibmon/ibmon.conf ]] || install -m 0644 "$SRC/contrib/ibmon.conf.example" /etc/ibmon/ibmon.conf
install -d -m 0750 /var/lib/ibmon

install -m 0644 "$SRC"/contrib/systemd/ibmon-*.{service,timer} /etc/systemd/system/
systemctl daemon-reload

if [[ "${1:-}" == "--enable" ]]; then
    systemctl enable --now ibmon-check.timer ibmon-discover.timer
fi
echo "Instalado em $PREFIX. Proximos passos:"
echo "  ibmon doctor && ibmon discover --fabric && ibmon check && ibmon topo"
