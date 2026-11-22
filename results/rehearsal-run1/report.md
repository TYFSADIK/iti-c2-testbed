| Scenario | Attempts | Availability | RTO (s) | Longest outage (s) | Lost writes | Served by |
| --- | --- | --- | --- | --- | --- | --- |
| baseline | 240 | 100.0% | 0.0 | 0.0 | - | a |
| app pod killed | 120 | 100.0% | 0.0 | 0.0 | - | a |
| database primary pod killed | 181 | 100.0% | 0.0 | 0.0 | - | a |
| node powered off (k3d-site-a-agent-0) | 271 | 93.73% | 26.0 | 25.1 | - | a -> b |
| degraded WAN (80 ms, 2% loss) | 160 | 78.75% | 16.9 | 16.5 | - | a |
| site A lost (network cut) | 75 | 0.0% | no recovery | 120.3 | - |  |
