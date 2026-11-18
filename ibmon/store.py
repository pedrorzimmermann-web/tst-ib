"""Persistencia minima em SQLite.

Guarda somente:
  nodes          HCAs e switches (GUID, tipo, nome, hostname, LID, posicao)
  ports          portas de HCAs (GUID da porta, LID, estado, taxa, interfaces IPoIB, IPs)
  links          conexoes origem/destino (host<->switch e switch<->switch)
  counter_state  ULTIMO snapshot de contadores por porta (sobrescrito; sem historico)
  events         alertas deduplicados (um registro aberto por porta+tipo; expurgo automatico)
  meta           hashes/timestamps usados para atualizacao incremental

Todas as gravacoes comparam antes de escrever: linhas sem mudanca nao sao tocadas.
"""

import json
import os
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS nodes (
    guid TEXT PRIMARY KEY, node_type TEXT, name TEXT, hostname TEXT,
    description TEXT, num_ports INTEGER, lid INTEGER, sys_guid TEXT,
    location TEXT, is_local INTEGER DEFAULT 0, updated_at INTEGER);
CREATE TABLE IF NOT EXISTS ports (
    node_guid TEXT, port_num INTEGER, port_guid TEXT, ca_name TEXT, pci_addr TEXT,
    lid INTEGER, state TEXT, phys_state TEXT, rate TEXT, link_layer TEXT,
    netdevs TEXT, ip_addrs TEXT, updated_at INTEGER,
    PRIMARY KEY (node_guid, port_num));
CREATE TABLE IF NOT EXISTS links (
    a_guid TEXT, a_port INTEGER, b_guid TEXT, b_port INTEGER, speed TEXT,
    source TEXT, updated_at INTEGER,
    PRIMARY KEY (a_guid, a_port, b_guid, b_port));
CREATE INDEX IF NOT EXISTS links_b ON links (b_guid, b_port);
CREATE TABLE IF NOT EXISTS counter_state (scope TEXT PRIMARY KEY, ts REAL, data TEXT);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT, kind TEXT, severity INTEGER,
    value REAL, message TEXT, first_seen INTEGER, last_seen INTEGER,
    count INTEGER DEFAULT 1, open INTEGER DEFAULT 1, closed_at INTEGER);
