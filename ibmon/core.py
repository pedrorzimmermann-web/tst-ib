"""Orquestracao: inventario incremental, descoberta da fabric e verificacao."""

import hashlib
import json
import time

from . import analyze, ibtools
from .naming import friendly_name, hostname_from_desc, load_node_name_map
from .store import canon, port_scope
from .sysfs import SysfsCollector, parse_ip_addr


class Context(object):
    def __init__(self, cfg, store, runner):
        self.cfg = cfg
        self.store = store
        self.runner = runner
        self.sysfs = SysfsCollector(cfg.sysfs_root)
        self.name_map = load_node_name_map(cfg.get("general", "node_name_map"))
        self.locations = cfg.locations()
        self._hcas = None

    def hcas(self):
        if self._hcas is None:
            self._hcas = self.sysfs.list_hcas()
        return self._hcas

    def ib_ports(self):
        """Portas locais InfiniBand (exclui RoCE/Ethernet e portas ignoradas)."""
        for h in self.hcas():
            for p in h["ports"]:
                if p["link_layer"] != "InfiniBand" or self.cfg.is_ignored(h["ca"], p["port"]):
                    continue
                yield h, p

    def local_hostname(self, hca):
        if self.cfg.get("general", "hostname").strip():
            return self.cfg.hostname
        return hostname_from_desc(hca.get("node_desc")) or self.cfg.hostname

    def location_for(self, *names):
        for n in names:
            if n and n in self.locations:
                return self.locations[n]
        return None


# --------------------------------------------------------------------------
# Inventario local (incremental)
# --------------------------------------------------------------------------
def local_fingerprint(ctx):
    items = [(h["ca"], h["node_guid"], p["port"], p["port_guid"], p["state"], p["lid"], p["sm_lid"], p["rate"])
             for h, p in ctx.ib_ports()]
    return hashlib.sha256(json.dumps(items).encode()).hexdigest()[:16]


def inventory_needed(ctx, force=False):
    if force:
        return True, "forcado"
    fp = local_fingerprint(ctx)
    if ctx.store.meta_get("local_fingerprint") != fp:
        return True, "mudanca no estado/LID/taxa das portas"
    last = float(ctx.store.meta_get("local_inventory_ts", "0"))
    if time.time() - last > ctx.cfg.getint("general", "inventory_ttl"):
        return True, "TTL expirado"
    return False, "inalterado"


def refresh_local_inventory(ctx):
    """Atualiza nos/portas locais, interfaces IPoIB/IPs e vizinhos diretos."""
    st, cfg = ctx.store, ctx.cfg
    before = st.changes
    report = {"neighbors": 0, "moved_links": []}
    ifmap = ctx.sysfs.ipoib_interfaces()
    rc, ipout = ctx.runner.run(["ip", "-o", "addr", "show"])
    ips = parse_ip_addr(ipout) if rc == 0 else {}
    do_nb = cfg.getbool("collect", "neighbors")

    for h in ctx.hcas():
        host = ctx.local_hostname(h)
        st.upsert_node(guid=h["node_guid"], node_type="CA",
                       name=friendly_name(h["node_desc"], h["node_guid"], "CA", ctx.name_map),
                       hostname=host, description=h["node_desc"], num_ports=len(h["ports"]),
                       sys_guid=h["sys_guid"], location=ctx.location_for(host, h["node_desc"]) or "",
                       is_local=1)
        for p in h["ports"]:
            if p["link_layer"] != "InfiniBand":
                continue
            netdevs = ifmap.get(p["port_guid"], [])
            addrs = []
            for n in netdevs:
                addrs += ips.get(n, {}).get("ipv4", []) + ips.get(n, {}).get("ipv6", [])
            st.upsert_port(node_guid=h["node_guid"], port_num=p["port"], port_guid=p["port_guid"],
                           ca_name=h["ca"], pci_addr=h["pci"] or "", lid=p["lid"], state=p["state"],
                           phys_state=p["phys_state"], rate=p["rate"], link_layer=p["link_layer"],
                           netdevs=",".join(netdevs), ip_addrs=",".join(addrs))
            if do_nb and p["state"] == "ACTIVE" and not cfg.is_ignored(h["ca"], p["port"]):
                nb = ibtools.query_neighbor(ctx.runner, h["ca"], p["port"])
                if nb:
                    report["neighbors"] += 1
                    name = friendly_name(nb["description"], nb["guid"], nb["node_type"], ctx.name_map)
                    row = dict(guid=nb["guid"], node_type=nb["node_type"], name=name,
                               description=nb["description"], num_ports=nb["num_ports"] or None,
                               lid=nb["lid"], location=ctx.location_for(name) or "")
                    if nb["node_type"] == "CA":
                        row["hostname"] = hostname_from_desc(nb["description"])
                    st.upsert_node(**row)
                    speed = "%s%s" % ((p["rate_info"].get("width") or "").lower(), p["rate_info"].get("speed") or "")
                    _, removed = st.set_link((h["node_guid"], p["port"]), (nb["guid"], nb["remote_port"]),
                                             speed=speed or None, source="local")
                    report["moved_links"] += removed

    st.meta_set("local_fingerprint", local_fingerprint(ctx))
    st.meta_set("local_inventory_ts", int(time.time()))
    report["changes"] = st.changes - before
    return report


