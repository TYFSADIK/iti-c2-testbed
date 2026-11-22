#!/usr/bin/env python3
"""Turn a prober CSV and the chaos markers into recovery and availability numbers.

  python3 tools/report.py --probe results/run/probe.csv --markers results/run/markers.jsonl \
      --seqs results/run/seqs.json --out results/run/report.md

markers.jsonl: one {"scenario": "...", "fault": <unix time>, "end": <unix time>, "note": "..."} per line,
written by the chaos runners. seqs.json: {"client": [...stored seqs...]} read back from the
database after the run (local/rehearse.sh does it).

Per scenario:
  RTO           fault injection to the first successful write after the outage (0 if no outage)
  outage        longest run of failed writes inside the scenario window
  availability  successful writes / attempts inside the window
  lost writes   writes the client saw acknowledged (201) that are not in the database afterwards (RPO)
"""
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass


@dataclass
class Row:
    t: float
    client: str
    seq: int
    ok: bool
    site: str


def load_rows(path):
    with open(path) as f:
        return [Row(float(r["t"]), r["client"], int(r["seq"]), r["ok"] == "1", r["site"]) for r in csv.DictReader(f)]


def analyse(rows: list[Row], fault: float, end: float, stored: dict[str, set] | None = None) -> dict:
    win = [r for r in rows if fault <= r.t <= end]
    if not win:
        return {"attempts": 0}
    ok = [r for r in win if r.ok]
    first_fail = next((r for r in win if not r.ok), None)
    rto = 0.0
    if first_fail:
        back = next((r for r in win if r.ok and r.t > first_fail.t), None)
        rto = (back.t - fault) if back else None
    longest, cur_start = 0.0, None
    for r in win:
        if not r.ok and cur_start is None:
            cur_start = r.t
        elif r.ok and cur_start is not None:
            longest, cur_start = max(longest, r.t - cur_start), None
    if cur_start is not None:
        longest = max(longest, win[-1].t - cur_start)
    sites = []
    for r in ok:
        if not sites or sites[-1] != r.site:
            sites.append(r.site)
    out = {"attempts": len(win), "ok": len(ok), "availability_pct": round(100 * len(ok) / len(win), 2),
           "rto_s": None if rto is None else round(rto, 1), "longest_outage_s": round(longest, 1),
           "sites": " -> ".join(sites)}
    if stored is not None:
        # acknowledged before or during the window, checked against the final database
        acked = [r for r in rows if r.ok and r.t <= end]
        lost = [r for r in acked if r.seq not in stored.get(r.client, set())]
        out["lost_writes"] = len(lost)
        out["lost_window_s"] = round(max(r.t for r in lost) - min(r.t for r in lost), 1) if lost else 0.0
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--probe", required=True)
    ap.add_argument("--markers", required=True)
    ap.add_argument("--seqs")
    ap.add_argument("--out")
    a = ap.parse_args()
    rows = load_rows(a.probe)
    stored = None
    if a.seqs:
        stored = {k: set(v) for k, v in json.load(open(a.seqs)).items()}
    marks = [json.loads(l) for l in open(a.markers) if l.strip()]
    lines = ["| Scenario | Attempts | Availability | RTO (s) | Longest outage (s) | Lost writes | Served by |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    results = []
    for m in marks:
        r = analyse(rows, m["fault"], m["end"], stored if m.get("check_loss", True) else None)
        r["scenario"] = m["scenario"]
        results.append(r)
        rto = "no recovery" if r.get("rto_s") is None else r["rto_s"]
        lines.append(f"| {m['scenario']} | {r.get('attempts', 0)} | {r.get('availability_pct', '-')}% | {rto} | "
                     f"{r.get('longest_outage_s', '-')} | {r.get('lost_writes', '-')} | {r.get('sites', '')} |")
    text = "\n".join(lines) + "\n"
    print(text)
    if a.out:
        open(a.out, "w").write(text)
        open(a.out.rsplit(".", 1)[0] + ".json", "w").write(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    main()
