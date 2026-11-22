"""Availability prober: behaves like a C2 client and records what it experiences.

Every INTERVAL seconds it resolves DNS_NAME through DNS_SERVER (no caching
beyond the record's TTL), then POSTs an order with the next sequence number.
Each attempt is one CSV row: time, seq, ok, site, http status, latency, error.
Acknowledged sequence numbers are what tools/report.py later checks against
the database to measure data loss (RPO).
"""
from __future__ import annotations

import csv
import json
import os
import random
import socket
import struct
import time
import urllib.error
import urllib.request


def dns_query(name: str, server: str, port: int = 53, timeout: float = 1.0) -> tuple[str, int]:
    """Minimal A-record lookup over UDP. Returns (ip, ttl)."""
    qid = random.randint(0, 65535)
    q = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)
    q += b"".join(bytes([len(p)]) + p.encode() for p in name.rstrip(".").split(".")) + b"\0"
    q += struct.pack(">HH", 1, 1)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        s.sendto(q, (server, port))
        data, _ = s.recvfrom(2048)
    return parse_answer(data, qid)


def parse_answer(data: bytes, qid: int) -> tuple[str, int]:
    rid, flags, qd, an = struct.unpack(">HHHH", data[:8])
    if rid != qid:
        raise OSError("DNS id mismatch")
    if flags & 0x000F:
        raise OSError(f"DNS rcode {flags & 0xF}")
    i = 12
    for _ in range(qd):
        while data[i]:
            i += data[i] + 1
        i += 5
    for _ in range(an):
        if data[i] & 0xC0 == 0xC0:
            i += 2
        else:
            while data[i]:
                i += data[i] + 1
            i += 1
        rtype, _, ttl, rdlen = struct.unpack(">HHIH", data[i:i + 10])
        i += 10
        if rtype == 1 and rdlen == 4:
            return socket.inet_ntoa(data[i:i + 4]), ttl
        i += rdlen
    raise OSError("no A record")


class Resolver:
    def __init__(self, name, server, clock=time.monotonic):
        self.name, self.server, self.clock = name, server, clock
        self.ip, self.expires = None, 0.0

    def get(self):
        if self.ip is None or self.clock() >= self.expires:
            try:
                self.ip, ttl = dns_query(self.name, self.server)
                self.expires = self.clock() + max(ttl, 1)
            except OSError:
                if self.ip is None:
                    raise
        return self.ip


def post(url, obj, timeout):
    req = urllib.request.Request(url, json.dumps(obj).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read())


STATS = {"ok": 0, "fail": 0, "site": "", "last_ok": 0.0, "latency_ms": 0.0}


def serve_metrics(port: int):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            s = STATS
            body = (f"c2_probe_ok_total {s['ok']}\nc2_probe_fail_total {s['fail']}\n"
                    f"c2_probe_last_ok_timestamp {s['last_ok']:.3f}\nc2_probe_latency_ms {s['latency_ms']}\n"
                    + "".join(f'c2_probe_serving_site{{site="{x}"}} {int(x == s["site"])}\n' for x in ("a", "b", "c"))).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    threading.Thread(target=ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever, daemon=True).start()


def main():
    env = os.environ.get
    if env("METRICS_PORT"):
        serve_metrics(int(env("METRICS_PORT")))
    name, server = env("DNS_NAME", "c2.lab"), env("DNS_SERVER", "127.0.0.1")
    port, interval = int(env("APP_PORT", "80")), float(env("INTERVAL", "0.5"))
    client = env("CLIENT", f"prober-{int(time.time())}")
    out = env("OUT", "/data/probe.csv")
    res = Resolver(name, server)
    new = not os.path.exists(out)
    f = open(out, "a", newline="", buffering=1)
    w = csv.writer(f)
    if new:
        w.writerow(["t", "client", "seq", "ok", "site", "status", "latency_ms", "error"])
    seq = 0
    print(f"probing {name} via {server} as {client}", flush=True)
    while True:
        seq += 1
        t0 = time.time()
        site, status, err = "", "", ""
        try:
            ip = res.get()
            status, body = post(f"http://{ip}:{port}/orders", {"client": client, "seq": seq, "body": f"order {seq}"}, 2.0)
            site = body.get("site", "")
        except urllib.error.HTTPError as e:
            status, err = e.code, e.read()[:120].decode(errors="replace")
        except (OSError, ValueError) as e:
            err = str(e)[:120]
        ok = status == 201
        lat = (time.time() - t0) * 1000
        STATS["ok" if ok else "fail"] += 1
        STATS["latency_ms"] = round(lat)
        if ok:
            STATS["last_ok"], STATS["site"] = t0, site
        w.writerow([f"{t0:.3f}", client, seq, int(ok), site, status, f"{(time.time() - t0) * 1000:.0f}", err])
        time.sleep(max(0.0, interval - (time.time() - t0)))


if __name__ == "__main__":
    main()
