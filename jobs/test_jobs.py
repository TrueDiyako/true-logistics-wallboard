import datetime as dt, io, json, urllib.error
import common, live, nightly
import otif_extract as ox, response_time_extract as rx, credit_notes_extract as cx

TODAY = dt.date(2026, 10, 2)

def O(oid, num, cust, state, start, locked="0", name="", deadline=None):
    return {"order_id": oid, "number": num, "customer_id": cust, "state": state, "locked": locked,
            "start_date": f"{start} 07:00:00", "deadline_date": f"{deadline or start} 15:00:00",
            "name": name or f"PO-{num}", "delete_dt": None}

# real open orders (2 Oct 2026)
ORDERS = [
    O("1", "1022154", "3023", "Ready to be shipped", "2026-08-25"),          # overdue 38 d
    O("2", "1022190", "3018", "Partially ready for warehouse", "2026-09-04"),
    O("3", "1022319", "1293", "Ready for warehouse", "2026-10-05"),
    O("4", "1022352", "1692", "Pakning", "2026-10-02"),                        # -> Not ready
    O("5", "1022357", "1692", "Pakning", "2026-10-23"),                        # beyond 14 d
    O("6", "1014480", "1155", "-- ingen --", "2025-01-03"),                     # stale
    O("7", "1022752", "1467", "Shipped", "2026-09-30"),                        # shipped not closed
    O("8", "1022885", "1419", "Shipped", "2026-07-15"),                        # shipped > 2 months ago
    O("9", "1022832", "2", "Ready for warehouse", "2026-10-02"),               # internal samples
    O("10", "1022811", "2008", "Not ready for warehouse", "2026-10-12", name="PO-TRC-20260921-NEOW", deadline="2026-10-14"),
    O("11", "1022422", "1308", "Ready for warehouse", "2026-10-07"),
    O("12", "1020354", "1364", "Pakning", "2026-10-03"),                       # intercompany GmbH
]
NAMES = {"3023": "Euro Sun Goods", "3018": "CLG", "1293": "Out of Home AB", "1692": "EUROBRANDS",
         "1155": "Old", "1467": "Premium Brands", "1419": "Healthy Vitafood", "2008": "Humble Group USA",
         "1308": "Real Food Distributors"}
VALUES = {"1022811": 704877.5, "1022422": 450000.0, "1022319": 80000.0, "1022357": 300000.0, "1022352": 1000.0}
GENOBJ = {"10": [{"unit_order_count_f": "0", "unit_order_reserved_f": "11520"}],
          "11": [{"unit_order_count_f": "3000", "unit_order_reserved_f": "1000"}],
          "3": [{"unit_order_count_f": "100", "unit_order_reserved_f": "0"}],
          "5": [], "4": [{"unit_order_count_f": "10", "unit_order_reserved_f": "30"}]}

class FakeTL:
    def __init__(self): self.genobj_calls = 0
    def list_orders(self, f): return [dict(o) for o in ORDERS]
    def module(self, m, oid): self.genobj_calls += 1; return GENOBJ.get(oid, [])

class FakeEcon:
    def _get(self, url):
        if "/customers" in url:
            return {"collection": [{"customerNumber": int(k), "name": v} for k, v in NAMES.items()]}
        if "/orders/drafts" in url:
            return {"collection": [{"orderNumber": int(k), "netAmountInBaseCurrency": v} for k, v in VALUES.items()]}
        return {"collection": []}

class FakeDachser:
    status = "ok"
    def booked(self, n, po="", start=None): return "booked" if n == "1022422" else "not booked"

def test_column_mapping():
    assert live.column_of("Pakning") == live.NOT_READY and live.column_of("-- none --") == live.NOT_READY
    assert live.column_of("Shipped") == "Shipped - not closed" and live.column_of("Weird") is None

