"""Saida de texto para terminal (sem dependencias externas)."""

import os
import sys
import time

from .analyze import CRIT, OK, SEV_NAME, SHORT, WARN

_COLORS = {OK: "32", WARN: "33", CRIT: "31"}
USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def color(text, sev):
    if not USE_COLOR or sev not in _COLORS:
        return text
    return "\033[%sm%s\033[0m" % (_COLORS[sev], text)


def human_rate(bps):
    if bps is None:
        return "-"
    for unit in ("B/s", "KB/s", "MB/s", "GB/s"):
        if abs(bps) < 1000:
            return "%.1f %s" % (bps, unit)
        bps /= 1000.0
    return "%.1f TB/s" % bps


def ts(t):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)) if t else "-"


def table(rows, headers):
    widths = [len(h) for h in headers]
    for r in rows:
        for i, c in enumerate(r):
            widths[i] = max(widths[i], len(_strip(str(c))))
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    out = [line, "  ".join("-" * w for w in widths)]
    for r in rows:
        out.append("  ".join(str(c) + " " * (widths[i] - len(_strip(str(c)))) for i, c in enumerate(r)))
    return "\n".join(out)


def _strip(s):
    import re
    return re.sub(r"\033\[[0-9;]*m", "", s)


# --------------------------------------------------------------------------
def node_label(n, guid):
    if not n:
        return guid
    return n.get("name") or n.get("description") or guid


def topology(store):
    """Arvore: host -> HCA/porta -> switch[porta]; depois interligacoes entre switches."""
    nodes, links = store.nodes(), store.links()
    ports = {(p["node_guid"], p["port_num"]): p for p in store.ports()}
    peers = {}
    for l in links:
        peers.setdefault((l["a_guid"], l["a_port"]), []).append((l["b_guid"], l["b_port"], l["speed"]))
        peers.setdefault((l["b_guid"], l["b_port"]), []).append((l["a_guid"], l["a_port"], l["speed"]))
    hosts = {}
    for g, n in nodes.items():
        if n["node_type"] == "CA":
            hosts.setdefault(n["hostname"] or n["name"] or g, []).append(n)
    out = []
    local_hosts = sorted(h for h, ns in hosts.items() if any(n["is_local"] for n in ns))
    other_hosts = sorted(h for h in hosts if h not in local_hosts)
    for h in local_hosts + other_hosts:
        cas = sorted(hosts[h], key=lambda n: n["name"] or "")
        loc = next((n["location"] for n in cas if n.get("location")), "")
        tag = " [local]" if any(n["is_local"] for n in cas) else ""
        out.append("%s%s%s" % (h, tag, ("  @ " + loc) if loc else ""))
        rows = []
        for n in cas:
            pnums = sorted(set([pn for (g, pn) in ports if g == n["guid"]] + [pn for (g, pn) in peers if g == n["guid"]]))
            for pn in pnums:
                p = ports.get((n["guid"], pn), {})
                pr = peers.get((n["guid"], pn), [])
                rows.append((n, pn, p, pr))
        for i, (n, pn, p, pr) in enumerate(rows):
            last = i == len(rows) - 1
            br = "└─" if last else "├─"
            ca = p.get("ca_name") or (n["name"] or "").split()[-1]
            state = p.get("state") or "?"
            sev = OK if state == "ACTIVE" else CRIT
            ifs = ""
            if p.get("netdevs"):
                ifs = " %s" % p["netdevs"]
                if p.get("ip_addrs"):
                    ifs += " (%s)" % p["ip_addrs"]
            dst = "(sem vizinho conhecido)"
            if pr:
                pg, pp, sp = pr[0]
                dst = "%s [porta %s]%s" % (node_label(nodes.get(pg), pg), pp, (" " + sp) if sp else "")
            out.append("  %s %s/%s  guid %s  lid %s  %s%s" % (
                br, ca, pn, p.get("port_guid") or n["guid"], p.get("lid") if p.get("lid") is not None else "-",
                color(state, sev), ifs))
            out.append("  %s     └──> %s" % ("  " if last else "│ ", dst))
        out.append("")

    switches = sorted((g for g, n in nodes.items() if n["node_type"] == "SW"), key=lambda g: node_label(nodes[g], g))
    if switches:
        out.append("Switches")
        for g in switches:
            n = nodes[g]
            sw_links = [l for l in links if g in (l["a_guid"], l["b_guid"])]
            to_sw = [l for l in sw_links if nodes.get(l["b_guid"] if l["a_guid"] == g else l["a_guid"], {}).get("node_type") == "SW"]
            out.append("  %s  guid %s  lid %s  portas %s  links %d (hosts %d, switches %d)%s" % (
                node_label(n, g), g, n["lid"] if n["lid"] is not None else "-", n["num_ports"] or "?",
                len(sw_links), len(sw_links) - len(to_sw), len(to_sw),
                ("  @ " + n["location"]) if n.get("location") else ""))
            ups = []
            for l in to_sw:
                mine, other, op = (l["a_port"], l["b_guid"], l["b_port"]) if l["a_guid"] == g else (l["b_port"], l["a_guid"], l["a_port"])
                ups.append((mine, other, op, l["speed"]))
            for mine, other, op, sp in sorted(ups):
                out.append("      porta %-3s ──> %s [porta %s]%s" % (mine, node_label(nodes.get(other), other), op,
                                                                    (" " + sp) if sp else ""))
    nh = len(hosts)
    out.append("")
    out.append("Resumo: %d host(s), %d HCA(s), %d switch(es), %d link(s)" % (
        nh, sum(len(v) for v in hosts.values()), len(switches), len(links)))
    return "\n".join(out)


