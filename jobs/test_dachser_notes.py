"""Dashboard 2 'Picked by Dachser, not in TraceLink' - real data of 14 Oct 2026, all customers."""
import datetime as dt, io, json, sys, urllib.error, urllib.parse as up
import dachser_notes as dn

def O(oid, num, name, cust, start, deadline, state="Pakning", created="2026-10-01"):
    return {"order_id": oid, "number": num, "name": name, "customer_id": cust, "state": state, "locked": "0",
            "create_date": created, "start_date": start + " 07:00:00", "deadline_date": deadline + " 15:00:00", "delete_dt": None}

ORDERS = [
    O("1333921", "1023038", "dm-drogerie markt GmbH + Co. KG | 1003725502", "1364", "2026-10-13", "2026-10-15"),
    O("1332135", "1023019", "EDEKA Graf | 1075849563", "1364", "2026-10-09", "2026-10-13"),
    O("1329176", "1022959", "Bela Handels GmbH & Co. KG | 01673087 - Sour Cola", "1364", "2026-10-14", "2026-10-16"),
    O("1328232", "1022945", "REWE Markt GmbH - Lager | 2070020023", "1364", "2026-10-07", "2026-10-09"),
    O("1340001", "1023050", "IOR8530", "1484", "2026-10-13", "2026-10-15"),               # True ApS customer (Camps Food) via Hannover
    O("1340002", "1023051", "EB66 - Truck 56", "1692", "2026-10-15", "2026-10-19"),        # EUROBRANDS: Dachser holds "EB66"
    O("1340003", "1023052", "4500078000", "1408", "2026-10-20", "2026-10-21"),             # Glostrup order: no Dachser note
    O("1340004", "1023053", "Intern flytning", "2", "2026-10-14", "2026-10-14"),          # internal: skipped
    O("9", "1022900", "Dirk Rossmann GmbH | 112700000", "1364", "2026-10-01", "2026-10-02", "Shipped"),
    O("1202297", "1020354", "", "1364", "2026-03-18", "2026-03-18")]
LINES = {"1333921": [{"unit_order_reserved_f": "912.000000"}], "1332135": [{"unit_order_reserved_f": "2"}] * 3,
         "1329176": [{"unit_order_reserved_f": "162"}], "1328232": [{"unit_order_reserved_f": "300"}],
         "1340001": [{"unit_order_reserved_f": "189"}], "1340002": [{"unit_order_reserved_f": "1890"}],
         "1340003": [{"unit_order_reserved_f": "40"}], "1340004": [{"unit_order_reserved_f": "5"}],
         "9": [{"unit_order_reserved_f": "0"}], "1202297": [{"unit_order_reserved_f": "3"}]}
NOTES = {"1003725502": [("414-1987087", "2026-10-14T07:09:00Z")], "1075849563": [("414-1986311", "2026-10-12T11:00:00Z")],
         "01673087": [("414-1982948", "2026-10-01T05:10:00Z"), ("414-1983696", "2026-10-05T07:33:00Z")],
         "IOR8530": [("414-1987200", "2026-10-13T14:00:00Z")], "EB66": [("414-1987300", "2026-10-14T08:00:00Z")]}
STAT = {"1003725502": [("414-1987087", "2026-10-15", "WV", "2026-10-14T10:12")],
        "1075849563": [("414-1986311", "2026-10-14", "WA", "2026-10-12T13:00")],
        "01673087": [("414-1982948", "2026-10-02", "WV", "2026-10-01T13:35"), ("414-1983696", "2026-10-06", "WV", "2026-10-05T20:43")],
        "IOR8530": [("414-1987200", "2026-10-15", "WV", "2026-10-13T16:00")], "EB66": [("414-1987300", "2026-10-19", "WA", "2026-10-14T09:00")]}
NAMES = {"1484": "Camps Food B.V.", "1692": "EUROBRANDS", "1408": "Salling Group A/S"}

class TL:
    def module(self, m, oid): return LINES[oid]

class R:
    status = 200
    def __init__(s, b): s.b = b
    def __enter__(s): return s
    def __exit__(s, *a): pass
    def read(s): return json.dumps(s.b).encode()

