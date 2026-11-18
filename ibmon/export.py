"""Exportacao: JSON de grafo (contrato para a futura GUI), Graphviz DOT e Prometheus."""

import json
import os
import tempfile
import time

from .analyze import SATURATION


def graph(store):
    """Formato descrito em gui/schema/graph.schema.json."""
    nodes, ports, links = store.nodes(), store.ports(), store.links()
    cs = store.counter_all()
    sev = {}
    for e in store.events(open_only=True, limit=100000):
        sev[e["scope"]] = max(sev.get(e["scope"], 0), e["severity"])
    by_node = {}
    for p in ports:
        by_node.setdefault(p["node_guid"], []).append({
            "port": p["port_num"], "port_guid": p["port_guid"], "ca": p["ca_name"], "lid": p["lid"],
            "state": p["state"], "phys_state": p["phys_state"], "rate": p["rate"],
            "netdevs": [x for x in (p["netdevs"] or "").split(",") if x],
            "ip_addrs": [x for x in (p["ip_addrs"] or "").split(",") if x],
            "pci": p["pci_addr"],
        })
    out_nodes = []
    for g, n in sorted(nodes.items()):
        out_nodes.append({
            "id": g, "type": "switch" if n["node_type"] == "SW" else "hca", "name": n["name"],
            "hostname": n["hostname"], "lid": n["lid"], "num_ports": n["num_ports"],
            "location": n["location"] or None, "local": bool(n["is_local"]), "ports": by_node.get(g, []),
        })
    out_edges = []
    for l in links:
        ta, tb = nodes.get(l["a_guid"], {}).get("node_type"), nodes.get(l["b_guid"], {}).get("node_type")
        s = max(sev.get("port:%s:%s" % (l["a_guid"], l["a_port"]), 0),
                sev.get("port:%s:%s" % (l["b_guid"], l["b_port"]), 0))
        metrics = {}
        for end, g, p in (("a", l["a_guid"], l["a_port"]), ("b", l["b_guid"], l["b_port"])):
            st = cs.get("port:%s:%s" % (g, p))
            if st:
                metrics[end] = st[1].get("r", {})
        out_edges.append({
            "id": "%s:%s-%s:%s" % (l["a_guid"], l["a_port"], l["b_guid"], l["b_port"]),
            "source": l["a_guid"], "source_port": l["a_port"], "target": l["b_guid"], "target_port": l["b_port"],
            "kind": "switch-switch" if ta == tb == "SW" else "host-switch" if "SW" in (ta, tb) else "host-host",
            "speed": l["speed"], "severity": s, "rates": metrics,
        })
    return {
        "schema": "ibmon.graph/1", "generated_at": int(time.time()),
        "nodes": out_nodes, "edges": out_edges,
        "events": [dict(e) for e in store.events(open_only=True, limit=1000)],
    }


def dot(store):
    g = graph(store)
    colors = {0: "darkgreen", 1: "orange", 2: "red"}
    out = ["graph ibfabric {", "  rankdir=LR; node [fontname=Helvetica fontsize=10];"]
    for n in g["nodes"]:
        shape = "box3d" if n["type"] == "switch" else "box"
        label = n["name"] or n["id"]
        if n["type"] == "hca" and n["hostname"] and n["hostname"] not in label:
            label = "%s\\n%s" % (n["hostname"], label)
        out.append('  "%s" [shape=%s label="%s\\n%s"];' % (n["id"], shape, label, n["id"]))
    for e in g["edges"]:
        out.append('  "%s" -- "%s" [taillabel="%s" headlabel="%s" label="%s" color=%s];' % (
            e["source"], e["target"], e["source_port"], e["target_port"], e["speed"] or "",
            colors.get(e["severity"], "black")))
    out.append("}")
    return "\n".join(out)


def _esc(v):
    return str(v).replace("\\", "\\\\").replace('"', '\\"')


def prometheus(store):
    nodes, cs = store.nodes(), store.counter_all()
    lines = [
        "# HELP ibmon_port_bytes_per_second Taxa de dados na porta IB (ultimo intervalo).",
        "# TYPE ibmon_port_bytes_per_second gauge",
        "# HELP ibmon_port_counter Valor acumulado do contador da porta IB.",
        "# TYPE ibmon_port_counter counter",
        "# HELP ibmon_port_xmit_wait_per_second PortXmitWait por segundo (congestionamento).",
        "# TYPE ibmon_port_xmit_wait_per_second gauge",
    ]
    for scope, (t, data) in sorted(cs.items()):
        _, guid, port = scope.split(":")
        n = nodes.get(guid, {})
        lab = 'guid="%s",port="%s",node="%s",host="%s"' % (guid, port, _esc(n.get("name") or ""),
                                                          _esc(n.get("hostname") or ""))
        r = data.get("r", {})
        for k, d in (("tx_Bps", "tx"), ("rx_Bps", "rx")):
            if k in r:
                lines.append('ibmon_port_bytes_per_second{%s,dir="%s"} %s' % (lab, d, r[k]))
        if "xmit_wait_ps" in r:
            lines.append("ibmon_port_xmit_wait_per_second{%s} %s" % (lab, r["xmit_wait_ps"]))
        for c, v in sorted(data.get("c", {}).items()):
            if c in SATURATION or c in ("port_xmit_wait", "port_xmit_data", "port_rcv_data"):
                lines.append('ibmon_port_counter{%s,counter="%s"} %d' % (lab, c, v))
    counts = {0: 0, 1: 0, 2: 0}
    for e in store.events(open_only=True, limit=100000):
        counts[e["severity"]] = counts.get(e["severity"], 0) + 1
    lines.append("# TYPE ibmon_open_events gauge")
    for s, name in ((1, "warning"), (2, "critical")):
        lines.append('ibmon_open_events{severity="%s"} %d' % (name, counts.get(s, 0)))
    lines.append("ibmon_last_check_timestamp_seconds %s" % (store.meta_get("last_check_ts", "0")))
    return "\n".join(lines) + "\n"


def write_atomic(path, content):
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".ibmon.")
    with os.fdopen(fd, "w") as fh:
        fh.write(content)
    os.chmod(tmp, 0o644)
    os.rename(tmp, path)