def test_status_board_rules():
    open_o = [o for o in ORDERS if o["customer_id"] not in common.INTERNAL_CUSTOMERS]
    b = {c["status"]: [x["order"] for x in c["orders"]] for c in live.build_status_board(open_o, NAMES, TODAY)}
    assert b["Ready to be shipped"] == ["1022154"]                     # overdue kept
    assert b["Partially ready for warehouse"] == ["1022190"]
    assert b[live.NOT_READY] == ["1022352", "1022811"]                 # Pakning folded, stale + far-future out
    assert b["Ready for warehouse"] == ["1022319", "1022422"]
    assert b["Shipped - not closed"] == ["1022752"]                    # July start dropped
    flat = [x for c in live.build_status_board(open_o, NAMES, TODAY) for x in c["orders"]]
    assert [x["overdue"] for x in flat if x["order"] == "1022154"] == [True]
    assert [x["po"] for x in flat if x["order"] == "1022811"] == ["PO-TRC-20260921-NEOW"]

def test_biggest_orders():
    open_o = [o for o in ORDERS if o["customer_id"] not in common.INTERNAL_CUSTOMERS]
    tl = FakeTL()
    rows = live.build_biggest(open_o, NAMES, {k: v for k, v in VALUES.items()}, lambda i: tl.module("genobj", i),
                              FakeDachser(), TODAY)
    assert [r["order"] for r in rows] == ["1022811", "1022422", "1022357", "1022319", "1022352"]
    r = {x["order"]: x for x in rows}
    assert r["1022811"]["pick_rate_pct"] == 0.0 and r["1022422"]["pick_rate_pct"] == 75.0
    assert r["1022422"]["transport"] == "booked" and r["1022811"]["transport"] == "not booked"
    assert r["1022357"]["pick_rate_pct"] is None and r["1022352"]["status"] == live.NOT_READY
    assert r["1022811"]["po"] == "PO-TRC-20260921-NEOW" and r["1022811"]["delivery"] == "2026-10-14"

def test_due_not_ready():
    open_o = [o for o in ORDERS if o["customer_id"] not in common.INTERNAL_CUSTOMERS]
    due = live.build_due_not_ready(open_o, NAMES, TODAY)
    assert [x["order"] for x in due] == ["1022190", "1022352"] and due[0]["overdue"]
    assert due[0]["days_late"] == 28 and due[1]["days_late"] == 0

def test_emails():
    rows = [{"in_kpi": True, "answered": False, "business_hours": 20, "customer": "a@x.dk", "customer_domain": "x.dk",
             "subject": "PO 1", "received_local": "2026-09-30 09:00"},
            {"in_kpi": True, "answered": False, "business_hours": 4, "customer": "b@y.dk", "customer_domain": "y.dk",
             "subject": "PO 2", "received_local": "2026-10-02 08:00"},
            {"in_kpi": True, "answered": True, "business_hours": 50, "customer": "c@z.dk", "customer_domain": "z.dk",
             "subject": "PO 3", "received_local": "2026-09-20 08:00"}]
    rows[0].update(owner="Andreas", owner_customer="Siradis")
    e = live.build_emails(rows)
    assert len(e) == 1 and e[0]["waiting_days"] == 2.5
    assert e[0]["owner"] == "Andreas" and e[0]["company"] == "Siradis"
    rows[0].update(owner="", owner_customer="")
    assert live.build_emails(rows)[0]["owner"] == "Unassigned" and live.build_emails(rows)[0]["company"] == "x.dk"

class _Resp:
    def __init__(self, body, status=200): self.body, self.status = body, status
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def read(self): return json.dumps(self.body).encode()

# real Dachser answers (5 Oct 2026): Hannover stores the PO, Denmark the TraceLink order number
SHIP_BOOKED = {"id": "41491200792", "shipmentDate": "2026-10-05", "references": [{"code": "100", "value": "PC26387"}],
               "status": [{"statusSequence": 1, "statusDate": "2026-10-05", "event": {"code": "0", "description": "No status available"}}]}
SHIP_DELIVERED = {"id": "41491199319", "shipmentDate": "2026-09-29", "status": [
    {"statusSequence": 1, "statusDate": "2026-10-01T12:43:00", "event": {"code": "Z", "description": "Delivered"}},
    {"statusSequence": 2, "statusDate": "2026-09-30", "event": {"code": "E", "description": "Inbound"}}]}
