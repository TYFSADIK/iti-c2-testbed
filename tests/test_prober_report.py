import socket
import struct

from conftest import load

pr = load("services/prober/prober.py", "prober")
rp = load("tools/report.py", "report")


def answer(qid, ip, ttl, rcode=0):
    q = b"\x02c2\x03lab\x00" + struct.pack(">HH", 1, 1)
    hdr = struct.pack(">HHHHHH", qid, 0x8180 | rcode, 1, 1, 0, 0)
    rr = b"\xc0\x0c" + struct.pack(">HHIH", 1, 1, ttl, 4) + socket.inet_aton(ip)
    return hdr + q + rr


def test_parse_dns_answer():
    assert pr.parse_answer(answer(7, "172.18.0.5", 5), 7) == ("172.18.0.5", 5)


def test_dns_errors():
    import pytest
    with pytest.raises(OSError):
        pr.parse_answer(answer(7, "1.2.3.4", 5), 8)
    with pytest.raises(OSError):
        pr.parse_answer(answer(7, "1.2.3.4", 5, rcode=3), 7)


def test_dns_against_local_udp_server():
    import threading
    srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    srv.bind(("127.0.0.1", 0))

    def serve():
        data, addr = srv.recvfrom(512)
        srv.sendto(answer(struct.unpack(">H", data[:2])[0], "10.9.8.7", 3), addr)

    threading.Thread(target=serve, daemon=True).start()
    assert pr.dns_query("c2.lab", "127.0.0.1", srv.getsockname()[1]) == ("10.9.8.7", 3)


def rows(pattern, t0=100.0, dt=0.5, site="a", fail_site="b"):
    out = []
    for i, ch in enumerate(pattern):
        ok = ch != "x"
        out.append(rp.Row(t0 + i * dt, "c", i + 1, ok, (site if i < pattern.find("x") or "x" not in pattern else fail_site) if ok else ""))
    return out


def test_report_outage_rto_availability():
    r = rows("....xxxxxxxxxx......")          # fault at t=101.5 (index 3), fail 102..106.5, back at 107
    res = rp.analyse(r, 101.5, 110)
    assert res["rto_s"] == 5.5 and res["longest_outage_s"] == 5.0 and res["sites"] == "a -> b"
    assert res["availability_pct"] == round(100 * 7 / 17, 2)


def test_report_no_outage_and_lost_writes():
    r = rows("..........")
    stored = {"c": {1, 2, 3, 5, 6, 7, 8, 9, 10}}
    res = rp.analyse(r, 100, 110, stored)
    assert res["rto_s"] == 0.0 and res["lost_writes"] == 1 and res["lost_window_s"] == 0.0


def test_report_never_recovered():
    assert rp.analyse(rows("..xxxx"), 100, 110)["rto_s"] is None
