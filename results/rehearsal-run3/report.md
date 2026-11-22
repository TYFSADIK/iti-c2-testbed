| Scenario | Attempts | Availability | RTO (s) | Longest outage (s) | Lost writes | Served by |
| --- | --- | --- | --- | --- | --- | --- |
| baseline | 240 | 100.0% | 0.0 | 0.0 | 0 | a |
| app pod killed | 121 | 100.0% | 0.0 | 0.0 | 0 | a |
| database primary pod killed | 180 | 100.0% | 0.0 | 0.0 | 0 | a |
| degraded WAN (80 ms, 2% loss) | 86 | 43.02% | 107.1 | 99.4 | 0 | a -> b |
| witness (site C) lost for 60 s | 225 | 52.0% | 73.7 | 54.1 | 0 | b |
| site A lost (network cut) | 364 | 98.9% | 37.2 | 2.0 | 0 | b |