SHIP_TRANSIT = {"id": "41491199974", "shipmentDate": "2026-10-01", "status": [
    {"statusSequence": 1, "statusDate": "2026-10-02T17:56:00", "event": {"code": "A", "description": "Outbound"}}]}
SHIP_DK = {"id": "9", "shipmentDate": "2026-10-05", "references": [{"code": "100", "value": "1022811"}],
           "status": [{"statusSequence": 1, "statusDate": "2026-10-05", "event": {"code": "0"}}]}

def test_dachser_states_keys_and_matching():
    import urllib.parse as up
    calls = []
    def fake(req, timeout=30):
        key = req.headers.get("X-api-key"); ref = up.parse_qs(up.urlparse(req.full_url).query)["tracking-number"][0]
        calls.append((key, ref, "shipmenthistory" in req.full_url))
        if key == "HANNOVER" and ref == "PC26387": return _Resp({"shipments": [SHIP_BOOKED]})
        if key == "HANNOVER" and ref == "IOR8471": return _Resp({"shipments": [SHIP_DELIVERED]})
        if key == "HANNOVER" and ref == "Order 21.2026": return _Resp({"shipments": [SHIP_TRANSIT]})
        if key == "DK" and ref == "1022811": return _Resp({"shipments": [SHIP_DK]})
        if key == "DK" and ref == "2903": return _Resp({"shipments": [dict(SHIP_DK, shipmentDate="2026-06-01")]})
        raise urllib.error.HTTPError(req.full_url, 422, "x", {}, io.BytesIO(b"{}"))
    orig = live.urllib.request.urlopen; live.urllib.request.urlopen = fake; live.time.sleep = lambda s: None
    try:
        dc = live.Dachser(["HANNOVER", "DK"])
        assert dc.booked("1022914", "PC26387", dt.date(2026, 10, 6)) == "booked"
        assert dc.booked("1022784", "IOR8471", dt.date(2026, 9, 29)) == "delivered"
        assert dc.booked("1022885", "Order 21.2026", dt.date(2026, 10, 1)) == "in transit"
        n = len(calls)
        assert dc.booked("1022811", "PO-TRC-20260921-NEOW", dt.date(2026, 10, 15)) == "booked"     # Glostrup: by order number
        assert calls[n:] == [("HANNOVER", "1022811", True), ("DK", "1022811", True)]                  # PO not needed
        assert dc.booked("1022877", "2903", dt.date(2026, 10, 2)) == "not booked"   # generic PO, shipment from June: ignored
        assert dc.status == "ok"
    finally:
        live.urllib.request.urlopen = orig
    def deny(req, timeout=30):
        calls.append(req.full_url); raise urllib.error.HTTPError(req.full_url, 401, "x", {}, io.BytesIO(b""))
    live.urllib.request.urlopen = deny; calls.clear()
    try:
        dc = live.Dachser(["BAD"])
        assert dc.booked("1", "", None) == "unknown" and dc.status == "not subscribed"
        assert "shipmenthistory" in calls[0] and "shipmentstatus" in calls[1] and len(calls) == 2
        assert dc.booked("2", "", None) == "unknown" and len(calls) == 2        # no more calls after both refused
    finally:
        live.urllib.request.urlopen = orig
    assert live.Dachser([]).booked("1") == "unknown" and live.Dachser([]).status == "no key"

def test_booking_warning_and_workdays():
    assert live.workdays_until(dt.date(2026, 10, 2), dt.date(2026, 10, 6)) == 2   # Fri -> Tue = Mon, Tue
    open_o = [o for o in ORDERS if o["customer_id"] not in common.INTERNAL_CUSTOMERS]
    tl = FakeTL()
    live.BOOKING_WARNING = True
    try:
        rows = live.build_biggest(open_o, NAMES, dict(VALUES), lambda i: tl.module("genobj", i), FakeDachser(), TODAY)
    finally:
        live.BOOKING_WARNING = False
    r = {x["order"]: x for x in rows}
    assert r["1022352"]["book_now"] and r["1022319"]["book_now"]            # Fri 2 Oct / Mon 5 Oct, not booked
    assert not r["1022811"]["book_now"] and not r["1022422"]["book_now"]    # 12 Oct is far; 1022422 booked
    s = live.biggest_summary(open_o, dict(VALUES), rows, TODAY)
    assert s["top_value_dkk"] == round(sum(VALUES.values())) and s["top_share_pct"] == 100.0 and s["booked"] == 1

