from conftest import load

app = load("services/c2board/app.py", "c2board")


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def test_lease_without_witness_always_held():
    assert app.Lease("", "a", 10).held()


def test_lease_expires_when_witness_unreachable():
    c = Clock()
    lease = app.Lease("http://w", "a", 10, clock=c)
    assert not lease.held()                     # not granted yet
    lease.update({"active": "a"})
    c.t = 9
    lease.update(None)                          # witness unreachable
    assert lease.held()
    c.t = 10.5
    assert not lease.held() and lease.reason == "witness unreachable"


def test_lease_revoked_when_other_site_active():
    c = Clock()
    lease = app.Lease("http://w", "a", 10, clock=c)
    lease.update({"active": "a"})
    lease.update({"active": "b"})
    assert not lease.held() and "b is active" in lease.reason


class FakeStore:
    def __init__(self, role):
        self.r = role

    def role(self):
        return self.r


def test_status_needs_primary_and_lease():
    held = app.Lease("", "a", 10)
    assert app.status(FakeStore("primary"), held)[0] == 200
    assert app.status(FakeStore("standby"), held)[0] == 503
    c = Clock()
    fenced = app.Lease("http://w", "a", 10, clock=c)
    code, body = app.status(FakeStore("primary"), fenced)
    assert code == 503 and body["writable"] is False
