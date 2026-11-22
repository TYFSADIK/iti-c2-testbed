from conftest import load

fc = load("services/failover/controller.py", "controller")
P = fc.Probe
UP, STANDBY, DOWN = P(200, "primary", True), P(503, "standby", False), P(None)


def run(ctl, seq):
    out = []
    for t, probes in seq:
        out += [(t, a) for a in ctl.step(t, probes)]
    return out


def test_no_action_while_healthy():
    c = fc.Controller(["a", "b"], "a", 20)
    assert run(c, [(t, {"a": UP, "b": STANDBY}) for t in range(30)]) == []


def test_failover_after_threshold_and_completion_time():
    c = fc.Controller(["a", "b"], "a", 20)
    acts = run(c, [(t, {"a": DOWN, "b": STANDBY}) for t in range(0, 21)])
    kinds = [a[1][0] for a in acts]
    assert kinds.count("promote") == 1 and ("lease", "b") in [a[1] for a in acts] and ("dns", "b") in [a[1] for a in acts]
    assert [t for t, a in acts if a[0] == "promote"] == [20]
    assert c.active == "b" and c.promoting == "b"
    run(c, [(25, {"a": DOWN, "b": P(200, "primary", True)})])
    assert c.promoting is None and c.last_failover_s == 25 and c.failovers == 1


def test_short_blip_does_not_fail_over():
    c = fc.Controller(["a", "b"], "a", 20)
    acts = run(c, [(t, {"a": DOWN, "b": STANDBY}) for t in range(10)] + [(10, {"a": UP, "b": STANDBY})])
    assert not any(a[0] == "promote" for _, a in acts) and c.active == "a" and c.fail_since is None


def test_no_failover_without_a_reachable_standby():
    c = fc.Controller(["a", "b"], "a", 5)
    acts = run(c, [(t, {"a": DOWN, "b": DOWN}) for t in range(10)])
    assert c.active == "a" and any(a[0] == "alert" for _, a in acts)


def test_fenced_old_primary_is_not_a_candidate_target_and_no_failback():
    c = fc.Controller(["a", "b"], "a", 5)
    run(c, [(t, {"a": DOWN, "b": STANDBY}) for t in range(6)] + [(7, {"a": DOWN, "b": UP})])
    acts = run(c, [(t, {"a": UP, "b": UP}) for t in range(8, 40)])
    assert c.active == "b" and acts == []          # site a coming back does not move the service


def test_lease_rule():
    assert fc.lease_safe(20, 10, 2) and not fc.lease_safe(12, 10, 2)


def test_writes_hosts_file(tmp_path):
    f = tmp_path / "hosts"
    fc.write_hosts(str(f), "c2.lab", "10.0.0.5")
    assert f.read_text() == "10.0.0.5 c2.lab\n"


def test_never_fails_back_to_a_stale_site():
    """Rehearsal run 1: after a->b, b failed and the controller moved back to a's stale database."""
    c = fc.Controller(["a", "b"], "a", 5)
    run(c, [(t, {"a": DOWN, "b": STANDBY}) for t in range(6)] + [(7, {"a": DOWN, "b": UP})])
    assert c.active == "b" and c.stale == {"a"}
    old_primary = P(503, "primary", False)          # site a back, its old database still says primary
    acts = run(c, [(t, {"a": old_primary, "b": DOWN}) for t in range(10, 30)])
    assert c.active == "b" and any(a[0] == "alert" for _, a in acts)


def test_state_survives_restart(tmp_path):
    c = fc.Controller(["a", "b"], "b", 90, stale={"a"})
    f = str(tmp_path / "state.json")
    fc.save_state(f, c)
    assert fc.load_state(f) == {"active": "b", "stale": ["a"]}
    assert fc.load_state(str(tmp_path / "missing.json")) == {}


BROKEN = P(503, "down", False)


def test_broken_standby_is_rebuilt_while_active_is_healthy():
    c = fc.Controller(["a", "b"], "a", 90, reseed_after=180)
    acts = run(c, [(t, {"a": UP, "b": BROKEN}) for t in range(0, 200)])
    reseeds = [t for t, a in acts if a[0] == "reseed"]
    assert reseeds == [180]                               # once, after 180 s
    acts = run(c, [(t, {"a": UP, "b": STANDBY}) for t in range(200, 205)])
    assert any("following a again" in a[1] for _, a in acts if a[0] == "event")


def test_no_rebuild_when_active_is_unhealthy_or_standby_is_stale():
    c = fc.Controller(["a", "b"], "a", 1000, reseed_after=10)
    acts = run(c, [(t, {"a": DOWN, "b": BROKEN}) for t in range(0, 60)])
    assert not any(a[0] == "reseed" for _, a in acts)     # never wipe the only copy that might be newest
    c2 = fc.Controller(["a", "b"], "a", 90, stale={"b"}, reseed_after=10)
    assert not any(a[0] == "reseed" for _, a in run(c2, [(t, {"a": UP, "b": BROKEN}) for t in range(60)]))