def test_live_main_end_to_end():
    store = {}
    common.redis_set = lambda k, v: store.__setitem__(k, json.loads(json.dumps(v, default=str)))
    common.redis_get = lambda k: store.get(k)
    orig_run = rx.run
    rx.run = lambda g: ([], [])
    try:
        payload, health = live.main(tl=FakeTL(), econ=FakeEcon(), graph=object(), dachser=FakeDachser(), today=TODAY,
                                    plan_source=__import__("test_production").plan_xlsx)
    finally:
        rx.run = orig_run
    assert health["ok"], health
    assert "lk:live" in store and "lk:health:live" in store
    orders = [x["order"] for c in store["lk:live"]["status_board"] for x in c["orders"]]
    assert "1022832" not in orders and "1020354" not in orders           # internal excluded
    assert store["lk:live"]["biggest"][0]["customer"] == "Humble Group USA"

def test_live_failure_keeps_previous_section():
    store = {"lk:live": {"emails_waiting": [{"customer": "old"}]}}
    common.redis_set = lambda k, v: store.__setitem__(k, json.loads(json.dumps(v, default=str)))
    common.redis_get = lambda k: store.get(k)
    def boom(g): raise RuntimeError("Graph 403")
    orig_run = rx.run; rx.run = boom
    try:
        payload, health = live.main(tl=FakeTL(), econ=FakeEcon(), graph=object(), dachser=FakeDachser(), today=TODAY,
                                    plan_source=__import__("test_production").plan_xlsx)
    finally:
        rx.run = orig_run
    assert not health["ok"] and health["errors"][0]["section"] == "emails"
    assert store["lk:live"]["emails_waiting"] == [{"customer": "old"}]
    assert "emails_waiting" in store["lk:live"]["stale_sections"]

def _ord(cust, req, on_time, in_full, ou=10, fu=10, late=0, note=1, name="", num="1"):
    return {"customer": cust, "requested_date": req, "on_time": on_time, "otif": on_time is True and in_full,
            "in_full": in_full, "state": "Shipped", "on_time_basis": "shipped, date kept",
            "ordered_units": ou, "filled_units": fu, "short_value_dkk": 0,
            "delivery_notes": note, "last_dispatch": req if note else None,
            "dispatch_vs_plan_days": late if note else None, "name": name, "order_number": num}

def test_nightly_payload_builders():
    orders = ([_ord("Big", TODAY - dt.timedelta(days=i), True, i % 4 != 0) for i in range(12)]
              + [_ord("Mid", TODAY - dt.timedelta(days=i), i % 2 == 0, True) for i in range(6)]
              + [_ord("Small", TODAY, False, False, 10, 5) for _ in range(3)])
    otif = nightly.otif_payload(orders)
    assert otif["headline"]["orders"] == 21 and otif["headline"]["on_time_pct"] == round(100 * 15 / 21, 1)
    # 100 ordered, 2 short = 2%; 10 ordered, 5 short = 50%; full = 0% -> average 17.3%, 2 of 3 short
    sp = nightly.shorts_payload([_ord("A", TODAY - dt.timedelta(days=9), True, False, 100, 98),
                                 _ord("B", TODAY - dt.timedelta(days=9), True, False, 10, 5),
                                 _ord("C", TODAY - dt.timedelta(days=9), True, True, 50, 50)], TODAY)
    assert sp["avg_short_pct"] == 17.3 and sp["orders_with_short_pct"] == 66.7 and sp["orders"] == 3
    assert sp["weekly"] == [{"week": "2026-W39", "pct": 17.3, "orders": 3}]
    c = nightly.customers_payload(orders)
    assert [x["customer"] for x in c["best"]][0] == "Big" and c["worst"][0]["customer"] == "Small"
    assert c["order_fill_rate_pct"] == round(100 * 195 / 210, 1)