CALLS = []
def fake(req, timeout=60):
    ref = up.parse_qs(up.urlparse(req.full_url).query)["reference-number1"][0]; p = up.urlparse(req.full_url).path
    CALLS.append((p.rsplit("/", 1)[-1], ref, req.headers["X-api-key"]))
    if p.endswith("deliverynotes") and ref in NOTES:
        return R({"deliveryOrders": [{"id": i, "documents": [{"timestamp": t}]} for i, t in NOTES[ref]]})
    if p.endswith("deliveryorderstatus") and ref in STAT:
        return R({"deliveryOrders": [{"id": i, "deliveryDate": d, "status": [{"statusSequence": 1, "statusDate": s, "event": {"code": k}}]}
                                     for i, d, k, s in STAT[ref]]})
    if ref == "EB66 - Truck 56":
        raise urllib.error.HTTPError(req.full_url, 400, "x", {}, io.BytesIO(b"{}"))            # odd PO text
    raise urllib.error.HTTPError(req.full_url, 422, "x", {}, io.BytesIO(b'{"message":"No Documents were found for the criteria."}'))

def run(notes_key="NOTES", status_key="STATUS"):
    CALLS.clear()
    dn.DACHSER_NOTES_KEY, dn.DACHSER_STATUS_KEY = notes_key, status_key
    orig, sleep = dn.urllib.request.urlopen, dn.time.sleep
    dn.urllib.request.urlopen, dn.time.sleep = fake, (lambda s: None)
    try:
        return dn.build(TL(), [dict(o) for o in ORDERS], NAMES, dt.datetime(2026, 10, 14, 9, 30, tzinfo=dt.timezone.utc),
                        today=dt.date(2026, 10, 14))
    finally:
        dn.urllib.request.urlopen, dn.time.sleep = orig, sleep

def test_all_customers_flagged_oldest_first():
    out = run()
    assert [o["order"] for o in out["orders"]] == ["1023019", "1023050", "1023038", "1023051"]
    by = {o["order"]: o for o in out["orders"]}
    assert by["1023050"]["customer"] == "Camps Food B.V." and by["1023050"]["po"] == "IOR8530"     # ApS order, name = PO
    assert by["1023051"]["po"] == "EB66" and by["1023051"]["customer"] == "EUROBRANDS"            # suffix stripped
    assert by["1023019"]["customer"] == "EDEKA Graf" and by["1023019"]["note_days"] == 2           # GmbH: name before "|"
    assert by["1023038"]["dachser_status"] == "WV loading"

def test_not_flagged_and_skipped():
    out = run()
    flagged = {o["order"] for o in out["orders"]}
    assert not flagged & {"1022959", "1022945", "1023052", "1023053", "1022900", "1020354"}
    refs = {c[1] for c in CALLS}
    assert "Intern flytning" not in refs and "112700000" not in refs                            # internal / fully picked: no call

def test_status_only_called_for_hits():
    run()
    status_refs = [c[1] for c in CALLS if c[0] == "deliveryorderstatus"]
    assert sorted(status_refs) == sorted(["1075849563", "01673087", "IOR8530", "1003725502", "EB66"])
    assert {(c[0], c[2]) for c in CALLS} == {("deliverynotes", "NOTES"), ("deliveryorderstatus", "STATUS")}
    assert run()["dachser_calls"] == len(CALLS)

def test_without_status_key_shared_po_is_check_by_hand():
    out = run(status_key="")
    by = {o["order"]: o for o in out["orders"]}
    assert by["1022959"]["check_by_hand"] and by["1023038"]["dachser_status"] == ""
    assert not any(c[0] == "deliveryorderstatus" for c in CALLS)

