#!/usr/bin/env python3
"""Gera um ambiente simulado (sysfs + saidas de comandos) para testar o ibmon sem hardware.

Topologia simulada:
    node01 mlx5_0/1 --> leaf01[1]      node01 mlx5_1/1 --> leaf02[1]
    node02 mlx5_0/1 --> leaf01[2]
    leaf01[35] --> spine01[1]          leaf02[35] --> spine01[2]
    leaf01[36] --> leaf02[36]

Uso:
    python3 tools/mkdemo.py create DIR
    python3 tools/mkdemo.py traffic DIR [--errors] [--congestion] [--down mlx5_1]
    ibmon --fixtures DIR --db DIR/ibmon.db check
"""

import argparse
import os
import random
import sys

N01_0 = "0c42a10300b2c3d4"
N01_1 = "0c42a10300b2c3e4"
N02_0 = "0c42a10300b2d000"
LEAF1 = "248a070300f0a1b0"
LEAF2 = "248a070300f0a2b0"
SPINE = "248a070300f0aab0"

LOCAL = [
    # ca, node_guid, lid, sw_guid, sw_port, sw_lid, sw_desc, ifname, ip, pci
    ("mlx5_0", N01_0, 5, LEAF1, 1, 1, "MF0;leaf01:MQM8700/U1", "ib0", "10.10.0.1/16", "0000:3b:00.0"),
    ("mlx5_1", N01_1, 6, LEAF2, 1, 2, "MF0;leaf02:MQM8700/U1", "ib1", "10.11.0.1/16", "0000:86:00.0"),
]

COUNTERS = ["symbol_error", "link_error_recovery", "link_downed", "port_rcv_errors",
            "port_rcv_remote_physical_errors", "port_rcv_switch_relay_errors", "port_xmit_discards",
            "port_xmit_constraint_errors", "port_rcv_constraint_errors", "local_link_integrity_errors",
            "excessive_buffer_overrun_errors", "VL15_dropped", "port_xmit_wait", "port_xmit_data",
            "port_rcv_data", "port_xmit_packets", "port_rcv_packets", "unicast_xmit_packets",
            "unicast_rcv_packets", "multicast_xmit_packets", "multicast_rcv_packets"]
HW = ["out_of_buffer", "out_of_sequence", "packet_seq_err", "local_ack_timeout_err", "rnr_nak_retry_err",
      "req_cqe_error", "resp_cqe_error", "duplicate_request"]


def colon_guid(g):
    return ":".join(g[i:i + 4] for i in range(0, 16, 4))


def w(path, content):
    d = os.path.dirname(path)
    if not os.path.isdir(d):
        os.makedirs(d)
    with open(path, "w") as fh:
        fh.write(str(content) + "\n")


def fx(root, args, content):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    from ibmon.runner import fixture_name
    w(os.path.join(root, "cmd", fixture_name(args)), content)


def port_guid(node_guid):
    return "%016x" % (int(node_guid, 16) + 1)


def perf_text(lid, port, vals, ext=False):
    names = {"symbol_error": "SymbolErrorCounter", "link_error_recovery": "LinkErrorRecoveryCounter",
             "link_downed": "LinkDownedCounter", "port_rcv_errors": "PortRcvErrors",
             "port_rcv_remote_physical_errors": "PortRcvRemotePhysicalErrors",
             "port_rcv_switch_relay_errors": "PortRcvSwitchRelayErrors", "port_xmit_discards": "PortXmitDiscards",
             "port_xmit_constraint_errors": "PortXmitConstraintErrors",
             "port_rcv_constraint_errors": "PortRcvConstraintErrors",
             "local_link_integrity_errors": "LocalLinkIntegrityErrors",
             "excessive_buffer_overrun_errors": "ExcessiveBufferOverrunErrors", "VL15_dropped": "VL15Dropped",
             "port_xmit_wait": "PortXmitWait", "port_xmit_data": "PortXmitData", "port_rcv_data": "PortRcvData",
             "port_xmit_packets": "PortXmitPkts", "port_rcv_packets": "PortRcvPkts"}
    hdr = "# Port %scounters: Lid %d port %d (CapMask: 0x5A00)" % ("extended " if ext else "", lid, port)
    lines = [hdr, "PortSelect:......................%d" % port, "CounterSelect:...................0x0000"]
    keys = ["port_xmit_data", "port_rcv_data", "port_xmit_packets", "port_rcv_packets"] if ext else list(names)
    for k in keys:
        n = names[k]
        lines.append("%s:%s%d" % (n, "." * max(1, 33 - len(n)), vals.get(k, 0)))
    return "\n".join(lines)


