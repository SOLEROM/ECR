"""Status parsers + GATE A-D logic — golden fixtures."""

from core import status as S


# --- links -------------------------------------------------------------------
LINKS_JSON = (
    '{"own_id": 1, "peers": {"2": {"last_seen_unix": 1718000000.1, "age_ms": 120}, '
    '"3": {"last_seen_unix": 1718000000.2, "age_ms": 880}}}'
)
LINKS_LOG = (
    '2026-06-18T12:00:00Z rx from=2 bytes=210 msg="{...}"\n'
    '2026-06-18T12:00:00Z rx from=3 bytes=210 msg="{...}"\n'
    '2026-06-18T12:00:00Z rx from=2 bytes=210 msg="{...}"\n'
)


def test_parse_links_json():
    p = S.parse_links(LINKS_JSON, own_id=1)
    assert p["source"] == "links.json"
    assert p["count"] == 2
    assert p["peers"][2] == 120 and p["peers"][3] == 880


def test_parse_links_log_fallback():
    p = S.parse_links(LINKS_LOG, own_id=1)
    assert p["source"] == "log"
    assert set(p["peers"].keys()) == {2, 3}
    assert p["count"] == 2


def test_parse_links_excludes_own():
    txt = '{"own_id": 2, "peers": {"2": {"age_ms": 5}, "3": {"age_ms": 5}}}'
    assert S.parse_links(txt, own_id=2)["count"] == 1


def test_parse_links_empty():
    assert S.parse_links("", own_id=1)["count"] == 0


# --- check -------------------------------------------------------------------
def test_parse_check_present():
    g = S.parse_check("noise\n[CHECK] value=3 age=0.2 unit=ok\n", "[CHECK]")
    assert g["present"] and g["value"] == 3 and g["age"] == 0.2


def test_parse_check_second_tag():
    g = S.parse_check("[CHECK2] value=3 age=0.4", "[CHECK2]")
    assert g["present"] and g["value"] == 3


def test_parse_check_absent():
    assert S.parse_check("nothing here", "[CHECK]")["present"] is False


# --- serviceC stats ----------------------------------------------------------
SERVICEC = ("+12s up=20 (20/s) down=40 (40/s) drop: bad_lan=0 loop=40 bad_air=0 "
            "self=0 err: tx=0 lan=0 signal=-72dB")


def test_parse_servicec_stats():
    b = S.parse_servicec_stats(SERVICEC)
    assert b["present"] and b["up"] == 20 and b["down"] == 40
    assert b["loop"] == 40 and b["self"] == 0 and b["err_tx"] == 0
    assert b["signal"] == -72


def test_parse_servicec_absent():
    assert S.parse_servicec_stats("garbage")["present"] is False


# --- probes ------------------------------------------------------------------
def test_parse_probe_a():
    assert S.parse_probe_a("PROBEA: INIT\nPROBEA: READY") is True
    assert S.parse_probe_a("PROBEA: INIT") is False


def test_parse_probe_b():
    assert S.parse_probe_b("...PROBEB_OK...") is True
    assert S.parse_probe_b("PROBEB_PENDING") is False


# --- gates: variant A --------------------------------------------------------
def _raw_a(serviceA=True, serviceB=True, links=LINKS_JSON, reachable=True):
    return {
        "reachable_roleA": reachable,
        "serviceA": {"up": serviceA}, "serviceB": {"up": serviceB},
        "links_text": links, "check1_text": "",
    }


def test_gates_variant_a_all_green():
    ns = S.build_status("d1", "A", _raw_a(), expected_links=2, own_id=1)
    assert ns.gates["A"]["state"] == S.OK
    assert ns.gates["B"]["state"] == S.OK
    assert ns.gates["C"]["state"] == S.NA          # no check in variant A
    assert ns.gates["D"]["state"] == S.OK
    assert S.overall_gate(ns.gates) == S.OK


def test_gates_variant_a_processes_down():
    ns = S.build_status("d1", "A", _raw_a(serviceA=False, serviceB=False, links=""),
                        expected_links=2, own_id=1)
    assert ns.gates["B"]["state"] == S.FAIL
    assert ns.gates["D"]["state"] == S.FAIL        # no peers
    assert S.overall_gate(ns.gates) == S.FAIL


def test_gates_variant_a_unreachable():
    ns = S.build_status("d1", "A", _raw_a(reachable=False), expected_links=2, own_id=1)
    assert ns.gates["A"]["state"] == S.FAIL


def test_gate_d_single_node_is_na():
    # one-node fleet: no peers expected → link gate not applicable, not WARN
    raw = {"reachable_roleA": True, "serviceA": {"up": True}, "serviceB": {"up": True},
           "links_text": "", "check1_text": ""}
    ns = S.build_status("solo", "A", raw, expected_links=0, own_id=1)
    assert ns.gates["D"]["state"] == S.NA


def test_gates_variant_a_partial_links_warns():
    # only 1 of expected 2 peers present → warn (not fail, since some seen)
    one = '{"own_id":1,"peers":{"2":{"age_ms":100}}}'
    ns = S.build_status("d1", "A", _raw_a(links=one), expected_links=2, own_id=1)
    assert ns.gates["D"]["state"] == S.WARN


# --- gates: variant B --------------------------------------------------------
def _raw_b(**over):
    raw = {
        "reachable_roleA": True, "reachable_roleB": True,
        "probe_a_text": "PROBEA: READY", "probe_b_text": "PROBEB_OK",
        "serviceA": {"up": True}, "serviceB": {"up": True}, "serviceC": {"up": True},
        "links_text": LINKS_JSON,
        "check1_text": "[CHECK] value=3 age=0.2\n[CHECK2] value=3 age=0.3",
        "servicec_text": SERVICEC,
    }
    raw.update(over)
    return raw


def test_gates_variant_b_all_green():
    ns = S.build_status("d1", "B", _raw_b(), expected_links=2, own_id=1)
    assert ns.gates["A"]["state"] == S.OK
    assert ns.gates["B"]["state"] == S.OK
    assert ns.gates["C"]["state"] == S.OK          # both checks fresh
    assert ns.gates["D"]["state"] == S.OK


def test_gates_variant_b_probe_a_not_ready():
    ns = S.build_status("d1", "B", _raw_b(probe_a_text="PROBEA: INIT"),
                        expected_links=2, own_id=1)
    assert ns.gates["A"]["state"] == S.FAIL


def test_gates_variant_b_servicec_missing_demotes_link():
    ns = S.build_status("d1", "B", _raw_b(servicec_text=""), expected_links=2, own_id=1)
    assert ns.gates["D"]["state"] == S.WARN


def test_gates_variant_b_no_check_fails_c():
    ns = S.build_status("d1", "B", _raw_b(check1_text=""), expected_links=2, own_id=1)
    assert ns.gates["C"]["state"] == S.FAIL


def test_gates_variant_b_servicec_down_fails_b():
    ns = S.build_status("d1", "B", _raw_b(serviceC={"up": False}),
                        expected_links=2, own_id=1)
    assert ns.gates["B"]["state"] == S.FAIL
