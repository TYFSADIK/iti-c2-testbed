| Scenario | Attempts | Availability | RTO (s) | Longest outage (s) | Lost writes | Served by |
| --- | --- | --- | --- | --- | --- | --- |
| baseline | 231 | 99.57% | 37.4 | 2.1 | 0 | a |
| app pod killed | 121 | 100.0% | 0.0 | 0.0 | 0 | a |
| database primary pod killed | 178 | 100.0% | 0.0 | 0.0 | 0 | a |
| node powered off (k3d-site-a-agent-0) | 272 | 74.26% | 30.1 | 29.0 | 0 | a |
| degraded WAN (80 ms, 2% loss) | 235 | 100.0% | 0.0 | 0.0 | 0 | a |
| witness (site C) lost for 60 s | 223 | 51.12% | 74.0 | 54.5 | 0 | a |
| site A lost (network cut) | 228 | 74.56% | 96.5 | 95.9 | 0 | a -> b |
