"""Testes sem hardware: python3 -m unittest discover -s tests -v"""

import io
import os
import shutil
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import mkdemo  # noqa: E402
from ibmon import analyze, cli, core, ibtools  # noqa: E402
from ibmon.config import Config  # noqa: E402
from ibmon.runner import Runner, parse_dotted  # noqa: E402
from ibmon.store import Store  # noqa: E402
from ibmon.sysfs import SysfsCollector, norm_guid, parse_ip_addr, parse_rate  # noqa: E402


class Env(object):
    def __init__(self, conf=""):
        self.dir = tempfile.mkdtemp(prefix="ibmon-test-")
        self.fx = os.path.join(self.dir, "fx")
        with redirect_stdout(io.StringIO()):
            mkdemo.create(self.fx)
        self.conf = os.path.join(self.dir, "ibmon.conf")
        with open(self.conf, "w") as fh:
            fh.write(conf)
        self.db = os.path.join(self.dir, "ibmon.db")
        self.stores = []

    def ctx(self):
        cfg = Config(self.conf)
        cfg.set("general", "sysfs_root", os.path.join(self.fx, "sys"))
        self.stores.append(Store(self.db))
        return core.Context(cfg, self.stores[-1], Runner(fixtures=self.fx))

    def cli(self, *args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["--no-color", "-c", self.conf, "--fixtures", self.fx, "--db", self.db] + list(args))
        return rc, buf.getvalue()

    def close(self):
        for s in self.stores:
            s.close()
        shutil.rmtree(self.dir)


class ParserTests(unittest.TestCase):
    def test_guid(self):
        self.assertEqual(norm_guid("0c42:a103:00b2:c3d4"), "0x0c42a10300b2c3d4")
        self.assertEqual(norm_guid("c42a10300b2c3d5"), "0x0c42a10300b2c3d5")
        self.assertEqual(norm_guid("0x248a070300f0a1b0(248a070300f0a1b0)"), "0x248a070300f0a1b0")

    def test_rate(self):
        r = parse_rate("100 Gb/sec (4X EDR)")
        self.assertEqual((r["gbps"], r["width"], r["speed"]), (100.0, "4X", "EDR"))
        self.assertEqual(parse_rate("25 Gb/sec (1X EDR)")["width"], "1X")

    def test_ip(self):
        out = parse_ip_addr("4: ib0    inet 10.0.0.1/16 brd 10.0.255.255 scope global ib0\\ valid\n"
                            "4: ib0    inet6 fe80::1/64 scope link\n"
                            "5: ib0.8001@ib0    inet 10.1.0.1/16 scope global ib0.8001\n")
        self.assertEqual(out["ib0"]["ipv4"], ["10.0.0.1/16"])
        self.assertEqual(out["ib0"]["ipv6"], [])
        self.assertIn("ib0.8001", out)

    def test_dotted(self):
        d = parse_dotted("# hdr\nNode Description:.........MF0;sw:X/U1\nLocalPort:....3")
        self.assertEqual(d["Node Description"], "MF0;sw:X/U1")
        self.assertEqual(d["LocalPort"], "3")

    def test_ibnetdiscover(self):
        t = ibtools.parse_ibnetdiscover(mkdemo.IBNETDISCOVER)
        self.assertEqual(len(t["nodes"]), 6)
        self.assertEqual(len(t["links"]), 6)
        sw = t["nodes"]["0x248a070300f0a1b0"]
        self.assertEqual((sw["node_type"], sw["lid"], sw["num_ports"]), ("SW", 1, 40))
        self.assertIn(("0x0c42a10300b2c3d4", 1, "0x248a070300f0a1b0", 1, "4xHDR"), t["links"])
        self.assertEqual(len(t["ca_ports"]), 3)


