"""Calculo de deltas/taxas e avaliacao de limiares."""

OK, WARN, CRIT = 0, 1, 2
SEV_NAME = {OK: "OK", WARN: "WARNING", CRIT: "CRITICAL"}

# Contadores de erro do PortCounters SATURAM (nao dao a volta) no valor maximo.
SATURATION = {
    "symbol_error": 0xFFFF,
    "link_error_recovery": 0xFF,
    "link_downed": 0xFF,
    "port_rcv_errors": 0xFFFF,
    "port_rcv_remote_physical_errors": 0xFFFF,
    "port_rcv_switch_relay_errors": 0xFFFF,
    "port_xmit_discards": 0xFFFF,
    "port_xmit_constraint_errors": 0xFF,
    "port_rcv_constraint_errors": 0xFF,
    "local_link_integrity_errors": 0xF,
    "excessive_buffer_overrun_errors": 0xF,
    "VL15_dropped": 0xFFFF,
}

TRAFFIC = ("port_xmit_data", "port_rcv_data", "port_xmit_packets", "port_rcv_packets")

SHORT = {
    "symbol_error": "SymbolErr",
    "link_error_recovery": "LinkErrRecov",
    "link_downed": "LinkDowned",
    "port_rcv_errors": "RcvErr",
    "port_rcv_remote_physical_errors": "RcvRemotePhysErr",
    "port_rcv_switch_relay_errors": "RcvSwRelayErr",
    "port_xmit_discards": "XmitDiscards",
    "port_xmit_constraint_errors": "XmitConstrErr",
    "port_rcv_constraint_errors": "RcvConstrErr",
    "local_link_integrity_errors": "LocalLinkIntegErr",
    "excessive_buffer_overrun_errors": "ExcBufOverrun",
    "VL15_dropped": "VL15Dropped",
    "port_xmit_wait": "PortXmitWait",
}


def deltas(prev, cur):
    """Delta por contador. Se o valor diminuiu, houve reset (perfquery -R / reboot): delta=cur."""
    out, reset = {}, False
    for k, v in cur.items():
        p = prev.get(k) if prev else None
        if p is None:
            continue
        if v >= p:
            out[k] = v - p
        else:
            out[k] = v
            reset = True
    return out, reset


def rates(d, dt, data_unit_bytes=4):
    if not dt or dt <= 0:
        return {}
    r = {}
    if "port_xmit_data" in d:
        r["tx_Bps"] = d["port_xmit_data"] * data_unit_bytes / dt
    if "port_rcv_data" in d:
        r["rx_Bps"] = d["port_rcv_data"] * data_unit_bytes / dt
    if "port_xmit_packets" in d:
        r["tx_pps"] = d["port_xmit_packets"] / dt
    if "port_rcv_packets" in d:
        r["rx_pps"] = d["port_rcv_packets"] / dt
    if "port_xmit_wait" in d:
        r["xmit_wait_ps"] = d["port_xmit_wait"] / dt
    return r


def _alert(scope, kind, sev, value, msg):
    return {"scope": scope, "kind": kind, "severity": sev, "value": value, "message": msg}


def evaluate_counters(scope, label, cur, d, dt, thresholds):
    """Gera alertas por limiar, aplicado ao delta ou a taxa (chaves *_per_sec)."""
    alerts = []
    for key, (warn, crit) in thresholds.items():
        if key.endswith("_per_sec"):
            name = key[:-len("_per_sec")]
            if name not in d or not dt:
                continue
            val = d[name] / dt
            unit = "/s"
        else:
            name = key
            if name not in d:
                continue
            val = d[name]
            unit = " no intervalo"
        sev = CRIT if val >= crit else WARN if val >= warn else OK
        if sev:
            alerts.append(_alert(scope, name, sev, val, "%s: %s=%s%s (limiar %g/%g)" % (
                label, SHORT.get(name, name), _fmt_num(val), unit, warn, crit)))
    return alerts


def saturation_alerts(scope, label, cur):
    alerts = []
    for name, maxv in SATURATION.items():
        if cur.get(name) == maxv:
            alerts.append(_alert(scope, name + ":saturated", WARN, maxv,
                                 "%s: contador %s saturado (%d); zere com 'perfquery -R' para voltar a medir"
                                 % (label, SHORT.get(name, name), maxv)))
    return alerts


def evaluate_port_state(scope, label, port, expected_gbps=0):
    alerts = []
    state, phys = port.get("state"), port.get("phys_state")
    if state != "ACTIVE":
        alerts.append(_alert(scope, "state", CRIT, None, "%s: porta %s (phys %s)" % (label, state, phys)))
    elif phys and phys != "LinkUp":
        alerts.append(_alert(scope, "phys_state", CRIT, None, "%s: estado fisico %s" % (label, phys)))
    ri = port.get("rate_info") or {}
    if state == "ACTIVE":
        if ri.get("width") and ri["width"] not in ("4X", "8X", "12X"):
            alerts.append(_alert(scope, "width", WARN, None,
                                 "%s: largura degradada %s (%s)" % (label, ri["width"], port.get("rate"))))
        if expected_gbps and ri.get("gbps") and ri["gbps"] < expected_gbps:
            alerts.append(_alert(scope, "rate", WARN, ri["gbps"], "%s: taxa %s abaixo do esperado (%g Gb/s)"
                                 % (label, port.get("rate"), expected_gbps)))
    return alerts


def baseline_alerts(scope, label, cur):
    """Primeira coleta (sem delta): apenas sinaliza contadores de erro nao-zero como informativo."""
    nz = ["%s=%d" % (SHORT[k], cur[k]) for k in SATURATION if cur.get(k)]
    if not nz:
        return []
    return [_alert(scope, "baseline", OK, None, "%s: contadores acumulados: %s" % (label, ", ".join(nz)))]


def _fmt_num(v):
    if isinstance(v, float) and not v.is_integer():
        return "%.1f" % v
    return "%d" % v
