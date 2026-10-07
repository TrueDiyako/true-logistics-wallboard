"""weclapp vs TraceLink (dashboard 2) - real orders of 2-6 Oct 2026."""
import datetime as dt, io, json, sys, urllib.error
import weclapp_check as w

TODAY = dt.date(2026, 10, 8)
MS = lambda y, m, d, h=10: w.cph_midnight_ms(dt.date(y, m, d)) + h * 3600000

def WO(num, company, po, status="ORDER_CONFIRMATION_PRINTED", created=(2026, 10, 6)):
    return {"orderNumber": num, "orderNumberAtCustomer": po, "status": status, "invoiceAddress": {"company": company},
            "orderDate": MS(*created, 0), "createdDate": MS(*created)}

WECLAPP = [
    WO("4667", "dm drogerie markt GmbH", "1003725225"),
    WO("4668", "dm-drogerie markt GmbH + Co. KG", "1003725502", "ORDER_ENTRY_IN_PROGRESS"),
    WO("4670", "Bela Handels GmbH & Co. KG", "01673087 - displays", created=(2026, 10, 7)),
    WO("4671", "Bela Handels GmbH & Co. KG", "01673087", created=(2026, 10, 7)),
    WO("4672", "Hammer Fitness Shop UG", "Order 3 - Hammer Fitness Shop", created=(2026, 10, 7)),
    WO("4673", "REWE Markt GmbH - Lager", "", created=(2026, 10, 7)),                       # no PO
    WO("4674", "Yorck-Kino GmbH", "Yorck Order 07102026", "CANCELLED", created=(2026, 10, 7)),
    {"orderNumber": "4675", "orderNumberAtCustomer": "", "status": "ORDER_ENTRY_IN_PROGRESS",
     "invoiceAddress": {"lastName": "Tobias - test customer"}, "createdDate": MS(2026, 10, 7), "orderDate": MS(2026, 10, 7)},
    WO("4664", "Veganista GmbH", "05102026 - Veganista", created=(2026, 10, 5)),             # before 6 Oct: not tracked
]
TRACELINK = [
    {"number": "1022956", "name": "dm drogerie markt GmbH | 1003725225", "description": "\r\n", "delete_dt": None},
    {"number": "1022970", "name": "Bela Handels GmbH & Co. KG | 01673087", "description": "", "delete_dt": None},
    {"number": "1022971", "name": "Hammer Fitness Shop UG | Order 3", "description": "", "delete_dt": None},
    {"number": "1022799", "name": "P1774", "description": "Orders: 1003694644, Hotel Badischer Hof 2, P3706-3540", "delete_dt": None},
    {"number": "1022999", "name": "Old | 1003725502", "description": "", "delete_dt": "2026-10-07"},   # deleted: doesn't count
]

class FakeWeclapp(w.Weclapp):
    def __init__(self, pages): super().__init__("https://x/webapp/api/v2", "tok"); self.pages, self.calls = pages, []
    def get(self, path, params):
        self.calls.append((path, params)); return {"result": self.pages[params["page"] - 1]}

class FakeTL:
    def list_orders(self, f):
        assert f["customer_id"] == "=1364"; return [dict(o) for o in TRACELINK]

def test_missing_orders_and_rules():
    out = w.build(FakeWeclapp([WECLAPP]), FakeTL(), TODAY)
    miss = {r["weclapp"]: r for r in out["missing"]}
    assert set(miss) == {"4668", "4670", "4673"}, miss          # deleted TL order doesn't count; displays don't ride on 01673087
    assert out["orders"] == 6 and out["in_tracelink"] == 3 and out["missing_count"] == 3 and out["no_po"] == 1
    assert miss["4668"]["age_days"] == 2 and miss["4668"]["customer"] == "dm-drogerie markt GmbH + Co. KG"
    assert [r["weclapp"] for r in out["missing"]][0] == "4668"  # oldest first

def test_paging_and_auth_header():
    fw = FakeWeclapp([[WECLAPP[0]] * w.PAGE_SIZE, [WECLAPP[1]]])
    assert len(fw.sales_orders_since(w.TRACK_FROM)) == w.PAGE_SIZE + 1 and [c[1]["page"] for c in fw.calls] == [1, 2]
    seen = {}
    class R:
        def __enter__(s): return s
        def __exit__(s, *a): pass
        def read(s): return b'{"result": []}'
    def fake(req, timeout=60): seen["h"] = req.headers; seen["url"] = req.full_url; return R()
    orig = w.urllib.request.urlopen; w.urllib.request.urlopen = fake
    try:
        w.Weclapp("https://t.weclapp.com/webapp/api/v2", "secret").sales_orders_since(dt.date(2026, 10, 6))
    finally:
        w.urllib.request.urlopen = orig
    assert seen["h"]["Authenticationtoken"] == "secret" and "orderDate-ge=" in seen["url"]

def test_dates_follow_copenhagen_time():
    assert w.cph_midnight_ms(dt.date(2026, 10, 6)) == 1791237600000          # weclapp's own value for 6 Oct
    assert w.ms_to_local_date(1791276507575) == dt.date(2026, 10, 6)         # 4668 created 10:48 local
    assert w.ms_to_local_date(1791237600000 - 1) == dt.date(2026, 10, 5)

def test_clear_error_on_bad_token():
    def deny(req, timeout=60): raise urllib.error.HTTPError(req.full_url, 401, "x", {}, io.BytesIO(b"unauthorized"))
    orig = w.urllib.request.urlopen; w.urllib.request.urlopen = deny
    try:
        w.Weclapp("https://x", "bad").get("salesOrder", {}); assert False
    except RuntimeError as e:
        assert "weclapp HTTP 401" in str(e)
    finally:
        w.urllib.request.urlopen = orig

def test_gzip_answer_and_retry_on_cut_off_answer():
    # real (7 Oct): weclapp answers gzip-compressed even unasked -> body starts with 1f 8b
    import gzip
    body = json.dumps({"result": [WECLAPP[0]]}).encode()
    answers = [b'{"result": [{"orderNumber": "46', gzip.compress(body)]      # first cut off, then fine
    class R:
        def __init__(s, b): s.b = b
        def __enter__(s): return s
        def __exit__(s, *a): pass
        def read(s): return s.b
    def fake(req, timeout=60):
        assert req.headers["Accept-encoding"] == "gzip"; return R(answers.pop(0))
    orig, sleep = w.urllib.request.urlopen, w.time.sleep
    w.urllib.request.urlopen, w.time.sleep = fake, (lambda s: None)
    try:
        res = w.Weclapp("https://x", "tok").get("salesOrder", {})
    finally:
        w.urllib.request.urlopen, w.time.sleep = orig, sleep
    assert res["result"][0]["orderNumber"] == "4667" and answers == []

if __name__ == "__main__":
    import traceback
    fails = 0
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            try: f(); print("PASS", n)
            except Exception as e: fails += 1; print("FAIL", n, repr(e)); traceback.print_exc()
    sys.exit(fails)