class AnalyzeTests(unittest.TestCase):
    def test_deltas_reset(self):
        d, reset = analyze.deltas({"a": 10, "b": 5}, {"a": 15, "b": 2})
        self.assertEqual(d, {"a": 5, "b": 2})
        self.assertTrue(reset)

    def test_thresholds(self):
        thr = {"symbol_error": (10, 100), "port_xmit_wait_per_sec": (100, 1000)}
        a = analyze.evaluate_counters("s", "x", {}, {"symbol_error": 50, "port_xmit_wait": 60000}, 60, thr)
        sev = {x["kind"]: x["severity"] for x in a}
        self.assertEqual(sev, {"symbol_error": analyze.WARN, "port_xmit_wait": analyze.CRIT})

    def test_saturation(self):
        a = analyze.saturation_alerts("s", "x", {"symbol_error": 65535})
        self.assertEqual(a[0]["kind"], "symbol_error:saturated")

    def test_degraded_width(self):
        p = {"state": "ACTIVE", "phys_state": "LinkUp", "rate": "50 Gb/sec (1X HDR)",
             "rate_info": parse_rate("50 Gb/sec (1X HDR)")}
        kinds = [a["kind"] for a in analyze.evaluate_port_state("s", "x", p, expected_gbps=200)]
        self.assertEqual(kinds, ["width", "rate"])


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.env = Env("[collect]\npeer_counters = yes\n[locations]\nnode01 = R1 U10\n")

    def tearDown(self):
        self.env.close()

    def test_sysfs(self):
        hcas = SysfsCollector(os.path.join(self.env.fx, "sys")).list_hcas()
        self.assertEqual([h["ca"] for h in hcas], ["mlx5_0", "mlx5_1"])
        self.assertEqual(hcas[0]["pci"], "0000:3b:00.0")
        self.assertEqual(hcas[0]["ports"][0]["port_guid"], "0x0c42a10300b2c3d5")
        ifs = SysfsCollector(os.path.join(self.env.fx, "sys")).ipoib_interfaces()
        self.assertEqual(ifs["0x0c42a10300b2c3d5"], ["ib0"])

    def test_inventory_incremental(self):
        ctx = self.env.ctx()
        self.assertTrue(core.inventory_needed(ctx)[0])
        r = core.refresh_local_inventory(ctx)
        self.assertEqual(r["neighbors"], 2)
        self.assertGreater(r["changes"], 0)
        self.assertFalse(core.inventory_needed(ctx)[0])
        self.assertEqual(core.refresh_local_inventory(ctx)["changes"], 0)  # nada muda -> nada gravado
        port = [p for p in ctx.store.ports() if p["ca_name"] == "mlx5_0"][0]
        self.assertEqual((port["netdevs"], port["ip_addrs"], port["lid"]), ("ib0", "10.10.0.1/16", 5))
        self.assertEqual(ctx.store.peer_of("0x0c42a10300b2c3d4", 1)[:2], ("0x248a070300f0a1b0", 1))
        self.assertEqual(ctx.store.nodes()["0x0c42a10300b2c3d4"]["location"], "R1 U10")

    def test_fabric_hash_skip(self):
        ctx = self.env.ctx()
        r1 = core.discover_fabric(ctx)
        self.assertEqual((r1["nodes"], r1["links"]), (6, 6))
        r2 = core.discover_fabric(ctx)
        self.assertTrue(r2["unchanged"])

    def test_link_removed(self):
        ctx = self.env.ctx()
        core.discover_fabric(ctx)
        text = "\n".join(l for l in mkdemo.IBNETDISCOVER.splitlines() if "[36]" not in l)
        r = core.discover_fabric(ctx, text)
        self.assertEqual(len(r["removed"]), 1)
        self.assertEqual([e["kind"] for e in ctx.store.events(open_only=True)], ["link_removed"])
        core.discover_fabric(ctx, mkdemo.IBNETDISCOVER)  # link volta -> evento fecha
        self.assertEqual(len(ctx.store.events(open_only=True)), 0)

    def test_check_cycle(self):
        ctx = self.env.ctx()
        r = core.check(ctx, now=time.time() - 60)
        self.assertEqual(r["severity"], analyze.OK)
        self.assertTrue(all(p["dt"] is None for p in r["ports"]))
        with redirect_stdout(io.StringIO()):
            mkdemo.traffic(self.env.fx, errors=True, congestion=True)
        r = core.check(ctx)
        self.assertEqual(r["severity"], analyze.CRIT)
        local0 = [p for p in r["ports"] if p["label"].endswith("mlx5_0/1")][0]
        self.assertGreater(local0["rates"]["tx_Bps"], 1e9)
        kinds = {(a["scope"].split(":")[1], a["kind"]) for a in r["alerts"]}
        self.assertIn(("0x0c42a10300b2c3d4", "symbol_error"), kinds)
        self.assertIn(("0x0c42a10300b2c3d4", "port_xmit_wait"), kinds)
        self.assertIn(("0x248a070300f0a2b0", "port_xmit_discards"), kinds)  # lado do switch
        n_open = len(ctx.store.events(open_only=True))
        # sem novos erros -> eventos de contador fecham, sem novos registros
        with redirect_stdout(io.StringIO()):
            mkdemo.traffic(self.env.fx)
        r = core.check(ctx)
        self.assertEqual(r["severity"], analyze.OK)
        self.assertEqual(len(ctx.store.events(open_only=True)), 0)
        self.assertEqual(len(ctx.store.events(limit=1000)), n_open)

    def test_port_down(self):
        ctx = self.env.ctx()
        core.check(ctx)
        with redirect_stdout(io.StringIO()):
            mkdemo.traffic(self.env.fx, down="mlx5_1")
        ctx._hcas = None
        r = core.check(ctx)
        self.assertTrue(r["inventory"]["refreshed"])
        self.assertIn("state", [a["kind"] for a in r["alerts"]])

    def test_cli_and_exports(self):
        rc, out = self.env.cli("discover", "--fabric")
        self.assertEqual(rc, 0)
        self.env.cli("check")
        rc, out = self.env.cli("topo")
        self.assertIn("leaf01 [porta 1]", out)
        self.assertIn("spine01", out)
        rc, out = self.env.cli("export", "-f", "json")
        self.assertIn('"schema": "ibmon.graph/1"', out)
        rc, out = self.env.cli("export", "-f", "dot")
        self.assertTrue(out.startswith("graph ibfabric"))
        rc, out = self.env.cli("export", "-f", "prom")
        self.assertIn("ibmon_open_events", out)
        rc, out = self.env.cli("prune")
        self.assertIn("eventos removidos: 0", out)


if __name__ == "__main__":
    unittest.main()