def test_orders_starting_after_14_days_are_not_checked():
    ORDERS.append(O("1340009", "1023099", "EB70 - Truck 60", "1692", "2026-11-05", "2026-11-09"))
    LINES["1340009"] = [{"unit_order_reserved_f": "1890"}]
    try:
        run()
        assert not any(c[1].startswith("EB70") for c in CALLS)                  # 5 Nov > 14 Oct + 14 days
        rows, _ = dn.check(TL(), [ORDERS[-1]], NAMES, dt.date(2026, 10, 14))
        assert rows[0]["flag"] == "starts after 2026-10-28 - not checked yet"
        dn.DACHSER_NOTES_KEY = "NOTES"; dn.urllib.request.urlopen = fake; dn.time.sleep = lambda s: None
        rows, _ = dn.check(TL(), [ORDERS[-1]], NAMES, dt.date(2026, 10, 25))   # 11 days ahead: checked
        assert rows[0]["flag"] == "no Dachser delivery note"
    finally:
        ORDERS.pop(); LINES.pop("1340009")

def _with_failures(fail):
    """fake() but timing out for refs in `fail` ({ref: number of timeouts, or -1 = always})."""
    left = dict(fail)
    def f(req, timeout=60):
        ref = up.parse_qs(up.urlparse(req.full_url).query)["reference-number1"][0]
        if ref in left and left[ref] != 0:
            left[ref] -= 1
            raise TimeoutError("The read operation timed out")        # real error of 21 Oct 2026
        return fake(req, timeout)
    return f

def _run_with(opener):
    CALLS.clear(); dn.DACHSER_NOTES_KEY, dn.DACHSER_STATUS_KEY = "NOTES", "STATUS"
    orig, sleep = dn.urllib.request.urlopen, dn.time.sleep
    dn.urllib.request.urlopen, dn.time.sleep = opener, (lambda s: None)
    try:
        return dn.build(TL(), [dict(o) for o in ORDERS], NAMES, dt.datetime(2026, 10, 14, 9, 30, tzinfo=dt.timezone.utc),
                        today=dt.date(2026, 10, 14))
    finally:
        dn.urllib.request.urlopen, dn.time.sleep = orig, sleep

def test_timeout_is_retried():
    out = _run_with(_with_failures({"1003725502": 2}))           # two timeouts, third try answers
    assert "1023038" in {o["order"] for o in out["orders"]} and out["no_answer"] == 0

def test_one_order_without_answer_is_skipped_not_fatal():
    out = _run_with(_with_failures({"IOR8530": -1}))             # never answers
    assert out["no_answer"] == 1 and "1023050" not in {o["order"] for o in out["orders"]}
    assert {"1023019", "1023038", "1023051"} <= {o["order"] for o in out["orders"]}   # the rest still checked

def test_dachser_down_fails_the_section():
    refs = ["1003725502", "1075849563", "01673087 - Sour Cola", "01673087", "2070020023", "IOR8530",
            "EB66 - Truck 56", "EB66", "4500078000"]
    try:
        _run_with(_with_failures({r: -1 for r in refs})); assert False
    except RuntimeError as e:
        assert "did not answer" in str(e)

def test_server_error_is_retried():
    left = {"n": 1}
    def f(req, timeout=60):
        if "1075849563" in req.full_url and left["n"]:
            left["n"] -= 1
            raise urllib.error.HTTPError(req.full_url, 503, "x", {}, io.BytesIO(b""))
        return fake(req, timeout)
    out = _run_with(f)
    assert "1023019" in {o["order"] for o in out["orders"]}

def test_not_connected_and_wrong_key():
    assert run(notes_key="") == {"connected": False}
    def deny(req, timeout=60): raise urllib.error.HTTPError(req.full_url, 401, "x", {}, io.BytesIO(b""))
    dn.DACHSER_NOTES_KEY = "BAD"; orig = dn.urllib.request.urlopen; dn.urllib.request.urlopen = deny; dn.time.sleep = lambda s: None
    try:
        dn.build(TL(), [dict(ORDERS[0])], NAMES); assert False
    except RuntimeError as e:
        assert "401" in str(e)
    finally:
        dn.urllib.request.urlopen = orig

if __name__ == "__main__":
    import traceback
    fails = 0
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            try: f(); print("PASS", n)
            except Exception as e: fails += 1; print("FAIL", n, repr(e)); traceback.print_exc()
    sys.exit(fails)