def status(store):
    cs = store.counter_all()
    nodes = store.nodes()
    open_ev = {}
    for e in store.events(open_only=True, limit=10000):
        open_ev[e["scope"]] = max(open_ev.get(e["scope"], 0), e["severity"])
    rows = []
    for scope, (t, data) in sorted(cs.items()):
        _, guid, port = scope.split(":")
        n = nodes.get(guid, {})
        c, r = data.get("c", {}), data.get("r", {})
        errs = sum(c.get(k, 0) for k in SHORT if k != "port_xmit_wait")
        sev = open_ev.get(scope, OK)
        rows.append([
            (n.get("hostname") if n.get("node_type") == "CA" else None) or n.get("name") or guid,
            "%s/%s" % ((n.get("name") or "").split()[-1] if n.get("node_type") == "CA" else "port", port),
            human_rate(r.get("tx_Bps")), human_rate(r.get("rx_Bps")),
            "%d" % c.get("port_xmit_wait", 0), "%.0f" % r.get("xmit_wait_ps", 0) if r else "-",
            "%d" % errs, color(SEV_NAME[sev], sev), ts(t)])
    if not rows:
        return "Sem dados. Execute 'ibmon check' (duas vezes para obter taxas)."
    return table(rows, ["NO", "PORTA", "TX", "RX", "XmitWait(total)", "XmitWait/s", "ERROS(total)", "ESTADO", "COLETA"])


def events(rows):
    if not rows:
        return "Nenhum evento."
    out = []
    for e in rows:
        out.append([color(SEV_NAME.get(e["severity"], "?"), e["severity"]), "aberto" if e["open"] else "fechado",
                    ts(e["first_seen"]), ts(e["last_seen"]), e["count"], e["message"]])
    return table(out, ["SEV", "SITUACAO", "INICIO", "ULTIMO", "N", "MENSAGEM"])


def check_report(res, verbose=False):
    out = []
    inv = res["inventory"]
    if inv["refreshed"]:
        d = inv["detail"] or {}
        out.append("Inventario local atualizado (%s): %d alteracao(oes), %d vizinho(s)" % (
            inv["reason"], d.get("changes", 0), d.get("neighbors", 0)))
    rows = []
    for p in res["ports"]:
        r = p["rates"]
        d = p["deltas"]
        derr = sum(v for k, v in d.items() if k in SHORT and k != "port_xmit_wait")
        rows.append([p["label"], p["state"] or "-", human_rate(r.get("tx_Bps")), human_rate(r.get("rx_Bps")),
                     "%.0f" % r["xmit_wait_ps"] if "xmit_wait_ps" in r else "-",
                     str(derr) if p["dt"] else "-", "%.0fs" % p["dt"] if p["dt"] else "baseline"])
    if rows:
        out.append(table(rows, ["PORTA", "ESTADO", "TX", "RX", "XmitWait/s", "ERROS(Δ)", "INTERVALO"]))
    else:
        out.append("Nenhuma porta InfiniBand encontrada.")
    for a in sorted(res["alerts"], key=lambda a: -a["severity"]):
        out.append(color("[%s] " % SEV_NAME[a["severity"]], a["severity"]) + a["message"])
    if verbose:
        for a in res["info"]:
            out.append("[INFO] " + a["message"])
    out.append("Resultado: " + color(SEV_NAME[res["severity"]], res["severity"]))
    return "\n".join(out)
