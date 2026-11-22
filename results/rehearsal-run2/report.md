| Scenario | Attempts | Availability | RTO (s) | Longest outage (s) | Lost writes | Served by |
| --- | --- | --- | --- | --- | --- | --- |
| baseline | 240 | 100.0% | 0.0 | 0.0 | - | a |
| app pod killed | 121 | 100.0% | 0.0 | 0.0 | - | a |
| database primary pod killed | 181 | 100.0% | 0.0 | 0.0 | - | a |
| degraded WAN (80 ms, 2% loss) | 133 | 96.99% | 89.3 | 2.8 | - | a |
| witness (site C) lost for 60 s | 227 | 37.0% | 94.1 | 86.7 | - | a |
| site A lost (network cut) | 114 | 0.88% | no recovery | 179.6 | - | a |
