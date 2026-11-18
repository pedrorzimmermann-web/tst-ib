"""Nomes amigaveis para nos da fabric."""

import re

from .sysfs import norm_guid


def load_node_name_map(path):
    """Formato do infiniband-diags: '0x<guid> "nome"' por linha."""
    out = {}
    if not path:
        return out
    try:
        with open(path) as fh:
            for line in fh:
                line = line.split("#", 1)[0].strip()
                m = re.match(r'^(\S+)\s+"?([^"]+)"?', line)
                if m and norm_guid(m.group(1)):
                    out[norm_guid(m.group(1))] = m.group(2).strip()
    except (IOError, OSError):
        pass
    return out


def friendly_name(desc, guid, node_type, name_map=None):
    """Switch Mellanox: 'MF0;sw01:MSB7700/U1' -> 'sw01'. HCA: 'node01 mlx5_0' -> 'node01 mlx5_0'."""
    if name_map and guid in name_map:
        return name_map[guid]
    desc = (desc or "").strip()
    if node_type == "SW":
        m = re.match(r"^MF0;([^:]+):", desc)
        if m:
            return m.group(1)
    return desc or guid


def hostname_from_desc(desc):
    """'node01 mlx5_0' -> 'node01' (descricao padrao das HCAs Linux)."""
    desc = (desc or "").strip()
    if not desc:
        return None
    return desc.split()[0].split(".")[0]
