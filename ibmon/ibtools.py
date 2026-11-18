"""Wrappers dos utilitarios infiniband-diags (pacote 'infiniband-diags' do rdma-core).

- smpquery  : vizinho direto de uma porta local (rota direta 0,1) -> barato
- perfquery : contadores de portas remotas (ex.: porta do switch ligada ao host)
- ibnetdiscover : topologia completa da fabric -> caro, executar com pouca frequencia
"""

import hashlib
import re

from .runner import parse_dotted
from .sysfs import norm_guid

# perfquery -> nomes canonicos (iguais aos do sysfs)
PERF_NAME_MAP = {
    "SymbolErrorCounter": "symbol_error",
    "LinkErrorRecoveryCounter": "link_error_recovery",
    "LinkDownedCounter": "link_downed",
    "PortRcvErrors": "port_rcv_errors",
    "PortRcvRemotePhysicalErrors": "port_rcv_remote_physical_errors",
    "PortRcvSwitchRelayErrors": "port_rcv_switch_relay_errors",
    "PortXmitDiscards": "port_xmit_discards",
    "PortXmitConstraintErrors": "port_xmit_constraint_errors",
    "PortRcvConstraintErrors": "port_rcv_constraint_errors",
    "LocalLinkIntegrityErrors": "local_link_integrity_errors",
    "ExcessiveBufferOverrunErrors": "excessive_buffer_overrun_errors",
    "VL15Dropped": "VL15_dropped",
    "PortXmitWait": "port_xmit_wait",
    "PortXmitData": "port_xmit_data",
    "PortRcvData": "port_rcv_data",
    "PortXmitPkts": "port_xmit_packets",
    "PortRcvPkts": "port_rcv_packets",
    "PortUnicastXmitPkts": "unicast_xmit_packets",
    "PortUnicastRcvPkts": "unicast_rcv_packets",
    "PortMulticastXmitPkts": "multicast_xmit_packets",
    "PortMulticastRcvPkts": "multicast_rcv_packets",
}


def _ca_args(ca, port):
    a = []
    if ca:
        a += ["-C", ca]
    if port:
        a += ["-P", str(port)]
    return a


# --------------------------------------------------------------------------
# smpquery: vizinho direto
# --------------------------------------------------------------------------
def query_neighbor(runner, ca, port):
    """Identifica o no do outro lado do cabo de <ca>:<port>.

    Retorna dict(guid, node_type, num_ports, remote_port, description, lid) ou None.
    """
    base = ["smpquery"] + _ca_args(ca, port) + ["-D"]
    rc, out = runner.run(base + ["nodeinfo", "0,1"])
    if rc != 0 or not out:
        return None
    ni = parse_dotted(out)
    guid = norm_guid(ni.get("Guid") or ni.get("NodeGuid"))
    if not guid:
        return None
    node_type = (ni.get("NodeType") or "").strip()
    remote_port = int(ni.get("LocalPort", "0") or 0)
    rc, out = runner.run(base + ["nodedesc", "0,1"])
    desc = parse_dotted(out).get("Node Description", "") if rc == 0 else ""
    # LID: em switches o LID pertence a porta 0
    pinfo_port = "0" if node_type.lower().startswith("switch") else str(remote_port)
    rc, out = runner.run(base + ["portinfo", "0,1", pinfo_port])
    lid = None
    if rc == 0:
        try:
            lid = int(parse_dotted(out).get("Lid", ""), 0)
        except ValueError:
            lid = None
    return {
        "guid": guid,
        "node_type": "SW" if node_type.lower().startswith("switch") else "CA",
        "num_ports": int(ni.get("NumPorts", "0") or 0),
        "remote_port": remote_port,
        "description": desc,
        "lid": lid,
    }


# --------------------------------------------------------------------------
# perfquery: contadores remotos
# --------------------------------------------------------------------------
def _perf_values(text):
    out = {}
    for k, v in parse_dotted(text).items():
        name = PERF_NAME_MAP.get(k)
        if name:
            try:
                out[name] = int(v, 0)
            except ValueError:
                pass
    return out