def test_nightly_main_with_fakes():
    store = {}
    common.redis_set = lambda k, v: store.__setitem__(k, json.loads(json.dumps(v, default=str)))
    common.redis_get = lambda k: store.get(k)
    o_run, r_run, c_run = ox.run, rx.run, cx.run
    ox.run = lambda tl, today=None: ([_ord("A", TODAY, True, True, name="4500067621", num="1022593")], [], [])
    rx.run = lambda g: ([{"in_kpi": True, "answered": True, "business_hours": 3, "received_local": "2026-09-21 09:00", "subject": "PO 1",
                          "without_order_copy": False, "replied_by": "order@truecompany.com"},
                         {"in_kpi": True, "answered": False, "business_hours": 30, "received_local": "2026-09-22 09:00",
                          "subject": "Purchase order 4500067621"},
                         {"in_kpi": True, "answered": False, "business_hours": 30, "received_local": "2026-09-23 09:00",
                          "subject": "Request for samples"}], [])
    cx.run = lambda e, today=None: ([], [{"period": "2026-W40", "invoices": 10, "credits": 2, "credit_pct_count": 20.0}],
                                    [{"internal": False, "over_credited": True, "credit_note": 1, "customer": "X",
                                      "date": "2026-09-30", "amount_dkk": -100.0, "reverses_invoice": 5}])
    try:
        payload, health = nightly.main(tl=1, graph=1, econ=1, today=TODAY)
    finally:
        ox.run, rx.run, cx.run = o_run, r_run, c_run
    assert health["ok"], health
    k = store["lk:kpi"]
    assert k["otif"]["headline"]["otif_pct"] == 100.0
    r = k["response"]
    assert r["headline"]["within_1_day_pct"] == 33.3 and r["bands"][-1] == "no reply"
    assert r["no_reply"] == {"count": 2, "order_in_tracelink": 1, "order_in_tracelink_pct": 50.0}
    assert r["trend"][-1] == {"week": "2026-W39", "pct": 33.3, "waits": 3}
    assert r["last4"]["no reply"] == 66.7 and r["last4"]["waits"] == 3
    assert "shorts" in k and "dispatch" not in k
    assert r["outside_copy"] == {"count": 0, "answered": 1, "pct": 0.0, "by_person": []}
    assert k["credit"]["headline"]["credit_pct"] == 20.0 and k["credit"]["to_check"][0]["reason"] == "credited more than invoiced"
    assert k["credit"]["weekly"] == []                                   # W40 is the running week

def test_complete_weeks_and_credit_dedupe():
    rows = [{"week": f"2026-W{w:02d}"} for w in range(20, 41)]
    out = nightly.complete_weeks(rows, TODAY)
    assert out[-1]["week"] == "2026-W39" and len(out) == 13
    # two credit notes in one over-credited chain -> one row; a chain that nets positive -> none
    cn = [{"internal": False, "over_credited": True, "credit_note": n, "customer": "LVK", "date": d, "chain": "Delivery to Stiller",
           "chain_net": -16273.0, "amount_dkk": -142000.0, "reverses_invoice": "19964"} for n, d in ((20565, "2026-09-01"), (20566, "2026-09-02"))]
    cn.append({"internal": False, "over_credited": False, "credit_note": 20550, "customer": "EUROBRANDS", "date": "2026-09-10",
               "chain": "EB51", "chain_net": 36012.19, "amount_dkk": -277000.0, "reverses_invoice": "20131"})
    c = nightly.credit_payload([], cn)
    assert len(c["to_check"]) == 1 and c["to_check"][0]["amount_dkk"] == -284000.0 and c["to_check"][0]["chain"] == "Delivery to Stiller"
    assert c["to_check"][0]["reason"] == "credited more than invoiced" and c["to_check"][0]["date"] == "2026-09-02"

def test_cph_now_is_naive_local():
    n = common.cph_now()
    assert n.tzinfo is None

if __name__ == "__main__":
    import sys, traceback
    fails = 0
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            try: f(); print("PASS", n)
            except Exception as e: fails += 1; print("FAIL", n, repr(e)); traceback.print_exc()
    sys.exit(fails)