def create(root):
    sysd = os.path.join(root, "sys")
    ipout = ["1: lo    inet 127.0.0.1/8 scope host lo\\       valid_lft forever preferred_lft forever"]
    for i, (ca, ng, lid, swg, swp, swlid, swdesc, ifn, ip, pci) in enumerate(LOCAL):
        base = os.path.join(sysd, "class", "infiniband", ca)
        pcidir = os.path.join(sysd, "devices", "pci0000:00", pci)
        os.makedirs(pcidir)
        os.makedirs(base)
        os.symlink(os.path.relpath(pcidir, base), os.path.join(base, "device"))
        w(os.path.join(base, "node_guid"), colon_guid(ng))
        w(os.path.join(base, "sys_image_guid"), colon_guid(ng))
        w(os.path.join(base, "node_desc"), "node01 %s" % ca)
        w(os.path.join(base, "node_type"), "1: CA")
        w(os.path.join(base, "fw_ver"), "20.39.1002")
        w(os.path.join(base, "hca_type"), "MT4123")
        w(os.path.join(base, "board_id"), "MT_0000000223")
        p = os.path.join(base, "ports", "1")
        pg = port_guid(ng)
        w(os.path.join(p, "state"), "4: ACTIVE")
        w(os.path.join(p, "phys_state"), "5: LinkUp")
        w(os.path.join(p, "rate"), "200 Gb/sec (4X HDR)")
        w(os.path.join(p, "lid"), hex(lid))
        w(os.path.join(p, "sm_lid"), "0x1")
        w(os.path.join(p, "link_layer"), "InfiniBand")
        w(os.path.join(p, "gids", "0"), "fe80:0000:0000:0000:" + colon_guid(pg))
        for c in COUNTERS:
            w(os.path.join(p, "counters", c), 0)
        for c in HW:
            w(os.path.join(p, "hw_counters", c), 0)
        nd = os.path.join(sysd, "class", "net", ifn)
        w(os.path.join(nd, "type"), "32")
        w(os.path.join(nd, "address"), "00:00:10:87:fe:80:00:00:00:00:00:00:" +
          ":".join(pg[j:j + 2] for j in range(0, 16, 2)))
        ipout.append("%d: %s    inet %s brd 10.%d.255.255 scope global %s\\       valid_lft forever preferred_lft forever"
                     % (i + 4, ifn, ip, 10 + i, ifn))
        # vizinho via smpquery
        dr = ["smpquery", "-C", ca, "-P", "1", "-D"]
        fx(root, dr + ["nodeinfo", "0,1"], "\n".join([
            "# Node info: DR path slid 65535; dlid 65535; 0,1",
            "BaseVers:........................1", "ClassVers:.......................1",
            "NodeType:........................Switch", "NumPorts:........................40",
            "SystemGuid:......................0x%s" % swg, "Guid:............................0x%s" % swg,
            "PortGuid:........................0x%s" % swg, "PartCap:.........................8",
            "DevId:...........................0xd2f0", "Revision:........................0x000000a0",
            "LocalPort:.......................%d" % swp, "VendorId:........................0x0002c9"]))
        fx(root, dr + ["nodedesc", "0,1"], "Node Description:.................%s" % swdesc)
        fx(root, dr + ["portinfo", "0,1", "0"], "# Port info: DR path slid 65535; dlid 65535; 0,1 port 0\n"
                                                "Lid:.............................%d\nSMLid:...........................1" % swlid)
        set_peer(root, ca, swlid, swp, {})
    fx(root, ["ip", "-o", "addr", "show"], "\n".join(ipout))
    fx(root, ["ibnetdiscover"], IBNETDISCOVER)
    print("ambiente simulado criado em %s" % root)


def set_peer(root, ca, swlid, swp, vals):
    base = ["perfquery", "-C", ca, "-P", "1"]
    fx(root, base + [str(swlid), str(swp)], perf_text(swlid, swp, vals))
    fx(root, base + ["-x", str(swlid), str(swp)], perf_text(swlid, swp, vals, ext=True))


def _bump(path, inc):
    with open(path) as fh:
        v = int(fh.read().strip() or 0)
    w(path, v + int(inc))


def traffic(root, errors=False, congestion=False, down=None, seconds=60):
    sysd = os.path.join(root, "sys", "class", "infiniband")
    for ca in sorted(os.listdir(sysd)):
        p = os.path.join(sysd, ca, "ports", "1")
        if down == ca:
            w(os.path.join(p, "state"), "1: DOWN")
            w(os.path.join(p, "phys_state"), "3: Disabled")
            continue
        gbps = random.uniform(2, 20) * 1e9
        words = gbps * seconds / 4
        for c, inc in (("port_xmit_data", words), ("port_rcv_data", words * 0.9),
                       ("port_xmit_packets", words / 1024), ("port_rcv_packets", words / 1100),
                       ("unicast_xmit_packets", words / 1024), ("unicast_rcv_packets", words / 1100)):
            _bump(os.path.join(p, "counters", c), inc)
        if congestion:
            _bump(os.path.join(p, "counters", "port_xmit_wait"), 2e6 * seconds)
        if errors and ca == "mlx5_0":
            _bump(os.path.join(p, "counters", "symbol_error"), 150)
            _bump(os.path.join(p, "counters", "port_rcv_errors"), 7)
            _bump(os.path.join(p, "hw_counters", "local_ack_timeout_err"), 3)
    # contadores da porta do switch (lado remoto)
    for ca, _, _, _, swp, swlid, _, _, _, _ in LOCAL:
        vals = {"port_xmit_data": random.randint(10 ** 9, 10 ** 10), "port_rcv_data": random.randint(10 ** 9, 10 ** 10)}
        if errors and ca == "mlx5_1":
            vals["port_xmit_discards"] = 500
        set_peer(root, ca, swlid, swp, vals)
    print("contadores incrementados (%s)" % ", ".join(
        x for x, f in (("erros", errors), ("congestionamento", congestion), ("down " + str(down), down)) if f) or "trafego")