# --------------------------------------------------------------------------
# Descoberta completa da fabric (ibnetdiscover)
# --------------------------------------------------------------------------
def fabric_needed(ctx, force=False):
    if force:
        return True
    last = float(ctx.store.meta_get("fabric_ts", "0"))
    return time.time() - last > ctx.cfg.getint("general", "fabric_ttl")


def discover_fabric(ctx, text=None):
    """Executa ibnetdiscover; se o hash nao mudou, nao toca no banco."""
    st = ctx.store
    text = text if text is not None else ibtools.run_ibnetdiscover(ctx.runner)
    if not text:
        return {"ok": False, "error": "ibnetdiscover indisponivel ou falhou (requer root/acesso a umad)"}
    h = ibtools.fabric_hash(text)
    st.meta_set("fabric_ts", int(time.time()))
    if st.meta_get("fabric_hash") == h:
        return {"ok": True, "unchanged": True, "changes": 0}
    before = st.changes
    topo = ibtools.parse_ibnetdiscover(text)
    local = {g for g, n in st.nodes().items() if n["is_local"]}
    for guid, n in topo["nodes"].items():
        name = friendly_name(n["description"], guid, n["node_type"], ctx.name_map)
        row = dict(guid=guid, node_type=n["node_type"], name=name, description=n["description"] or None,
                   num_ports=n["num_ports"], lid=n["lid"])
        if n["node_type"] == "CA":
            row["hostname"] = hostname_from_desc(n["description"])
        loc = ctx.location_for(row.get("hostname"), name)
        if loc:
            row["location"] = loc
        if guid in local:  # dados locais (sysfs) sao mais ricos; nao sobrescrever nome/host
            row = {k: v for k, v in row.items() if k in ("guid", "lid", "num_ports")}
        st.upsert_node(**row)
    for cp in topo["ca_ports"]:
        if cp["node_guid"] in local:
            continue
        st.upsert_port(node_guid=cp["node_guid"], port_num=cp["port"], port_guid=cp["port_guid"],
                       lid=cp["lid"], state="ACTIVE")
    keep, removed = set(), []
    for a_g, a_p, b_g, b_p, speed in topo["links"]:
        _, rm = st.set_link((a_g, a_p), (b_g, b_p), speed=speed, source="fabric")
        removed += rm
        a, b = canon((a_g, a_p), (b_g, b_p))
        keep.add(a + b)
    removed += st.prune_links(keep)
    seen = set()
    removed = [r for r in removed
               if (r["a_guid"], r["a_port"], r["b_guid"], r["b_port"]) not in keep
               and not ((r["a_guid"], r["a_port"], r["b_guid"], r["b_port"]) in seen
                        or seen.add((r["a_guid"], r["a_port"], r["b_guid"], r["b_port"])))]
    st.meta_set("fabric_hash", h)
    alerts = [analyze._alert("link:%s:%s-%s:%s" % (r["a_guid"], r["a_port"], r["b_guid"], r["b_port"]),
                             "link_removed", analyze.WARN, None,
                             "link desapareceu da fabric: %s[%s] <-> %s[%s]" % (
                                 _nm(st, r["a_guid"]), r["a_port"], _nm(st, r["b_guid"]), r["b_port"]))
              for r in removed]
    new = st.sync_events(alerts, scopes=[]) if alerts else []
    # Fecha eventos de "link removido" cujos links voltaram a aparecer
    for e in st.events(open_only=True, limit=100000):
        if e["kind"] == "link_removed":
            a, b = e["scope"][5:].split("-")
            ag, ap = a.rsplit(":", 1)
            bg, bp = b.rsplit(":", 1)
            if (ag, int(ap), bg, int(bp)) in keep:
                st.db.execute("UPDATE events SET open=0, closed_at=? WHERE id=?", (int(time.time()), e["id"]))
    return {"ok": True, "unchanged": False, "changes": st.changes - before,
            "nodes": len(topo["nodes"]), "links": len(topo["links"]), "removed": removed, "new_events": new}


