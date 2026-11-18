"""Interface de linha de comando: ibmon <comando> [opcoes]."""

import argparse
import json
import os
import sys
import time

from . import __version__, core, export, render
from .analyze import CRIT, OK, SEV_NAME
from .config import Config
from .runner import Runner
from .store import Store

TOOLS = ("smpquery", "perfquery", "ibnetdiscover", "ip")


def _ctx(args):
    cfg = Config(args.config)
    if args.fixtures:
        cfg.set("general", "sysfs_root", os.path.join(args.fixtures, "sys"))
    if args.db:
        cfg.set("general", "db_path", args.db)
    runner = Runner(timeout=cfg.getint("general", "command_timeout"), fixtures=args.fixtures)
    store = Store(cfg.db_path)
    return core.Context(cfg, store, runner)


def _print_json(obj):
    print(json.dumps(obj, indent=2, sort_keys=True, default=str))


# ---- comandos --------------------------------------------------------------
def cmd_discover(ctx, args):
    rc = OK
    need, reason = core.inventory_needed(ctx, force=args.force)
    if need:
        r = core.refresh_local_inventory(ctx)
        print("Inventario local: %s -> %d alteracao(oes), %d vizinho(s) identificado(s)" % (
            reason, r["changes"], r["neighbors"]))
        for l in r["moved_links"]:
            print("  link substituido (cabo movido?): %s[%s] <-> %s[%s]" % (
                l["a_guid"], l["a_port"], l["b_guid"], l["b_port"]))
    else:
        print("Inventario local: inalterado (use --force para refazer)")
    if args.fabric or (args.auto_fabric and core.fabric_needed(ctx)):
        text = None
        if args.from_file:
            with open(args.from_file) as fh:
                text = fh.read()
        r = core.discover_fabric(ctx, text)
        if not r["ok"]:
            print("Fabric: ERRO - %s" % r["error"], file=sys.stderr)
            rc = CRIT
        elif r["unchanged"]:
            print("Fabric: topologia inalterada (hash igual), nada gravado")
        else:
            print("Fabric: %d no(s), %d link(s), %d alteracao(oes) gravada(s), %d link(s) removido(s)" % (
                r["nodes"], r["links"], r["changes"], len(r["removed"])))
    ctx.store.commit()
    return rc


def cmd_check(ctx, args):
    worst = OK
    while True:
        res = core.check(ctx)
        ctx.store.commit()
        prom = ctx.cfg.get("general", "prom_textfile")
        if prom:
            export.write_atomic(prom, export.prometheus(ctx.store))
        if args.json:
            _print_json(res)
        elif not args.quiet or res["severity"] > OK:
            if args.watch:
                print("\n=== %s ===" % render.ts(res["ts"]))
            print(render.check_report(res, verbose=args.verbose))
        worst = max(worst, res["severity"])
        if not args.watch:
            break
        try:
            time.sleep(args.watch)
        except KeyboardInterrupt:
            break
        ctx._hcas = None  # reler estado das portas na proxima iteracao
    if ctx.runner.calls and args.verbose:
        print("comandos externos executados: %d" % len(ctx.runner.calls))
    return res["severity"] if not args.watch else worst


def cmd_status(ctx, args):
    if args.json:
        _print_json({s: d for s, (t, d) in ctx.store.counter_all().items()})
    else:
        print(render.status(ctx.store))
    return OK


def cmd_topo(ctx, args):
    if args.json:
        _print_json(export.graph(ctx.store))
    else:
        print(render.topology(ctx.store))
    return OK


def cmd_events(ctx, args):
    sev = {"warning": 1, "critical": 2}.get(args.severity, 0)
    rows = ctx.store.events(open_only=not args.all, limit=args.limit, min_severity=sev)
    if args.json:
        _print_json([dict(r) for r in rows])
    else:
        print(render.events(rows))
    return OK


def cmd_export(ctx, args):
    fmt = args.format
    content = {"json": lambda s: json.dumps(export.graph(s), indent=2, default=str),
               "dot": export.dot, "prom": export.prometheus}[fmt](ctx.store)
    if args.output:
        export.write_atomic(args.output, content)
        print("gravado: %s" % args.output)
    else:
        sys.stdout.write(content if content.endswith("\n") else content + "\n")
    return OK


def cmd_prune(ctx, args):
    n = ctx.store.prune(ctx.cfg.getint("general", "events_retention_days"),
                        ctx.cfg.getint("general", "events_max_rows"))
    ctx.store.commit()
    if args.vacuum:
        ctx.store.vacuum()
    size = os.path.getsize(ctx.cfg.db_path) if os.path.exists(ctx.cfg.db_path) else 0
    print("eventos removidos: %d; tamanho do banco: %.1f KiB" % (n, size / 1024.0))
    return OK


