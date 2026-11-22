# 4. Break it and measure

The prober at site C behaves like a C2 client: every 0.5 s it resolves `c2.lab`
through the site C DNS and writes an order with the next sequence number.
Every attempt is logged. After a scenario, `tools/report.py` reads that log and
the database:

| Metric | Meaning |
| --- | --- |
| RTO | from fault injection to the first successful write after the outage |
| Longest outage | longest run of failed writes in the window |
| Availability | successful writes / attempts in the window |
| Lost writes (RPO) | writes the client saw acknowledged that are not in the database afterwards |
| Served by | which site took the writes, in order |

## On the Proxmox build

```bash
./chaos/run.sh all      # on the Proxmox host, about 45 minutes, then results/proxmox/report.md
./chaos/run.sh wan-cut  # or one scenario at a time
```

| Scenario | Fault | Expected |
| --- | --- | --- |
| app-pod | one C2 Board pod killed | no failed writes (second replica) |
| db-primary | database primary pod killed | in-site failover to a replica, seconds |
| node-off | power-off of the node with the database primary | in-site failover after the node is declared dead |
| disk-fail | Longhorn data disk taken offline on one node | Longhorn rebuilds the replica elsewhere, no outage |
| wan-degrade | 80 ms, 2% loss on site A's WAN | slower writes from site C, no failover |
| wan-cut | site A partitioned for 3 minutes | failover to B after `FAIL_AFTER`; A fences itself |
| site-loss | every site A VM powered off | failover to site B |
| restore | database rebuilt from the S3 backup | restore time |

## Choosing FAIL_AFTER

The controller waits `FAIL_AFTER` seconds of continuous failure before moving
the service to another site. It must be:

- longer than `LEASE_TTL + LEASE_POLL` (12 s), so a cut-off site has stopped
  writing before the other is promoted (the controller refuses to start otherwise);
- longer than the slowest **in-site** recovery, or a node failure becomes an
  unnecessary site failover with possible data loss. The first local rehearsal
  showed exactly that with 20 s: a powered-off node triggered a site failover.
  The default is now 90 s.

The cost is site-loss RTO: roughly `FAIL_AFTER` plus promotion time.

## What the rehearsal found (run 1)

| Symptom | Cause | Fix |
| --- | --- | --- |
| A node power-off at site A moved the service to site B | in-site database failover after a node loss took longer than `FAIL_AFTER` = 20 s | `FAIL_AFTER` = 90 s and the rule above |
| After failover, site B's app could not log in to its database | promoting a CloudNativePG replica cluster reset the app password to a generated one | the replica bootstraps with the shared `c2-db-credentials` owner secret |
| The controller then failed back to site A's stale database | the old primary still reported "primary" | failed-away sites are marked stale and never chosen; state saved across restarts |

## What the rehearsal found (run 2)

| Symptom | Cause | Fix |
| --- | --- | --- |
| Site A was cut off and nothing failed over | site B's database had been crash-looping since the in-site failover at site A ("requested timeline 2 is not a child of this server's history"): with asynchronous in-site replication, site B had already received WAL the promoted local replica never had | synchronous in-site replication at site A (`minSyncReplicas: 1`, three instances), and the controller rebuilds a broken, non-stale standby from the active site after 3 minutes (`RESEED_CMD_B`) and reports it at once |

The controller was right to refuse: failing over to a broken standby would have
been worse than staying down.

## What the rehearsal found (run 3)

Run 3 confirmed the run 2 fixes: the standby followed site A through an in-site
database failover, a site failover took 93.5 s (90 s threshold + 3.5 s promotion)
and lost no acknowledged write. It also found one more real problem and two
rehearsal-method problems:

| Symptom | Cause | Fix |
| --- | --- | --- |
| A degraded WAN caused a site failover | the app sent every request, health checks included, through one shared database connection; with synchronous replication under 80 ms delay the health checks queued behind writes and timed out for 90 s | the app uses a small connection pool with a 3 s statement timeout |
| (rehearsal) the delay and loss also hit traffic inside site A | `tc netem` was applied to every packet leaving the k3d nodes | netem now only applies to packets for site B and site C, like `wan-impair` on the Proxmox routers |
| (rehearsal) the node power-off test was skipped | the database primary was on the k3d server node, which the test never stops | the server node is tainted for control-plane use only, and each site is reached through k3d's proxy in front of all nodes |

Raw data for every run is in `results/rehearsal-run1/` onwards.

## Run 4 (all fixes in)

| Failure injected | Availability | RTO (s) | Longest outage (s) | Lost writes | Served by |
| --- | --- | --- | --- | --- | --- |
| baseline | 99.57% | - | 2.1 | 0 | a |
| app pod killed | 100% | 0 | 0 | 0 | a |
| database primary pod killed | 100% | 0 | 0 | 0 | a |
| node powered off (k3d-site-a-agent-0) | 74.26% | 30.1 | 29 | 0 | a |
| degraded WAN (80 ms, 2% loss) | 100% | 0 | 0 | 0 | a |
| witness (site C) lost for 60 s | 51.12% | 74 | 54.5 | 0 | a |
| site A lost (network cut) | 74.56% | 96.5 | 95.9 | 0 | a -> b |
| restore from S3 backup | - | 46 | - | - | 1210 orders restored |

No acknowledged write was lost in any scenario. Site failover: 94.2 s.
