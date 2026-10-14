"""Dashboard 2 'Picked by Dachser - pick in TraceLink' - real data of 14 Oct 2026."""
import datetime as dt, io, json, sys, urllib.error, urllib.parse as up
import dachser_notes as dn

ORDERS = [  # open True Company GmbH orders (TraceLink, 14 Oct)
    {"order_id": "1333921", "number": "1023038", "name": "dm-drogerie markt GmbH + Co. KG | 1003725502", "state": "Pakning",
     "create_date": "2026-10-13 13:13", "start_date": "2026-10-13 07:00:00", "deadline_date": "2026-10-15 15:00:00", "delete_dt": None},
    {"order_id": "1332135", "number": "1023019", "name": "EDEKA Graf | 1075849563", "state": "Pakning",
     "create_date": "2026-10-09 14:12", "start_date": "2026-10-09 07:00:00", "deadline_date": "2026-10-13 15:00:00", "delete_dt": None},
    {"order_id": "1329176", "number": "1022959", "name": "Bela Handels GmbH & Co. KG | 01673087 - Sour Cola", "state": "Pakning",
     "create_date": "2026-10-06 11:03", "start_date": "2026-10-14 07:00:00", "deadline_date": "2026-10-16 13:00:00", "delete_dt": None},
    {"order_id": "1328232", "number": "1022945", "name": "REWE Markt GmbH - Lager | 2070020023", "state": "Pakning",
     "create_date": "2026-10-05 11:27", "start_date": "2026-10-07 07:00:00", "deadline_date": "2026-10-09 13:00:00", "delete_dt": None},
    {"order_id": "9", "number": "1022900", "name": "Dirk Rossmann GmbH | 112700000", "state": "Shipped",
     "create_date": "2026-10-01", "start_date": "2026-10-01", "deadline_date": "2026-10-02", "delete_dt": None},
    {"order_id": "1202297", "number": "1020354", "name": "P1653", "state": "Pakning", "create_date": "2026-03-18",
     "start_date": "2026-03-18", "deadline_date": "2026-03-18", "delete_dt": None}]
LINES = {"1333921": [{"unit_order_reserved_f": "912.000000", "unit_order_count_f": "0"}],
         "1332135": [{"unit_order_reserved_f": "2"}, {"unit_order_reserved_f": "2"}, {"unit_order_reserved_f": "2"}],
         "1329176": [{"unit_order_reserved_f": "162"}], "1328232": [{"unit_order_reserved_f": "300"}],
         "9": [{"unit_order_reserved_f": "0", "unit_order_count_f": "50"}], "1202297": []}
NOTES = {"1003725502": [("414-1987087", "2026-10-14T07:09:00Z")], "1075849563": [("414-1986311", "2026-10-12T11:00:00Z")],
         "01673087": [("414-1982948", "2026-10-01T05:10:00Z"), ("414-1983696", "2026-10-05T07:33:00Z")]}
STAT = {"1003725502": [("414-1987087", "2026-10-15", "WV", "2026-10-14T10:12")],
        "1075849563": [("414-1986311", "2026-10-14", "WA", "2026-10-12T13:00")],
        "2070020023": [("414-1984429", "2026-10-23", "WN", "2026-10-05T11:25")],
        "01673087": [("414-1982948", "2026-10-02", "WV", "2026-10-01T13:35"), ("414-1983696", "2026-10-06", "WV", "2026-10-05T20:43")]}

class TL:
    def list_orders(self, f): assert f["customer_id"] == "=1364"; return [dict(o) for o in ORDERS]
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
    raise urllib.error.HTTPError(req.full_url, 422, "x", {}, io.BytesIO(b'{"message":"No Documents were found for the criteria."}'))

def run(notes_key="NOTES", status_key="STATUS"):
    dn.DACHSER_NOTES_KEY, dn.DACHSER_STATUS_KEY = notes_key, status_key
    orig, sleep = dn.urllib.request.urlopen, dn.time.sleep
    dn.urllib.request.urlopen, dn.time.sleep = fake, (lambda s: None)
    try:
        return dn.build(TL(), dt.date(2026, 10, 14), dt.datetime(2026, 10, 14, 9, 30, tzinfo=dt.timezone.utc))
    finally:
        dn.urllib.request.urlopen, dn.time.sleep = orig, sleep

def test_flags_the_real_hits_oldest_first():
    out = run()
    assert out["connected"] and out["count"] == 2
    assert [o["order"] for o in out["orders"]] == ["1023019", "1023038"]          # EDEKA Graf (12 Oct) before dm (14 Oct)
    g, d = out["orders"]
    assert g["customer"] == "EDEKA Graf" and g["po"] == "1075849563" and g["reserved_cases"] == 6 and g["note_days"] == 2
    assert d["reserved_cases"] == 912 and d["note_days"] == 0 and d["dachser_status"] == "WV loading"

def test_bela_shared_po_and_unpicked_orders_not_flagged():
    out = run()
    flagged = {o["order"] for o in out["orders"]}
    assert "1022959" not in flagged and "1022945" not in flagged and "1022900" not in flagged
    assert not any(c[1] == "112700000" for c in CALLS)                          # fully picked: Dachser not asked

def test_keys_used_per_api_and_not_connected_without_key():
    CALLS.clear(); run()
    assert {(c[0], c[2]) for c in CALLS} == {("deliverynotes", "NOTES"), ("deliveryorderstatus", "STATUS")}
    assert run(notes_key="") == {"connected": False}

def test_wrong_notes_key_is_a_clear_error():
    def deny(req, timeout=60): raise urllib.error.HTTPError(req.full_url, 401, "x", {}, io.BytesIO(b""))
    dn.DACHSER_NOTES_KEY, dn.DACHSER_STATUS_KEY = "BAD", ""
    orig = dn.urllib.request.urlopen; dn.urllib.request.urlopen = deny; dn.time.sleep = lambda s: None
    try:
        dn.build(TL(), dt.date(2026, 10, 14)); assert False
    except RuntimeError as e:
        assert "401" in str(e) and "DACHSER_NOTES_KEY" in str(e)
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
