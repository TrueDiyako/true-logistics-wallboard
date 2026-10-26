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
    assert r["1022422"]["transport"] == "ex works" and r["1022811"]["transport"] == "not booked"   # Real Food: ex works
    assert r["1022357"]["pick_rate_pct"] is None and r["1022352"]["status"] == live.NOT_READY
    assert r["1022811"]["po"] == "PO-TRC-20260921-NEOW" and r["1022811"]["delivery"] == "2026-10-14"

def test_due_not_ready():
    open_o = [o for o in ORDERS if o["customer_id"] not in common.INTERNAL_CUSTOMERS]
    due = live.build_due_not_ready(open_o, NAMES, TODAY)
    assert [x["order"] for x in due] == ["1022190", "1022352"] and due[0]["overdue"]
    assert due[0]["days_late"] == 28 and due[1]["days_late"] == 0

def test_emails():
    rows = [{"in_kpi": True, "answered": False, "business_hours": 20, "customer": "a@x.dk", "customer_domain": "x.dk",
             "subject": "PO 1", "received_local": "2026-10-12 09:00"},
            {"in_kpi": True, "answered": False, "business_hours": 90, "customer": "old@x.dk", "customer_domain": "x.dk",
             "subject": "before the clean slate", "received_local": "2026-10-09 09:00"},
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

def test_four_accounts_customer_id_and_timeouts():
    import urllib.parse as up
    calls = []
    def fake(req, timeout=30):
        q = up.parse_qs(up.urlparse(req.full_url).query)
        calls.append((req.headers.get("X-api-key"), q["tracking-number"][0], q.get("customer-id", [""])[0]))
        if q["tracking-number"][0] == "1022811" and q.get("customer-id") == ["47133021"]:
            return _Resp({"shipments": [SHIP_DK]})
        if q["tracking-number"][0] == "SLOW":
            raise TimeoutError("The read operation timed out")
        raise urllib.error.HTTPError(req.full_url, 422, "x", {}, io.BytesIO(b"{}"))
    orig = live.urllib.request.urlopen; live.urllib.request.urlopen = fake; live.time.sleep = lambda s: None
    try:
        dc = live.Dachser([("47379200", "KH"), ("72695099", "K1"), ("47133021", "K2"), ("47222882", "K3")])
        assert dc.booked("1022811", "", dt.date(2026, 10, 15)) == "booked"
        assert dc.found_by["1022811"] == "47133021"
        assert [c[2] for c in calls] == ["47379200", "72695099", "47133021"]          # stops at the account that has it
        assert dc.booked("1", "SLOW", None) == "unknown" and dc.no_answer == 1          # timeout: unknown, no crash
        assert dc.booked("1022811", "", dt.date(2026, 10, 15)) == "booked"               # carries on afterwards
    finally:
        live.urllib.request.urlopen = orig
    env = {"DACHSER_TT_47379200": "KH", "DACHSER_TT_47222882": "K3", "DACHSER_API_KEY": "KH", "DACHSER_API_KEY_DK": "OLD"}
    orig_env = dict(live.os.environ)
    for var in [v for v in live.os.environ if v.startswith("DACHSER_TT_") or v.startswith("DACHSER_API_KEY")]:
        del live.os.environ[var]                 # on GitHub the real secrets are set: test with only these
    live.os.environ.update(env)
    try:
        assert live._dachser_keys() == [("47379200", "KH"), ("47222882", "K3"), ("", "OLD")]   # no duplicate key
    finally:
        live.os.environ.clear(); live.os.environ.update(orig_env)

def test_booking_warning_and_workdays():
    assert live.workdays_until(dt.date(2026, 10, 2), dt.date(2026, 10, 6)) == 2   # Fri -> Tue = Mon, Tue
    open_o = [o for o in ORDERS if o["customer_id"] not in common.INTERNAL_CUSTOMERS]
    tl = FakeTL()
    was, live.BOOKING_WARNING = live.BOOKING_WARNING, True
    try:
        rows = live.build_biggest(open_o, NAMES, dict(VALUES), lambda i: tl.module("genobj", i), FakeDachser(), TODAY)
    finally:
        live.BOOKING_WARNING = was
    r = {x["order"]: x for x in rows}
    assert r["1022352"]["book_now"] and r["1022319"]["book_now"]            # Fri 2 Oct / Mon 5 Oct, not booked
    assert not r["1022811"]["book_now"] and not r["1022422"]["book_now"]    # 12 Oct is far; 1022422 booked
    s = live.biggest_summary(open_o, dict(VALUES), rows, TODAY)
    assert s["top_value_dkk"] == round(sum(VALUES.values())) and s["top_share_pct"] == 100.0 and s["booked"] == 0

def test_live_main_end_to_end():
    store = {}
    common.redis_set = lambda k, v: store.__setitem__(k, json.loads(json.dumps(v, default=str)))
    common.redis_get = lambda k: store.get(k)
    orig_run = rx.run
    rx.run = lambda g: ([], [])
    try:
        payload, health = live.main(tl=FakeTL(), econ=FakeEcon(), graph=object(), dachser=FakeDachser(), today=TODAY,
                                    drive=__import__("test_skill_usage").FakeDrive([]),
                                    weclapp=__import__("test_weclapp").FakeWeclapp([__import__("test_weclapp").WECLAPP]),
                                    plan_source=__import__("test_production").plan_xlsx)
    finally:
        rx.run = orig_run
    assert health["ok"], health
    assert "lk:live" in store and "lk:health:live" in store
    orders = [x["order"] for c in store["lk:live"]["status_board"] for x in c["orders"]]
    assert "1022832" not in orders and "1020354" not in orders           # internal excluded
    assert store["lk:live"]["biggest"][0]["customer"] == "Humble Group USA"
    wc = store["lk:live"]["weclapp"]
    assert wc["tracking_from"] == "2026-10-06" and wc["orders"] == 6 and wc["missing_count"] >= 1

def test_live_failure_keeps_previous_section():
    store = {"lk:live": {"emails_waiting": [{"customer": "old"}]}}
    common.redis_set = lambda k, v: store.__setitem__(k, json.loads(json.dumps(v, default=str)))
    common.redis_get = lambda k: store.get(k)
    def boom(g): raise RuntimeError("Graph 403")
    orig_run = rx.run; rx.run = boom
    try:
        payload, health = live.main(tl=FakeTL(), econ=FakeEcon(), graph=object(), dachser=FakeDachser(), today=TODAY,
                                    drive=__import__("test_skill_usage").FakeDrive([]),
                                    weclapp=__import__("test_weclapp").FakeWeclapp([__import__("test_weclapp").WECLAPP]),
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
    assert r["outside_copy"] == {"count": 0, "answered": 0, "from": "2026-10-12", "pct": None, "by_person": []}   # Sep mails: before the clean slate

    assert k["credit"]["headline"]["credit_pct"] == 20.0 and k["credit"]["to_check"][0]["reason"] == "credited more than invoiced"
    assert k["credit"]["weekly"] == []                                   # W40 is the running week

def test_outside_copy_counts_from_week_42_only():
    rows = [{"in_kpi": True, "answered": True, "business_hours": 2, "received_local": d, "subject": "PO",
             "without_order_copy": oc, "replied_by": who}
            for d, oc, who in (("2026-10-09 09:00", True, "andreas@truecompany.com"),     # before: ignored
                               ("2026-10-12 09:00", True, "valentina@truecompany.com"),
                               ("2026-10-13 09:00", False, "order@truecompany.com"))]
    oc = nightly.response_payload(rows, TODAY)["outside_copy"]
    assert oc["count"] == 1 and oc["answered"] == 2 and oc["pct"] == 50.0 and oc["by_person"] == [("Valentina", 1)]

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

def test_nightly_backup_skips_when_done_today():
    now = dt.datetime.now(common.UTC)
    store = {"lk:health:nightly": {"ok": True, "at": now.isoformat()}}
    orig = common.redis_get; common.redis_get = lambda k: store.get(k)
    try:
        assert nightly.already_done_today()
        store["lk:health:nightly"]["ok"] = False                      # failed run -> backup must run
        assert not nightly.already_done_today()
        store["lk:health:nightly"] = {"ok": True, "at": (now - dt.timedelta(days=1)).isoformat()}
        assert not nightly.already_done_today()                       # yesterday's -> run
        store.clear()
        assert not nightly.already_done_today()                       # nothing yet -> run
    finally:
        common.redis_get = orig

def test_dachser_check_runs_hourly_and_reuses_in_between():
    import dachser_notes
    now = dt.datetime.now(dt.timezone.utc)
    store = {"lk:live": {"dachser_pick": {"connected": True, "count": 1, "orders": [], "checked_at": (now - dt.timedelta(minutes=30)).isoformat()}}}
    built = []
    o_get, o_build = common.redis_get, dachser_notes.build
    common.redis_get = lambda k: store.get(k)
    dachser_notes.build = lambda tl, orders, names, today=None: built.append(1) or {"connected": True, "count": 2, "orders": [], "checked_at": now.isoformat()}
    kw = dict(tl=FakeTL(), econ=FakeEcon(), graph=object(), dachser=FakeDachser(), today=TODAY,
              plan_source=__import__("test_production").plan_xlsx, weclapp=__import__("test_weclapp").FakeWeclapp([__import__("test_weclapp").WECLAPP]),
              drive=__import__("test_skill_usage").FakeDrive([]))
    try:
        p, _ = live.main(**kw)
        assert p["dachser_pick"]["count"] == 2 and built == [1]                  # 30 min old: checked (every 30 min)
        built.clear()
        store["lk:live"]["dachser_pick"]["checked_at"] = (now - dt.timedelta(minutes=10)).isoformat()
        p, _ = live.main(**kw)
        assert p["dachser_pick"]["count"] == 1 and not built                     # 10 min old: reused
        store["lk:live"]["dachser_pick"]["checked_at"] = (now - dt.timedelta(minutes=25)).isoformat()
        p, _ = live.main(**kw)
        assert p["dachser_pick"]["count"] == 2 and built == [1]                  # 25 min old: next half-hour run checks
        p, _ = live.main(force_dachser=True, **kw)
        assert built == [1, 1]
    finally:
        common.redis_get, dachser_notes.build = o_get, o_build

# real Dachser shipments (Glostrup account 47222882, 21-23 Oct 2026)
EB64_CANCELLED = {"id": "4969442874", "shipmentDate": "2026-10-07",
                  "status": [{"statusDate": "2026-10-23", "event": {"code": "K", "description": "Information - Order Cancelled"}}]}
EB64_REBOOKED = {"id": "4969894494", "shipmentDate": "2026-10-22",
                 "status": [{"statusDate": "2026-10-22T14:52:00", "event": {"code": "A", "description": "Outbound"}}]}
BOOKED_ONLY = {"id": "4969683956", "shipmentDate": "2026-10-21", "status": [{"statusDate": "2026-10-21", "event": {"code": "0"}}]}
CUSTOMS_INFO = {"id": "4969925066", "shipmentDate": "2026-10-22",
                "status": [{"statusDate": "2026-10-23", "event": {"code": "K", "description": "Information - Customs clearance handling"}}]}
DELIVERED = {"id": "4969878031", "shipmentDate": "2026-10-21", "status": [
    {"statusDate": "2026-10-21T21:35:00", "event": {"code": "A"}}, {"statusDate": "2026-10-22T02:52:00", "event": {"code": "E"}},
    {"statusDate": "2026-10-22T05:40:00", "event": {"code": "R"}}, {"statusDate": "2026-10-22T09:19:00", "event": {"code": "Z"}}]}

def test_state_from_all_events_and_cancellations():
    D = live.Dachser
    assert D.state_of(BOOKED_ONLY) == "booked" and D.state_of(CUSTOMS_INFO) == "booked"     # info event is not movement
    assert D.state_of(EB64_REBOOKED) == "in transit" and D.state_of(DELIVERED) == "delivered"
    assert D.cancelled(EB64_CANCELLED) and not D.cancelled(CUSTOMS_INFO)
    import urllib.parse as up
    def fake(req, timeout=30):
        ref = up.parse_qs(up.urlparse(req.full_url).query)["tracking-number"][0]
        if ref == "1022352": return _Resp({"shipments": [EB64_CANCELLED, EB64_REBOOKED]})
        if ref == "1023065": return _Resp({"shipments": [BOOKED_ONLY]})
        if ref == "OLDONLY": return _Resp({"shipments": [EB64_CANCELLED]})
        raise urllib.error.HTTPError(req.full_url, 422, "x", {}, io.BytesIO(b"{}"))
    orig = live.urllib.request.urlopen; live.urllib.request.urlopen = fake; live.time.sleep = lambda s: None
    try:
        dc = live.Dachser([("47222882", "K")])
        assert dc.booked("1022352", "EB64 - Truck 54", dt.date(2026, 10, 22)) == "in transit"   # cancelled one ignored
        assert dc.collection["1022352"] == "2026-10-22"
        assert dc.booked("1023065", "", dt.date(2026, 10, 21)) == "booked" and dc.collection["1023065"] == "2026-10-21"
        assert dc.booked("9", "OLDONLY", dt.date(2026, 10, 8)) == "not booked"                  # only a cancelled booking
    finally:
        live.urllib.request.urlopen = orig

def test_book_now_ex_works_and_hannover():
    import dachser_notes
    o = lambda num, cust, name, start: {"order_id": num, "number": num, "customer_id": cust, "name": name,
                                         "start_date": start + " 07:00:00", "deadline_date": start + " 15:00:00", "state": "Pakning"}
    orders = [o("A", "1308", "TR-41", "2026-10-06"),                   # Real Food: ex works
              o("B", "1484", "IOR8600", "2026-10-06"),                 # Hannover warehouse order exists
              o("C", "1692", "EB70 - Truck 60", "2026-10-06"),         # Glostrup, not booked, due -> Book now
              o("D", "1692", "EB71 - Truck 61", "2026-10-20"),         # not booked but far ahead
              o("E", "9999", "LONGHAUL", "2026-10-09")]               # customer with a 5-day rule
    values = {"A": 5e5, "B": 4e5, "C": 3e5, "D": 2e5, "E": 1e5}
    class NotBooked:
        status = "ok"; collection = {}; found_by = {}
        def booked(self, n, po="", start=None): return "not booked"
    orig_h, orig_ahead, was = live.hannover_order, dict(live.BOOK_AHEAD_WORKDAYS), live.BOOKING_WARNING
    live.BOOKING_WARNING = True
    live.hannover_order = lambda po: po == "IOR8600"
    live.BOOK_AHEAD_WORKDAYS["9999"] = 5
    try:
        rows = {r["order"]: r for r in live.build_biggest(orders, {}, values, lambda i: [], NotBooked(), dt.date(2026, 10, 2))}
    finally:
        live.hannover_order = orig_h; live.BOOK_AHEAD_WORKDAYS.clear(); live.BOOK_AHEAD_WORKDAYS.update(orig_ahead)
        live.BOOKING_WARNING = was
    assert rows["A"]["transport"] == "ex works" and not rows["A"]["book_now"]
    assert rows["B"]["transport"] == "hannover" and not rows["B"]["book_now"]
    assert rows["C"]["book_now"] and not rows["D"]["book_now"]
    assert rows["E"]["book_now"]                                          # 5 working days ahead for that customer

def test_hannover_order_uses_the_warehouse_status():
    import dachser_notes
    orig = dachser_notes.dachser, dachser_notes.DACHSER_STATUS_KEY
    dachser_notes.DACHSER_STATUS_KEY = "S"
    dachser_notes.dachser = lambda path, ref: (200, {"deliveryOrders": [{"id": "414-1"}]}) if ref == "IOR8600" else (422, {})
    try:
        assert live.hannover_order("IOR8600") and not live.hannover_order("EB70 - Truck 60") and not live.hannover_order("")
    finally:
        dachser_notes.dachser, dachser_notes.DACHSER_STATUS_KEY = orig

def test_credit_split_by_logistics_owner():
    class Econ:
        def _get(self, url):
            return {"collection": [{"customerNumber": 1175, "email": "invoicedkpur@sallinggroup.com"},   # real e-conomic e-mails
                                   {"customerNumber": 1347, "email": "info@gkr.ee"},
                                   {"customerNumber": 1308, "email": "mark@realfooddistributors.com.au"},
                                   {"customerNumber": 9999, "email": "someone@unknown-customer.xyz"}], "pagination": {}}
    owners = nightly.customer_owners(Econ())
    assert owners == {"1175": "Frederikke", "1347": "Valentina", "1308": "Andreas"}
    I = lambda n, c, day, amt: {"bookedInvoiceNumber": n, "customer": {"customerNumber": c}, "date": day, "netAmount": amt}
    window = [I(1, 1175, "2026-10-13", 100), I(2, 1175, "2026-10-14", 100), I(3, 1347, "2026-10-13", 100),
              I(4, 1308, "2026-10-13", 100), I(5, 1308, "2026-10-20", 100), I(6, 9999, "2026-10-13", 100),
              I(7, 1175, "2026-10-14", -20)]                                   # credit notes are not invoices
    rows = [{"customer_number": "1175", "date": "2026-10-14", "internal": False},
            {"customer_number": "1308", "date": "2026-10-20", "internal": False},
            {"customer_number": "9999", "date": "2026-10-13", "internal": False}]
    w = nightly.owner_weekly(window, rows, owners, ["2026-W42", "2026-W43"])
    # denominator = ALL non-internal invoices (6 in W42 incl. the customer without an owner, +1 in W43)
    assert w["Frederikke"][0] == {"week": "2026-W42", "credits": 1, "invoices": 5, "pct": 20.0}
    assert w["Frederikke"][1] == {"week": "2026-W43", "credits": 1, "invoices": 6, "pct": 16.7}   # rolling 4 weeks
    assert w["Andreas"][1] == {"week": "2026-W43", "credits": 1, "invoices": 6, "pct": 16.7}
    assert w["Valentina"][1]["pct"] == 0.0
    # the lines plus the unowned customer add up to the overall rate (W42: 2 credit notes of 5 invoices)
    unowned = 1
    assert round(sum(w[o][0]["credits"] for o in w) + unowned, 1) == 2
    w = nightly.owner_weekly(window, rows, owners, ["2026-W47"])                                     # 4 weeks later
    assert w["Frederikke"][0]["invoices"] == 0 and w["Frederikke"][0]["pct"] is None

def test_closeout_share_since_week_41():
    # real (credit notes 5-20 Oct): none of their orders went through order-closeout -> 100%
    rows = [{"credit_note": 20923, "date": "2026-10-08", "internal": False, "orders": [], "heading": "TR-40"},
            {"credit_note": 20887, "date": "2026-10-14", "internal": False, "orders": [1022780], "heading": "1022780 - LVK H&B promotion"},
            {"credit_note": 20921, "date": "2026-10-19", "internal": False, "orders": [], "heading": "PO229336"},
            {"credit_note": 20898, "date": "2026-10-15", "internal": False, "orders": [], "heading": "252402"},     # no TraceLink order
            {"credit_note": 20700, "date": "2026-09-30", "internal": False, "orders": [1022905], "heading": ""},   # before week 41
            {"credit_note": 20701, "date": "2026-10-12", "internal": True, "orders": [], "heading": ""}]
    po = {"tr-40": {1022422}, "po229336": {1022742}}
    closed = {1022905, 1022927, 1023008}                                      # real closeout logs (19-20 Oct)
    s = nightly.closeout_share(rows, closed, po)
    assert s["credit_notes"] == 4 and s["without_closeout"] == 4 and s["pct_without_closeout"] == 100.0
    assert s["no_order_found"] == 1
    assert {d["credit_note"]: d["orders"] for d in s["detail"]}[20923] == [1022422]      # found via the PO
    # one credit note on an order that WAS closed out with the skill -> 75% without
    rows.append({"credit_note": 20950, "date": "2026-10-21", "internal": False, "orders": [1023008], "heading": "10087876-Tru"})
    s = nightly.closeout_share(rows, closed, po)
    assert s["credit_notes"] == 5 and s["without_closeout"] == 4 and s["pct_without_closeout"] == 80.0

def test_po_lookup_asks_only_for_the_needed_names():
    calls = []
    class TL:
        def list_orders(self, f):
            calls.append(f)
            db = [{"number": "1022422", "name": "TR-40"}, {"number": "1022742", "name": "PO229336"},
                  {"number": "1020448", "name": "GKR Trade March order 3"}, {"number": "1022999", "name": "Order 2, part B"},
                  {"number": "1022998", "name": "TR-40", "delete_dt": "2026-10-01"}]
            if f["name"].startswith("IN("):
                want = f["name"][3:-1].split(",")
                return [o for o in db if o["name"] in want]
            return [o for o in db if o["name"] == f["name"][1:]]
    m = nightly.po_orders(TL(), {"TR-40", "PO229336", "GKR Trade March order 3", "Order 2, part B", "252402", "", None})
    assert m == {"tr-40": {1022422}, "po229336": {1022742}, "gkr trade march order 3": {1020448}, "order 2, part b": {1022999}}
    assert len(calls) == 2 and calls[0]["name"].startswith("IN(") and calls[1] == {"name": "=Order 2, part B"}
    assert all("start_date" not in c for c in calls)                       # no 8-month bulk list any more

def test_po_lookup_asks_only_for_the_needed_pos():
    # real (26 Oct): listing all sales orders of 8 months hit TraceLink's 1000 limit (1828 orders)
    asked = []
    class TL:
        def list_orders(self, f):
            asked.append(f["name"])
            real = {"TR-40": "1022422", "PO229336": "1022742", "Order 1, B&B": "1022999"}
            if f["name"].startswith("IN("):
                names = f["name"][3:-1].split(",")
            else:
                names = [f["name"][1:]]
            return [{"number": real[n], "name": n, "delete_dt": None} for n in names if n in real]
    rows = [{"credit_note": 1, "date": "2026-10-08", "internal": False, "orders": [], "heading": "TR-40"},
            {"credit_note": 2, "date": "2026-10-19", "internal": False, "orders": [], "heading": "PO229336"},
            {"credit_note": 3, "date": "2026-10-15", "internal": False, "orders": [], "heading": "252402"},
            {"credit_note": 4, "date": "2026-10-14", "internal": False, "orders": [1022780], "heading": "1022780 - LVK"},
            {"credit_note": 5, "date": "2026-10-16", "internal": False, "orders": [], "heading": "Order 1, B&B"}]
    need = {r["heading"] for r in rows if not r["orders"]}
    po = nightly.po_orders(TL(), need)
    assert asked[0].startswith("IN(") and set(asked[0][3:-1].split(",")) == {"TR-40", "PO229336", "252402"}
    assert asked[1] == "=Order 1, B&B"                                   # comma in the name: asked on its own
    s = nightly.closeout_share(rows, {1022742}, po)
    d = {x["credit_note"]: x["orders"] for x in s["detail"]}
    assert d[1] == [1022422] and d[2] == [1022742] and d[3] == [] and d[5] == [1022999]
    assert s["credit_notes"] == 5 and s["without_closeout"] == 4 and s["pct_without_closeout"] == 80.0
    assert s["no_order_found"] == 1

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
