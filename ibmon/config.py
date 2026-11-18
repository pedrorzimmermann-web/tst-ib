"""Carga de configuracao (INI) com valores padrao embutidos."""

import configparser
import os
import socket

SEARCH_PATHS = (
    "/etc/ibmon/ibmon.conf",
    os.path.expanduser("~/.config/ibmon/ibmon.conf"),
    "./ibmon.conf",
)

DEFAULTS = {
    "general": {
        # Banco SQLite: apenas inventario, ultimo snapshot de contadores e eventos.
        "db_path": "/var/lib/ibmon/ibmon.db",
        "sysfs_root": "/sys",
        "hostname": "",
        # port_xmit_data / port_rcv_data sao contados em unidades de 4 octetos.
        "data_unit_bytes": "4",
        # Inventario local (sysfs + vizinhos via smpquery) so e refeito quando o
        # "fingerprint" das portas muda ou quando este TTL (s) expira.
        "inventory_ttl": "3600",
        # Varredura completa da fabric (ibnetdiscover) e cara: TTL padrao 6h.
        "fabric_ttl": "21600",
        "command_timeout": "30",
        "events_retention_days": "30",
        "events_max_rows": "20000",
        # Arquivo opcional no formato do --node-name-map do infiniband-diags.
        "node_name_map": "",
        # Se definido, 'check' grava metricas Prometheus (textfile collector).
        "prom_textfile": "",
    },
    "collect": {
        # Descobre o switch vizinho de cada porta local (2-3 MADs por porta).
        "neighbors": "yes",
        # Consulta tambem os contadores da porta do switch ligada a cada HCA local
        # (perfquery; requer root). Detecta erros do lado do switch.
        "peer_counters": "no",
        # Contadores especificos do driver (hw_counters do mlx5).
        "hw_counters": "yes",
        # Portas a ignorar: lista "mlx5_1:1, mlx5_3:*"
        "ignore_ports": "",
        # Velocidade esperada por link (Gb/s). 0 = nao verificar.
        "expected_rate_gbps": "0",
    },
    # Limiares "aviso,critico" aplicados ao DELTA entre duas coletas.
    # Chaves terminadas em _per_sec sao aplicadas a taxa (delta / segundos).
    "thresholds": {
        "symbol_error": "10,100",
        "link_error_recovery": "1,5",
        "link_downed": "1,1",
        "port_rcv_errors": "5,50",
        "port_rcv_remote_physical_errors": "5,50",
        "port_rcv_switch_relay_errors": "50,500",
        "port_xmit_discards": "10,100",
        "port_xmit_constraint_errors": "1,10",
        "port_rcv_constraint_errors": "1,10",
        "local_link_integrity_errors": "1,5",
        "excessive_buffer_overrun_errors": "1,5",
        "VL15_dropped": "100,1000",
        "port_xmit_wait_per_sec": "100000,1000000",
        # hw_counters (mlx5)
        "out_of_buffer": "100,10000",
        "out_of_sequence": "100,10000",
        "packet_seq_err": "10,1000",
        "local_ack_timeout_err": "1,100",
        "rnr_nak_retry_err": "1,100",
        "req_cqe_error": "10,1000",
        "resp_cqe_error": "10,1000",
    },
    # Posicao fisica opcional: <hostname ou nome do switch> = <local>
    "locations": {},
}


def _split_list(value):
    return [v.strip() for v in value.replace(";", ",").split(",") if v.strip()]


class Config(object):
    def __init__(self, path=None):
        self.cp = configparser.ConfigParser(interpolation=None)
        self.cp.optionxform = str  # preserva maiusculas (ex.: VL15_dropped)
        self.cp.read_dict(DEFAULTS)
        self.path = None
        candidates = [path] if path else ([os.environ["IBMON_CONFIG"]] if os.environ.get("IBMON_CONFIG") else list(SEARCH_PATHS))
        for c in candidates:
            if c and os.path.isfile(c):
                self.cp.read(c)
                self.path = c
                break
        if path and not self.path:
            raise IOError("arquivo de configuracao nao encontrado: %s" % path)

    # ---- acessores ---------------------------------------------------------
    def get(self, section, key):
        return self.cp.get(section, key)

    def getint(self, section, key):
        return self.cp.getint(section, key)

    def getfloat(self, section, key):
        return self.cp.getfloat(section, key)

    def getbool(self, section, key):
        return self.cp.getboolean(section, key)

    def set(self, section, key, value):
        self.cp.set(section, key, str(value))

    @property
    def db_path(self):
        return self.get("general", "db_path")

    @property
    def sysfs_root(self):
        return self.get("general", "sysfs_root")

    @property
    def hostname(self):
        h = self.get("general", "hostname").strip()
        return h or socket.gethostname().split(".")[0]

    def thresholds(self):
        out = {}
        for key, raw in self.cp.items("thresholds"):
            parts = _split_list(raw)
            if not parts:
                continue
            warn = float(parts[0])
            crit = float(parts[1]) if len(parts) > 1 else warn
            out[key] = (warn, crit)
        return out

    def ignore_ports(self):
        return set(_split_list(self.get("collect", "ignore_ports")))

    def is_ignored(self, ca, port):
        ign = self.ignore_ports()
        return ("%s:%s" % (ca, port)) in ign or ("%s:*" % ca) in ign

    def locations(self):
        return dict(self.cp.items("locations"))