IBNETDISCOVER = """#
# Topology file: generated on Wed Nov 18 10:00:00 2026
#
# Initiated from node 0c42a10300b2c3d4 port 0c42a10300b2c3d5

vendid=0x2c9
devid=0xd2f0
sysimgguid=0x248a070300f0aab0
switchguid=0x248a070300f0aab0(248a070300f0aab0)
Switch	40 "S-248a070300f0aab0"		# "MF0;spine01:MQM8700/U1" enhanced port 0 lid 3 lmc 0
[1]	"S-248a070300f0a1b0"[35]		# "MF0;leaf01:MQM8700/U1" lid 1 4xHDR
[2]	"S-248a070300f0a2b0"[35]		# "MF0;leaf02:MQM8700/U1" lid 2 4xHDR

vendid=0x2c9
devid=0xd2f0
sysimgguid=0x248a070300f0a1b0
switchguid=0x248a070300f0a1b0(248a070300f0a1b0)
Switch	40 "S-248a070300f0a1b0"		# "MF0;leaf01:MQM8700/U1" enhanced port 0 lid 1 lmc 0
[1]	"H-0c42a10300b2c3d4"[1](c42a10300b2c3d5) 		# "node01 mlx5_0" lid 5 4xHDR
[2]	"H-0c42a10300b2d000"[1](c42a10300b2d001) 		# "node02 mlx5_0" lid 7 4xHDR
[35]	"S-248a070300f0aab0"[1]		# "MF0;spine01:MQM8700/U1" lid 3 4xHDR
[36]	"S-248a070300f0a2b0"[36]		# "MF0;leaf02:MQM8700/U1" lid 2 4xHDR

vendid=0x2c9
devid=0xd2f0
sysimgguid=0x248a070300f0a2b0
switchguid=0x248a070300f0a2b0(248a070300f0a2b0)
Switch	40 "S-248a070300f0a2b0"		# "MF0;leaf02:MQM8700/U1" enhanced port 0 lid 2 lmc 0
[1]	"H-0c42a10300b2c3e4"[1](c42a10300b2c3e5) 		# "node01 mlx5_1" lid 6 4xHDR
[35]	"S-248a070300f0aab0"[2]		# "MF0;spine01:MQM8700/U1" lid 3 4xHDR
[36]	"S-248a070300f0a1b0"[36]		# "MF0;leaf01:MQM8700/U1" lid 1 4xHDR

vendid=0x2c9
devid=0x101b
sysimgguid=0x0c42a10300b2c3d4
caguid=0x0c42a10300b2c3d4
Ca	1 "H-0c42a10300b2c3d4"		# "node01 mlx5_0"
[1](c42a10300b2c3d5) 	"S-248a070300f0a1b0"[1]		# lid 5 lmc 0 "MF0;leaf01:MQM8700/U1" lid 1 4xHDR

vendid=0x2c9
devid=0x101b
sysimgguid=0x0c42a10300b2c3e4
caguid=0x0c42a10300b2c3e4
Ca	1 "H-0c42a10300b2c3e4"		# "node01 mlx5_1"
[1](c42a10300b2c3e5) 	"S-248a070300f0a2b0"[1]		# lid 6 lmc 0 "MF0;leaf02:MQM8700/U1" lid 2 4xHDR

vendid=0x2c9
devid=0x101b
sysimgguid=0x0c42a10300b2d000
caguid=0x0c42a10300b2d000
Ca	1 "H-0c42a10300b2d000"		# "node02 mlx5_0"
[1](c42a10300b2d001) 	"S-248a070300f0a1b0"[2]		# lid 7 lmc 0 "MF0;leaf01:MQM8700/U1" lid 1 4xHDR
"""


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    c = sub.add_parser("create")
    c.add_argument("dir")
    t = sub.add_parser("traffic")
    t.add_argument("dir")
    t.add_argument("--errors", action="store_true")
    t.add_argument("--congestion", action="store_true")
    t.add_argument("--down", metavar="CA")
    a = ap.parse_args()
    if a.cmd == "create":
        if os.path.exists(a.dir) and os.listdir(a.dir):
            sys.exit("diretorio nao vazio: %s" % a.dir)
        create(a.dir)
    elif a.cmd == "traffic":
        traffic(a.dir, a.errors, a.congestion, a.down)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
