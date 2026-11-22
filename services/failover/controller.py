"""Failover controller and witness, running at site C (management).

Every CHECK_INTERVAL seconds it checks /healthz of the C2 Board at every site.
If the active site has failed for FAIL_AFTER seconds and a standby site answers,
it makes the standby active: the write lease moves to it, global DNS (c2.lab)
points at it, and its database is promoted (PROMOTE_CMD_<SITE>). The site it
failed away from is marked stale and is never a target again: its data is older
than the new primary's (rehearsal run 1 failed back to such a site and would have
lost writes). Rebuilding it as a standby is a manual step (docs/05-failback.md);
restart the controller with STALE= cleared afterwards.

FAIL_AFTER must also be longer than the slowest in-site recovery (a node loss),
or a node failure becomes an unnecessary site failover (rehearsal run 1, 20 s).

Split-brain safety: an application only writes while it holds a lease from this
witness (LEASE_TTL). FAIL_AFTER must be longer than LEASE_TTL + LEASE_POLL, so a
site that is cut off has already stopped writing when the other one is promoted.

  GET /lease?site=a   {"active": "a"}      apps renew their write lease here
  GET /state          controller state as JSON
  GET /metrics        Prometheus metrics
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


@dataclass
class Probe:
    code: int | None          # HTTP status, None if no answer at all
    db: str = ""              # primary / standby / down
    writable: bool = False


@dataclass
class Controller:
    sites: list[str]
    active: str
    fail_after: float = 20.0
    fail_since: float | None = None
    promoting: str | None = None
    failovers: int = 0
    last_failover_s: float = 0.0
    failover_started: float | None = None
    stale: set = field(default_factory=set)   # sites failed away from: never a target until rebuilt
    reseed_after: float = 180.0
    standby_down_since: dict = field(default_factory=dict)
    last_reseed: dict = field(default_factory=dict)
    events: list = field(default_factory=list)

    def standby_care(self, now: float, probes: dict[str, Probe]) -> list[tuple]:
        """A standby whose database is broken while the active site is healthy holds no data
        the active site lacks, so it is safe to rebuild it from the active site (rehearsal run 2:
        an in-site failover at site A left site B unable to follow the new timeline)."""
        actions = []
        active_ok = probes.get(self.active, Probe(None)).writable
        for s in self.sites:
            if s == self.active or s in self.stale:
                continue
            p = probes.get(s, Probe(None))
            if p.code is not None and p.db == "down" and active_ok:
                since = self.standby_down_since.setdefault(s, now)
                if since == now:
                    actions.append(("event", f"standby {s} database is down: site failover not possible"))
                if now - since >= self.reseed_after and now - self.last_reseed.get(s, -1e9) >= 3600:
                    self.last_reseed[s] = now
                    actions += [("reseed", s), ("event", f"rebuilding standby {s} from {self.active} "
                                                          f"after {now - since:.0f} s down")]
            elif s in self.standby_down_since and p.db in ("standby", "primary"):
                del self.standby_down_since[s]
                actions.append(("event", f"standby {s} is following {self.active} again"))
        return actions

    def step(self, now: float, probes: dict[str, Probe]) -> list[tuple]:
        """Decide what to do from one round of health checks. Returns actions."""
        actions = self.standby_care(now, probes)
        act = probes.get(self.active, Probe(None))
        if self.promoting:
            if act.writable:
                self.promoting = None
                self.last_failover_s = now - self.failover_started
                actions.append(("event", f"{self.active} is serving writes, failover took {self.last_failover_s:.1f} s"))
            return actions
        if act.writable:
            if self.fail_since is not None:
                actions.append(("event", f"{self.active} recovered before failover"))
            self.fail_since = None
            return actions
        if self.fail_since is None:
            self.fail_since = now
            actions.append(("event", f"{self.active} unhealthy (code={act.code}, db={act.db or '-'})"))
            return actions
        if now - self.fail_since < self.fail_after:
            return actions
        candidates = [s for s in self.sites if s != self.active and s not in self.stale
                      and probes.get(s, Probe(None)).code is not None and probes[s].db in ("standby", "primary")]
        if not candidates:
            actions.append(("alert", f"{self.active} down for {now - self.fail_since:.0f} s and no standby is reachable"))
            return actions
        new, old = candidates[0], self.active
        self.stale.add(old)
        self.active, self.promoting, self.failovers = new, new, self.failovers + 1
        self.failover_started, self.fail_since = self.fail_since, None
        actions += [("lease", new), ("dns", new), ("promote", new),
                    ("event", f"failover {old} -> {new} after {now - self.failover_started:.0f} s of failure")]
        return actions


def lease_safe(fail_after: float, lease_ttl: float, lease_poll: float, margin: float = 3.0) -> bool:
    return fail_after >= lease_ttl + lease_poll + margin


def check(url: str, timeout: float = 2.0) -> Probe:
    try:
        with urllib.request.urlopen(url + "/healthz", timeout=timeout) as r:
            j = json.loads(r.read())
            return Probe(r.status, j.get("db", ""), bool(j.get("writable")))
    except urllib.error.HTTPError as e:
        try:
            j = json.loads(e.read())
        except ValueError:
            j = {}
        return Probe(e.code, j.get("db", ""), False)
    except (OSError, ValueError):
        return Probe(None)


def load_state(path: str) -> dict:
    """Active and stale sites survive a controller restart (rehearsal: a restart must not
    hand the lease back to a stale site)."""
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(path: str, ctl: "Controller") -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"active": ctl.active, "stale": sorted(ctl.stale)}, f)
    os.replace(tmp, path)


def write_hosts(path: str, name: str, ip: str):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(f"{ip} {name}\n")
    os.replace(tmp, path)


def main():
    env = os.environ.get
    sites = {}
    for item in env("SITES", "a=http://127.0.0.1:8081,b=http://127.0.0.1:8082").split(","):
        name, url = item.split("=", 1)
        sites[name.strip()] = url.strip()
    ips = dict(kv.split("=") for kv in env("SITE_IPS", "").split(",") if kv)
    fail_after = float(env("FAIL_AFTER", "90"))
    ttl, poll = float(env("LEASE_TTL", "10")), float(env("LEASE_POLL", "2"))
    if not lease_safe(fail_after, ttl, poll):
        raise SystemExit(f"FAIL_AFTER={fail_after} must be at least LEASE_TTL + LEASE_POLL + 3 = {ttl + poll + 3}")
    state_file = env("STATE_FILE", "/data/state.json")
    saved = load_state(state_file)
    ctl = Controller(list(sites), saved.get("active") or env("ACTIVE", list(sites)[0]), fail_after,
                     stale=set(saved.get("stale", [])) | {x for x in env("STALE", "").split(",") if x},
                     reseed_after=float(env("RESEED_AFTER", "180")))
    hosts, record = env("DNS_HOSTS_FILE", ""), env("DNS_NAME", "c2.lab")
    log = open(env("EVENT_LOG", "/data/events.jsonl"), "a", buffering=1)
    lock = threading.Lock()
    last = {}

    def emit(kind, msg):
        log.write(json.dumps({"t": time.time(), "kind": kind, "msg": msg}) + "\n")
        print(kind, msg, flush=True)

    if hosts and ctl.active in ips:
        write_hosts(hosts, record, ips[ctl.active])
    save_state(state_file, ctl)
    emit("event", f"controller started, active site {ctl.active}, stale {sorted(ctl.stale) or 'none'}")

    def promote(site):
        cmd = env(f"PROMOTE_CMD_{site.upper()}", "")
        for attempt in range(20):
            if not cmd:
                emit("alert", f"no PROMOTE_CMD_{site.upper()} set; promote {site} by hand")
                return
            r = subprocess.run(shlex.split(cmd), capture_output=True, text=True, timeout=30)
            emit("event", f"promote {site}: exit {r.returncode} {(r.stdout or r.stderr).strip()[:200]}")
            if r.returncode == 0:
                return
            time.sleep(3)

    def reseed(site):
        cmd = env(f"RESEED_CMD_{site.upper()}", "")
        if not cmd:
            emit("alert", f"no RESEED_CMD_{site.upper()} set; rebuild standby {site} by hand")
            return
        r = subprocess.run(shlex.split(cmd), capture_output=True, text=True, timeout=600)
        emit("event", f"reseed {site}: exit {r.returncode} {(r.stdout or r.stderr).strip()[-200:]}")

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            u = urlparse(self.path)
            with lock:
                if u.path == "/lease":
                    body = {"active": ctl.active, "site": parse_qs(u.query).get("site", [""])[0]}
                elif u.path == "/state":
                    body = {"active": ctl.active, "promoting": ctl.promoting, "failovers": ctl.failovers,
                            "last_failover_s": ctl.last_failover_s,
                            "probes": {k: vars(v) for k, v in last.items()}}
                elif u.path == "/metrics":
                    lines = [f'c2_active_site{{site="{s}"}} {int(s == ctl.active)}' for s in sites]
                    lines += [f'c2_site_writable{{site="{s}"}} {int(p.writable)}' for s, p in last.items()]
                    lines += [f"c2_failovers_total {ctl.failovers}", f"c2_last_failover_seconds {ctl.last_failover_s}"]
                    data = ("\n".join(lines) + "\n").encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; version=0.0.4")
                    self.end_headers()
                    self.wfile.write(data)
                    return
                else:
                    self.send_error(404)
                    return
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    threading.Thread(target=ThreadingHTTPServer(("0.0.0.0", int(env("PORT", "8090"))), H).serve_forever,
                     daemon=True).start()
    interval = float(env("CHECK_INTERVAL", "1"))
    while True:
        probes = {s: check(u) for s, u in sites.items()}
        with lock:
            last.clear()
            last.update(probes)
            actions = ctl.step(time.time(), probes)
        if any(k == "lease" for k, _ in actions):
            save_state(state_file, ctl)
        for kind, arg in actions:
            if kind == "dns" and hosts and arg in ips:
                write_hosts(hosts, record, ips[arg])
                emit("event", f"DNS {record} -> {ips[arg]} ({arg})")
            elif kind == "promote":
                threading.Thread(target=promote, args=(arg,), daemon=True).start()
            elif kind == "reseed":
                threading.Thread(target=reseed, args=(arg,), daemon=True).start()
            elif kind in ("event", "alert"):
                emit(kind, arg)
        time.sleep(interval)


if __name__ == "__main__":
    main()
