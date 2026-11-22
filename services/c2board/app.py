"""C2 Board: a small command-and-control message service used as the workload.

Orders are written to PostgreSQL (CloudNativePG). The same image runs at every
site; only the active site's database accepts writes.

Fencing: with WITNESS_URL set, the service asks the witness (the failover
controller at site C) for a write lease every LEASE_POLL seconds. If it cannot
renew the lease for LEASE_TTL seconds, or the witness says another site is
active, it refuses writes. A site cut off from the witness therefore stops
taking orders before the other site is promoted, which prevents split-brain.

  GET  /healthz            200 when this site can take writes, 503 otherwise (JSON says why)
  POST /orders             {"client": "...", "seq": 1, "body": "..."} -> 201
  GET  /orders/seqs?client=c   every stored seq for a client (used to measure data loss)
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

SITE = os.environ.get("SITE", "unknown")
DSN = os.environ.get("DATABASE_URL", "")
WITNESS_URL = os.environ.get("WITNESS_URL", "")
LEASE_TTL = float(os.environ.get("LEASE_TTL", "10"))
LEASE_POLL = float(os.environ.get("LEASE_POLL", "2"))

SCHEMA = """CREATE TABLE IF NOT EXISTS orders (
  id bigserial PRIMARY KEY, client text NOT NULL, seq bigint NOT NULL, body text,
  site text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), UNIQUE (client, seq))"""


class Lease:
    """Write lease from the witness. Without a witness the lease is always held."""

    def __init__(self, url: str, site: str, ttl: float, clock=time.monotonic):
        self.url, self.site, self.ttl, self.clock = url, site, ttl, clock
        self.expires = clock() + ttl if not url else 0.0
        self.reason = "no witness configured" if not url else "lease not yet granted"

    def held(self) -> bool:
        return not self.url or self.clock() < self.expires

    def update(self, answer: dict | None) -> None:
        if answer is None:
            self.reason = "witness unreachable"
            return
        if answer.get("active") == self.site:
            self.expires = self.clock() + self.ttl
            self.reason = "lease held"
        else:
            self.expires = 0.0
            self.reason = f"witness says {answer.get('active')} is active"

    def poll_forever(self):
        while True:
            try:
                with urllib.request.urlopen(f"{self.url}/lease?site={self.site}", timeout=2) as r:
                    self.update(json.loads(r.read()))
            except (OSError, ValueError):
                self.update(None)
            time.sleep(LEASE_POLL)


class Store:
    """Small connection pool. Rehearsal run 3: one shared connection made health checks
    queue behind slow writes on a degraded link, so the witness saw a dead site."""

    def __init__(self, dsn: str, size: int = 4):
        import queue
        self.dsn, self.size = dsn, size
        self.pool = queue.LifoQueue(maxsize=size)

    def _get(self):
        import psycopg
        try:
            return self.pool.get_nowait()
        except Exception:
            c = psycopg.connect(self.dsn, autocommit=True, connect_timeout=3,
                                options="-c statement_timeout=3000")
            if not c.execute("SELECT pg_is_in_recovery()").fetchone()[0]:
                c.execute(SCHEMA)
            return c

    def _put(self, c, ok: bool):
        if ok and not c.closed:
            try:
                self.pool.put_nowait(c)
                return
            except Exception:
                pass
        try:
            c.close()
        except Exception:
            pass

    def _run(self, fn):
        c = self._get()
        try:
            out = fn(c)
        except Exception:
            self._put(c, False)
            raise
        self._put(c, True)
        return out

    def role(self) -> str:
        try:
            rec = self._run(lambda c: c.execute("SELECT pg_is_in_recovery()").fetchone()[0])
            return "standby" if rec else "primary"
        except Exception:
            return "down"

    def insert(self, client, seq, body):
        self._run(lambda c: c.execute(
            "INSERT INTO orders (client, seq, body, site) VALUES (%s,%s,%s,%s) ON CONFLICT (client, seq) DO NOTHING",
            (client, seq, body, SITE)))

    def seqs(self, client):
        return self._run(lambda c: [r[0] for r in c.execute(
            "SELECT seq FROM orders WHERE client = %s ORDER BY seq", (client,)).fetchall()])


def status(store, lease) -> tuple[int, dict]:
    role = store.role()
    ok = role == "primary" and lease.held()
    return (200 if ok else 503), {"site": SITE, "db": role, "lease": lease.reason, "writable": ok}


def make_handler(store, lease):
    class H(BaseHTTPRequestHandler):
        def reply(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/healthz":
                self.reply(*status(store, lease))
            elif u.path == "/orders/seqs":
                client = parse_qs(u.query).get("client", [""])[0]
                try:
                    self.reply(200, {"site": SITE, "client": client, "seqs": store.seqs(client)})
                except Exception as e:
                    self.reply(503, {"site": SITE, "error": str(e)[:200]})
            else:
                self.reply(404, {"error": "not found"})

        def do_POST(self):
            if urlparse(self.path).path != "/orders":
                self.reply(404, {"error": "not found"})
                return
            if not lease.held():
                self.reply(503, {"site": SITE, "error": f"fenced: {lease.reason}"})
                return
            try:
                n = min(int(self.headers.get("Content-Length", 0)), 65536)
                o = json.loads(self.rfile.read(n))
                store.insert(str(o["client"])[:64], int(o["seq"]), str(o.get("body", ""))[:1000])
            except (KeyError, ValueError, TypeError) as e:
                self.reply(400, {"error": str(e)})
                return
            except Exception as e:
                self.reply(503, {"site": SITE, "error": str(e)[:200]})
                return
            self.reply(201, {"site": SITE, "seq": o["seq"]})

        def log_message(self, *a):
            pass
    return H


def main():
    store, lease = Store(DSN), Lease(WITNESS_URL, SITE, LEASE_TTL)
    if WITNESS_URL:
        threading.Thread(target=lease.poll_forever, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), make_handler(store, lease)).serve_forever()


if __name__ == "__main__":
    main()