def cmd_doctor(ctx, args):
    ok = True
    print("ibmon %s  python %s" % (__version__, sys.version.split()[0]))
    print("config: %s" % (ctx.cfg.path or "(padroes embutidos)"))
    print("banco : %s" % ctx.cfg.db_path)
    sysok = ctx.sysfs.present()
    print("sysfs : %s %s" % (ctx.sysfs.ib, "OK" if sysok else "AUSENTE (driver mlx5_ib carregado?)"))
    ok &= sysok
    for h in ctx.hcas():
        print("  %s guid %s fw %s pci %s desc '%s'" % (h["ca"], h["node_guid"], h["fw_ver"], h["pci"], h["node_desc"]))
        for p in h["ports"]:
            print("    porta %d %s/%s %s lid %s %s" % (p["port"], p["state"], p["phys_state"], p["rate"],
                                                    p["lid"], p["link_layer"]))
    for t in TOOLS:
        w = ctx.runner.which(t)
        w = "(simulado)" if w is True else w
        print("tool  : %-14s %s" % (t, w or "nao encontrado (dnf install infiniband-diags iproute)"))
    if os.geteuid() != 0 and not args.fixtures:
        print("aviso : sem root; smpquery/perfquery/ibnetdiscover exigem acesso a /dev/infiniband/umad*")
    return OK if ok else CRIT


def build_parser():
    p = argparse.ArgumentParser(prog="ibmon", description="Monitor de links InfiniBand (HPC / RHEL 8)")
    p.add_argument("--version", action="version", version="ibmon " + __version__)
    p.add_argument("-c", "--config", help="arquivo INI (padrao: /etc/ibmon/ibmon.conf)")
    p.add_argument("--db", help="caminho do banco SQLite (sobrepoe config)")
    p.add_argument("--fixtures", help="diretorio de simulacao (sys/ e cmd/) para testes sem hardware")
    p.add_argument("--no-color", action="store_true")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("discover", help="inventario local + vizinhos; --fabric para topologia completa")
    s.add_argument("--force", action="store_true", help="refaz inventario local mesmo sem mudancas")
    s.add_argument("--fabric", action="store_true", help="executa ibnetdiscover (caro)")
    s.add_argument("--auto-fabric", action="store_true", help="executa ibnetdiscover so se fabric_ttl expirou")
    s.add_argument("--from-file", help="usa saida salva do ibnetdiscover em vez de executar")
    s.set_defaults(func=cmd_discover)

    s = sub.add_parser("check", help="le contadores, calcula TX/RX e erros, gera eventos (rapido)")
    s.add_argument("--json", action="store_true")
    s.add_argument("-q", "--quiet", action="store_true", help="so imprime se houver alerta (para timers)")
    s.add_argument("-v", "--verbose", action="store_true")
    s.add_argument("-w", "--watch", type=float, metavar="SEG", help="repete a cada SEG segundos")
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("status", help="ultima coleta por porta (taxas, XmitWait, erros)")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("topo", help="representacao logica host -> switch -> switch")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_topo)

    s = sub.add_parser("events", help="eventos (padrao: apenas abertos)")
    s.add_argument("-a", "--all", action="store_true", help="inclui fechados")
    s.add_argument("-n", "--limit", type=int, default=50)
    s.add_argument("--severity", choices=("warning", "critical"))
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_events)

    s = sub.add_parser("export", help="exporta grafo (json/dot) ou metricas (prom)")
    s.add_argument("-f", "--format", choices=("json", "dot", "prom"), default="json")
    s.add_argument("-o", "--output")
    s.set_defaults(func=cmd_export)

    s = sub.add_parser("prune", help="expurga eventos antigos (retencao)")
    s.add_argument("--vacuum", action="store_true", help="compacta o arquivo do banco")
    s.set_defaults(func=cmd_prune)

    s = sub.add_parser("doctor", help="verifica pre-requisitos (sysfs, ferramentas, permissoes)")
    s.set_defaults(func=cmd_doctor)
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_help()
        return 3
    if args.no_color:
        render.USE_COLOR = False
    try:
        ctx = _ctx(args)
    except Exception as e:  # config/banco inacessivel
        print("ibmon: %s" % e, file=sys.stderr)
        return 3
    try:
        return args.func(ctx, args)
    finally:
        ctx.store.close()


if __name__ == "__main__":
    sys.exit(main())