def query_port_counters(runner, lid, port, ca=None, ca_port=None):
    """Contadores de uma porta remota via PMA. Combina PortCounters e -x (64 bits)."""
    base = ["perfquery"] + _ca_args(ca, ca_port)
    rc, out = runner.run(base + [str(lid), str(port)])
    if rc != 0:
        return None
    vals = _perf_values(out)
    rc, out = runner.run(base + ["-x", str(lid), str(port)])
    if rc == 0:
        vals.update(_perf_values(out))  # valores de 64 bits sobrescrevem os de 32
    return vals


# --------------------------------------------------------------------------
# ibnetdiscover: topologia completa
# --------------------------------------------------------------------------
_NODE_RE = re.compile(
    r'^(Switch|Ca|Rt)\s+(\d+)\s+"([SHR])-([0-9A-Fa-fx]+)"\s*(?:#\s*"(.*?)"(.*))?$')
_PORT_RE = re.compile(
    r'^\[(\d+)\](?:\(([0-9A-Fa-f]+)\))?\s+"([SHR])-([0-9A-Fa-fx]+)"\[(\d+)\]'
    r'(?:\(([0-9A-Fa-f]+)\))?\s*(?:#\s*(.*))?$')


def run_ibnetdiscover(runner, ca=None, port=None):
    rc, out = runner.run(["ibnetdiscover"] + _ca_args(ca, port))
    return out if rc == 0 and out.strip() else None


def fabric_hash(text):
    """Hash estavel da topologia (ignora comentarios de cabecalho com data)."""
    lines = [l.rstrip() for l in text.splitlines() if l.strip() and not l.startswith("#")]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def parse_ibnetdiscover(text):
    """Retorna {'nodes': {guid: {...}}, 'links': [(a_guid,a_port,b_guid,b_port,speed)], 'ca_ports': [...]}"""
    nodes, links, ca_ports = {}, {}, []
    cur = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _NODE_RE.match(line)
        if m:
            kind, nports, _, guid, desc, rest = m.groups()
            cur = norm_guid(guid)
            lm = re.search(r"\blid (\d+)", rest or "")
            nodes[cur] = {
                "guid": cur,
                "node_type": {"Switch": "SW", "Ca": "CA", "Rt": "RT"}[kind],
                "num_ports": int(nports),
                "description": desc or "",
                "lid": int(lm.group(1)) if lm else None,
            }
            continue
        m = _PORT_RE.match(line)
        if m and cur:
            lport, lpguid, _, rguid, rport, rpguid, comment = m.groups()
            comment = comment or ""
            rguid = norm_guid(rguid)
            speed_m = re.search(r"(\d+x[A-Za-z]+)\s*$", comment)
            speed = speed_m.group(1) if speed_m else None
            lids = [int(x) for x in re.findall(r"\blid (\d+)", comment)]
            node = nodes[cur]
            if node["node_type"] == "CA":
                ca_ports.append({"node_guid": cur, "port": int(lport),
                                 "port_guid": norm_guid(lpguid) if lpguid else None,
                                 "lid": lids[0] if lids else None})
            a, b = (cur, int(lport)), (rguid, int(rport))
            key = (a, b) if a <= b else (b, a)
            links[key] = speed or links.get(key)
            # Garante registro minimo do no remoto (sera completado no seu proprio bloco)
            if rguid not in nodes:
                rdesc = re.search(r'"(.*?)"', comment)
                nodes[rguid] = {"guid": rguid,
                                "node_type": "SW" if m.group(3) == "S" else "CA",
                                "num_ports": None,
                                "description": rdesc.group(1) if rdesc else "",
                                "lid": lids[-1] if lids else None,
                                "_partial": True}
            continue
        if line.startswith(("vendid", "devid", "sysimgguid", "switchguid", "caguid", "routerguid")):
            continue
    return {
        "nodes": nodes,
        "links": [(k[0][0], k[0][1], k[1][0], k[1][1], s) for k, s in sorted(links.items())],
        "ca_ports": ca_ports,
    }