CREATE INDEX IF NOT EXISTS events_open ON events (open, scope, kind);
"""

SCHEMA_VERSION = "1"


def port_scope(guid, port):
    return "port:%s:%s" % (guid, port)


def canon(a, b):
    return (a, b) if a <= b else (b, a)


class Store(object):
    def __init__(self, path):
        if path != ":memory:":
            d = os.path.dirname(os.path.abspath(path))
            if not os.path.isdir(d):
                os.makedirs(d, mode=0o750)
        self.path = path
        self.db = sqlite3.connect(path, timeout=30)
        self.db.row_factory = sqlite3.Row
        if path != ":memory:":
            self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)
        if self.meta_get("schema_version") is None:
            self.meta_set("schema_version", SCHEMA_VERSION)
        self.changes = 0

    def commit(self):
        self.db.commit()

    def close(self):
        self.db.commit()
        self.db.close()

    # ---- meta ---------------------------------------------------------------
    def meta_get(self, key, default=None):
        r = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r[0] if r else default

    def meta_set(self, key, value):
        value = str(value)
        if self.meta_get(key) != value:
            self.db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, value))

    # ---- upsert com deteccao de mudanca -------------------------------------
    def upsert(self, table, keys, row):
        """Insere/atualiza apenas se algum campo mudou. Retorna 'insert'|'update'|None."""
        where = " AND ".join("%s=?" % k for k in keys)
        cur = self.db.execute("SELECT * FROM %s WHERE %s" % (table, where), [row[k] for k in keys]).fetchone()
        now = int(time.time())
        if cur is None:
            row = dict(row, updated_at=now)
            cols = list(row)
            self.db.execute("INSERT INTO %s (%s) VALUES (%s)" % (table, ",".join(cols), ",".join("?" * len(cols))),
                            [row[c] for c in cols])
            self.changes += 1
            return "insert"
        diff = {k: v for k, v in row.items() if k not in keys and v is not None and cur[k] != v}
        if not diff:
            return None
        diff["updated_at"] = now
        self.db.execute("UPDATE %s SET %s WHERE %s" % (table, ",".join("%s=?" % k for k in diff), where),
                        list(diff.values()) + [row[k] for k in keys])
        self.changes += 1
        return "update"

    def upsert_node(self, **row):
        return self.upsert("nodes", ["guid"], row)

    def upsert_port(self, **row):
        return self.upsert("ports", ["node_guid", "port_num"], row)

    # ---- links --------------------------------------------------------------
    def set_link(self, ep1, ep2, speed=None, source="local"):
        """Registra o cabo ep1<->ep2 ((guid,port)). Remove ligacoes antigas conflitantes
        (cabo movido). Retorna (mudou, [links_removidos])."""
        a, b = canon(tuple(ep1), tuple(ep2))
        removed = []
        for ep in (a, b):
            for r in self.links_of(ep[0], ep[1]):
                if (r["a_guid"], r["a_port"], r["b_guid"], r["b_port"]) != (a[0], a[1], b[0], b[1]):
                    removed.append(dict(r))
                    self._delete_link(r)
        res = self.upsert("links", ["a_guid", "a_port", "b_guid", "b_port"],
                          {"a_guid": a[0], "a_port": a[1], "b_guid": b[0], "b_port": b[1],
                           "speed": speed, "source": source})
        return (res is not None or bool(removed)), removed

    def _delete_link(self, r):
        self.db.execute("DELETE FROM links WHERE a_guid=? AND a_port=? AND b_guid=? AND b_port=?",
                        (r["a_guid"], r["a_port"], r["b_guid"], r["b_port"]))
        self.changes += 1

    def links_of(self, guid, port=None):
        if port is None:
            return self.db.execute("SELECT * FROM links WHERE a_guid=? OR b_guid=?", (guid, guid)).fetchall()
        return self.db.execute("SELECT * FROM links WHERE (a_guid=? AND a_port=?) OR (b_guid=? AND b_port=?)",
                               (guid, port, guid, port)).fetchall()

    def peer_of(self, guid, port):
        for r in self.links_of(guid, port):
            if r["a_guid"] == guid and r["a_port"] == port:
                return r["b_guid"], r["b_port"], r["speed"]
            return r["a_guid"], r["a_port"], r["speed"]
        return None

    def prune_links(self, keep):
        """Remove links que nao estao em 'keep' (conjunto de tuplas canonicas). Retorna removidos."""
        removed = []
        for r in self.db.execute("SELECT * FROM links").fetchall():
            if (r["a_guid"], r["a_port"], r["b_guid"], r["b_port"]) not in keep:
                removed.append(dict(r))
                self._delete_link(r)
        return removed

    # ---- contadores (somente ultimo snapshot) -------------------------------
    def counter_get(self, scope):
        r = self.db.execute("SELECT ts, data FROM counter_state WHERE scope=?", (scope,)).fetchone()
        return (r["ts"], json.loads(r["data"])) if r else (None, None)

    def counter_put(self, scope, ts, data):
        self.db.execute("INSERT OR REPLACE INTO counter_state(scope, ts, data) VALUES (?,?,?)",
                        (scope, ts, json.dumps(data, separators=(",", ":"), sort_keys=True)))

    def counter_all(self):
        return {r["scope"]: (r["ts"], json.loads(r["data"]))
                for r in self.db.execute("SELECT * FROM counter_state")}

    # ---- eventos (deduplicados) ---------------------------------------------
    def sync_events(self, alerts, scopes, now=None):
        """Abre/atualiza eventos para 'alerts' e fecha os eventos abertos dos 'scopes'
        avaliados que nao estao mais em alerta. Retorna lista de eventos NOVOS."""
        now = int(now or time.time())
        new = []
        active = set()
        for a in alerts:
            key = (a["scope"], a["kind"])
            active.add(key)
            r = self.db.execute("SELECT id, severity FROM events WHERE open=1 AND scope=? AND kind=?", key).fetchone()
            if r:
                self.db.execute("UPDATE events SET last_seen=?, count=count+1, value=?, message=?, severity=? WHERE id=?",
                                (now, a.get("value"), a["message"], a["severity"], r["id"]))
                if a["severity"] > r["severity"]:
                    new.append(a)  # escalonamento conta como novidade
            else:
                self.db.execute("INSERT INTO events(scope,kind,severity,value,message,first_seen,last_seen) "
                                "VALUES (?,?,?,?,?,?,?)",
                                (a["scope"], a["kind"], a["severity"], a.get("value"), a["message"], now, now))
                new.append(a)
        scopes = set(scopes)
        for r in self.db.execute("SELECT id, scope, kind FROM events WHERE open=1").fetchall():
            if r["scope"] in scopes and (r["scope"], r["kind"]) not in active:
                self.db.execute("UPDATE events SET open=0, closed_at=? WHERE id=?", (now, r["id"]))
        return new

    def events(self, open_only=False, limit=50, min_severity=0):
        q = "SELECT * FROM events WHERE severity>=?" + (" AND open=1" if open_only else "")
        q += " ORDER BY open DESC, severity DESC, last_seen DESC LIMIT ?"
        return self.db.execute(q, (min_severity, limit)).fetchall()

    def prune(self, retention_days, max_rows):
        cutoff = int(time.time()) - int(retention_days) * 86400
        n = self.db.execute("DELETE FROM events WHERE open=0 AND last_seen<?", (cutoff,)).rowcount
        total = self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        if total > max_rows:
            n += self.db.execute(
                "DELETE FROM events WHERE id IN (SELECT id FROM events WHERE open=0 "
                "ORDER BY last_seen ASC LIMIT ?)", (total - max_rows,)).rowcount
        return n

    # ---- consultas ----------------------------------------------------------
    def nodes(self):
        return {r["guid"]: dict(r) for r in self.db.execute("SELECT * FROM nodes")}

    def ports(self, local_only=False):
        q = "SELECT p.* FROM ports p"
        if local_only:
            q += " JOIN nodes n ON n.guid=p.node_guid WHERE n.is_local=1"
        return [dict(r) for r in self.db.execute(q + " ORDER BY node_guid, port_num")]

    def links(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM links ORDER BY a_guid, a_port")]

    def vacuum(self):
        self.db.commit()
        self.db.execute("VACUUM")
