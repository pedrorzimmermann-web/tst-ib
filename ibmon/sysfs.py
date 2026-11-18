"""Coleta local via /sys (sem trafego de MADs na fabric, custo de milissegundos).

- /sys/class/infiniband/<ca>/...           HCAs, portas, estado, taxa, contadores
- /sys/class/net/<if>/{type,address}       interfaces IPoIB (type 32) -> port GUID
"""

import os
import re

IB_TYPE = "32"  # ARPHRD_INFINIBAND


def _read(path):
    try:
        with open(path) as fh:
            return fh.read().strip()
    except (IOError, OSError):
        return None


def norm_guid(raw):
    """'0c42:a103:00b2:c3d4' | '0x0c42a103...' | 'c42a103...' -> '0x0c42a10300b2c3d4'."""
    if raw is None:
        return None
    s = raw.strip().lower().replace(":", "")
    if s.startswith("0x"):
        s = s[2:]
    s = s.split("(")[0]
    if not s or not re.match(r"^[0-9a-f]+$", s):
        return None
    return "0x" + s.zfill(16)[-16:]


def strip_code(raw):
    """'4: ACTIVE' -> 'ACTIVE'."""
    if raw is None:
        return None
    return raw.split(":", 1)[-1].strip()


def parse_rate(raw):
    """'100 Gb/sec (4X EDR)' -> {'gbps': 100.0, 'width': '4X', 'speed': 'EDR'}."""
    out = {"gbps": None, "width": None, "speed": None, "raw": raw}
    if not raw:
        return out
    m = re.match(r"^\s*([\d.]+)\s*Gb/sec(?:\s*\((\d+X)\s*([A-Za-z]*)\))?", raw)
    if m:
        out["gbps"] = float(m.group(1))
        out["width"] = m.group(2)
        out["speed"] = m.group(3) or None
    return out


def _int(raw):
    if raw is None:
        return None
    try:
        return int(raw, 0)
    except ValueError:
        return None


class SysfsCollector(object):
    def __init__(self, root="/sys"):
        self.root = root
        self.ib = os.path.join(root, "class", "infiniband")
        self.net = os.path.join(root, "class", "net")

    def present(self):
        return os.path.isdir(self.ib)

    def list_hcas(self):
        """Inventario das HCAs e portas. Nao le contadores."""
        hcas = []
        if not self.present():
            return hcas
        for ca in sorted(os.listdir(self.ib)):
            base = os.path.join(self.ib, ca)
            dev = os.path.join(base, "device")
            pci = os.path.basename(os.path.realpath(dev)) if os.path.exists(dev) else None
            hca = {
                "ca": ca,
                "node_guid": norm_guid(_read(os.path.join(base, "node_guid"))),
                "sys_guid": norm_guid(_read(os.path.join(base, "sys_image_guid"))),
                "node_desc": _read(os.path.join(base, "node_desc")) or "",
                "node_type": strip_code(_read(os.path.join(base, "node_type"))),
                "fw_ver": _read(os.path.join(base, "fw_ver")),
                "hca_type": _read(os.path.join(base, "hca_type")),
                "board_id": _read(os.path.join(base, "board_id")),
                "pci": pci,
                "ports": [],
            }
            pdir = os.path.join(base, "ports")
            if os.path.isdir(pdir):
                for p in sorted(os.listdir(pdir), key=lambda x: int(x) if x.isdigit() else 0):
                    if p.isdigit():
                        hca["ports"].append(self._port(ca, int(p)))
            hcas.append(hca)
        return hcas

    def _port(self, ca, port):
        b = os.path.join(self.ib, ca, "ports", str(port))
        gid0 = _read(os.path.join(b, "gids", "0")) or ""
        port_guid = norm_guid(gid0.replace(":", "")[-16:]) if gid0 else None
        rate_raw = _read(os.path.join(b, "rate"))
        return {
            "ca": ca,
            "port": port,
            "port_guid": port_guid,
            "state": strip_code(_read(os.path.join(b, "state"))),
            "phys_state": strip_code(_read(os.path.join(b, "phys_state"))),
            "rate": rate_raw,
            "rate_info": parse_rate(rate_raw),
            "lid": _int(_read(os.path.join(b, "lid"))),
            "sm_lid": _int(_read(os.path.join(b, "sm_lid"))),
            "link_layer": _read(os.path.join(b, "link_layer")) or "InfiniBand",
        }

    def read_counters(self, ca, port, hw=True):
        """Le todos os contadores da porta. Retorna {nome: int}."""
        out = {}
        b = os.path.join(self.ib, ca, "ports", str(port))
        dirs = ["counters"] + (["hw_counters"] if hw else [])
        for d in dirs:
            full = os.path.join(b, d)
            if not os.path.isdir(full):
                continue
            for name in os.listdir(full):
                v = _int(_read(os.path.join(full, name)))
                if v is not None:
                    out[name] = v
        return out

    def ipoib_interfaces(self):
        """{port_guid: [ifname, ...]} para interfaces IPoIB (inclui filhas/pkeys)."""
        out = {}
        if not os.path.isdir(self.net):
            return out
        for ifname in sorted(os.listdir(self.net)):
            d = os.path.join(self.net, ifname)
            if _read(os.path.join(d, "type")) != IB_TYPE:
                continue
            addr = _read(os.path.join(d, "address")) or ""
            parts = addr.split(":")
            if len(parts) != 20:
                continue
            guid = norm_guid("".join(parts[12:]))
            out.setdefault(guid, []).append(ifname)
        return out


def parse_ip_addr(text):
    """Saida de 'ip -o addr show' -> {ifname: {'ipv4': [...], 'ipv6': [...]}} (ignora link-local)."""
    out = {}
    for line in text.splitlines():
        m = re.match(r"^\d+:\s+(\S+?)(?:@\S+)?\s+(inet6?)\s+(\S+)", line)
        if not m:
            continue
        ifname, fam, addr = m.groups()
        if fam == "inet6" and addr.lower().startswith("fe80"):
            continue
        d = out.setdefault(ifname, {"ipv4": [], "ipv6": []})
        d["ipv4" if fam == "inet" else "ipv6"].append(addr)
    return out