def _nm(st, guid):
    r = st.db.execute("SELECT name FROM nodes WHERE guid=?", (guid,)).fetchone()
    return r[0] if r and r[0] else guid


# --------------------------------------------------------------------------
# Verificacao (rapida, executada periodicamente)
# --------------------------------------------------------------------------
def check(ctx, now=None):
    cfg, st = ctx.cfg, ctx.store
    now = now or time.time()
    thr = cfg.thresholds()
    unit = cfg.getint("general", "data_unit_bytes")
    expected = cfg.getfloat("collect", "expected_rate_gbps")
    hw = cfg.getbool("collect", "hw_counters")

    inv_needed, inv_reason = inventory_needed(ctx)
    inv = refresh_local_inventory(ctx) if inv_needed else None

    alerts, info, scopes, results = [], [], [], []
    for h, p in ctx.ib_ports():
        scope = port_scope(h["node_guid"], p["port"])
        label = "%s %s/%d" % (ctx.local_hostname(h), h["ca"], p["port"])
        scopes.append(scope)
        alerts += analyze.evaluate_port_state(scope, label, p, expected)
        cur = ctx.sysfs.read_counters(h["ca"], p["port"], hw=hw)
        res = _eval_counters(st, scope, label, cur, now, thr, unit, alerts, info)
        res.update(scope=scope, label=label, side="local", state=p["state"], rate=p["rate"])
        results.append(res)

        if cfg.getbool("collect", "peer_counters") and p["state"] == "ACTIVE":
            peer = st.peer_of(h["node_guid"], p["port"])
            if peer:
                pg, pp, _ = peer
                node = st.nodes().get(pg) or {}
                if node.get("lid"):
                    pc = ibtools.query_port_counters(ctx.runner, node["lid"], pp, h["ca"], p["port"])
                    if pc:
                        pscope = port_scope(pg, pp)
                        plabel = "%s porta %d (<- %s)" % (node.get("name") or pg, pp, label)
                        scopes.append(pscope)
                        r2 = _eval_counters(st, pscope, plabel, pc, now, thr, unit, alerts, info)
                        r2.update(scope=pscope, label=plabel, side="peer", state="ACTIVE", rate=p["rate"])
                        results.append(r2)

    new = st.sync_events(alerts, scopes, now)
    st.meta_set("last_check_ts", int(now))
    return {
        "ts": now,
        "inventory": {"refreshed": inv_needed, "reason": inv_reason, "detail": inv},
        "ports": results,
        "alerts": alerts,
        "info": info,
        "new_events": new,
        "severity": max([a["severity"] for a in alerts] or [analyze.OK]),
    }


def _eval_counters(st, scope, label, cur, now, thr, unit, alerts, info):
    prev_ts, prev = st.counter_get(scope)
    prev_counters = (prev or {}).get("c")
    res = {"counters": cur, "deltas": {}, "rates": {}, "dt": None, "reset": False}
    if prev_counters is None:
        info += analyze.baseline_alerts(scope, label, cur)
    else:
        dt = now - prev_ts
        d, reset = analyze.deltas(prev_counters, cur)
        res.update(deltas=d, dt=dt, reset=reset, rates=analyze.rates(d, dt, unit))
        alerts += analyze.evaluate_counters(scope, label, cur, d, dt, thr)
    alerts += analyze.saturation_alerts(scope, label, cur)
    st.counter_put(scope, now, {"c": cur, "r": {k: round(v, 2) for k, v in res["rates"].items()},
                                "d": {k: v for k, v in res["deltas"].items() if v and k in analyze.SATURATION}})
    return res
