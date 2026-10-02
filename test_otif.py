import datetime as dt, json, os, shutil, tempfile, threading, csv
from http.server import BaseHTTPRequestHandler, HTTPServer
import otif_extract as ox

TODAY = dt.date(2026, 10, 2)

def src(lines, ddate, rate=747.512038, name="Cust", country=""):
    return json.dumps({"lines": [{"product": {"productNumber": p}, "quantity": q,
                                  "totalNetAmount": q * price, "description": p}
                                 for p, q, price in lines],
                       "delivery": {"deliveryDate": ddate, "country": country} if country else {"deliveryDate": ddate},
                       "exchangeRate": rate, "recipient": {"name": name, "country": "Spain"}})

def g(pn, qty, pick_dt="2026-09-21 13:46:00", as_str=True):
    b = [{"unit_count_f": str(qty), "create_date": pick_dt}] if qty else []
    return {"name": pn, "unit_order_count_f": f"{qty:.6f}",
            "batches": json.dumps(b) if as_str else b}

# --- real order 1022681 GKR "TD September order pt. 2" ---
GKR_H = {"order_id": "1311296", "number": "1022681", "name": "TD September order pt. 2",
         "state": "Shipped", "locked": "1", "deadline_date": "2026-09-28 15:00:00",
         "start_date": "2026-09-24 07:00:00",
         "create_date": "2026-09-09 09:27:36.000", "update_date": "2026-09-25 09:40:07.355",
         "customer_id": "1347"}
GKR_D = {"order": {"order_src_data": src([("6304", 347, 9.84), ("6306", 1076, 9.84),
                                          ("6310", 945, 9.84)], "2026-09-21", name="GKR Trade OÜ", country="Estonia")},
         "genobj": [g("6304", 347), g("6306", 1134, "2026-09-24 15:11:50"), g("6310", 945)],
         "packslip": [{"create_date": "2026-09-24 15:12:11"}]}

# --- real order 1022885 "Order 21.2026" (Healthy Vitafood) ---
VF_H = {"order_id": "1327132", "number": "1022885", "name": "Order 21.2026", "state": "Shipped",
        "locked": "0", "deadline_date": "2026-10-07 15:00:00", "start_date": "2026-09-29 07:00:00", "create_date": "2026-10-02 08:37:33.000",
        "update_date": "2026-10-02 08:43:52.390", "customer_id": "1419"}
VF_LINES = [("3.4", 80, 18), ("7.4", 50, 18), ("10.4", 28, 18), ("10.16", 12, 18), ("4.16", 20, 18),
            ("6.4", 40, 18), ("102.4", 25, 9.9), ("105.4", 20, 9.9), ("6003.2", 20, 10.8)]
VF_D = {"order": {"order_src_data": src(VF_LINES, "2026-10-07", rate=747.546727, name="HEALTHY VITAFOOD, SL")},
        "genobj": [g("3.4", 0), g("10.4", 28), g("7.4", 50), g("10.16", 12), g("4.16", 20),
                   g("6.4", 40), g("102.4", 23), g("105.4", 20), g("6003.2", 20)],
        "packslip": []}

def test_gkr_late_and_overship_fails_in_full():
    row, lines = ox.analyse_order(GKR_H, GKR_D, TODAY)
    assert row["in_full"] is False          # 1076 -> 1134 changed after import
    assert row["on_time"] is False and row["on_time_basis"] == "re-dated later"
    assert row["delta_days"] == 7 and row["dispatch_vs_plan_days"] == 0
    assert row["suspect_missed_redate"] is False
    assert row["otif"] is False
    assert row["over_lines"] == 1
    sw = [l for l in lines if l["product"] == "6306"][0]
    assert sw["over_qty"] == 58 and sw["short_qty"] == 0 and sw["deviation"] == "over"
    ox.EXACT_QTY_REQUIRED = False
    assert ox.analyse_order(GKR_H, GKR_D, TODAY)[0]["in_full"] is True
    ox.EXACT_QTY_REQUIRED = True
    assert row["unit_fill_rate"] == 1.0
    assert row["customer"] == "GKR Trade OÜ"

def test_vitafood_short_and_no_delivery_note():
    row, lines = ox.analyse_order(VF_H, VF_D, TODAY)
    assert row["in_full"] is False
    assert row["short_lines"] == 2
    short = {l["product"]: l["short_qty"] for l in lines if l["short_qty"]}
    assert short == {"3.4": 80, "102.4": 2}
    exp = (80 * 18 + 2 * 9.9) * 7.47546727
    assert abs(row["short_value_dkk"] - exp) < 0.05, row["short_value_dkk"]
    assert row["delta_days"] == 0 and row["on_time"] is True    # date never moved
    assert row["suspect_missed_redate"] is False                # no note -> cannot check
    assert row["otif"] is False
    assert row["ordered_units"] == 295 and row["filled_units"] == 213

def test_non_stock_line_ignored():
    d = json.loads(json.dumps(GKR_D))
    s = json.loads(d["order"]["order_src_data"])
    s["lines"].append({"product": {"productNumber": "FRAGT"}, "quantity": 1, "totalNetAmount": 500})
    s["lines"].append({"description": "text only line"})
    d["order"]["order_src_data"] = json.dumps(s)
    ox.NON_STOCK_PRODUCTS = {"FRAGT"}
    try:
        row, _ = ox.analyse_order(GKR_H, d, TODAY)
    finally:
        ox.NON_STOCK_PRODUCTS = set()
    assert row["removed_products"] == ""
    assert row["ordered_units"] == 347 + 1076 + 945

def _order(lines_ord, picks):
    d = {"order": {"order_src_data": src(lines_ord, "2026-09-21")},
         "genobj": [g(p, q) for p, q in picks],
         "packslip": [{"create_date": "2026-09-18 10:00:00"}]}
    h = dict(GKR_H, deadline_date="2026-09-21 15:00:00", start_date="2026-09-18 07:00:00")
    return ox.analyse_order(h, d, TODAY)

def test_same_flavour_variant_swap_is_in_full():
    # real: Dagrofa 1021618 ordered 6305 Salty Liquorice, got 6005 Salty Liquorice
    row, lines = _order([("6309", 189, 10), ("6305", 189, 10)], [("6309", 189), ("6005", 189)])
    dev = {l["product"]: l["deviation"] for l in lines}
    assert dev == {"6309": "", "6305": "variant swap", "6005": "variant swap"}
    assert row["in_full"] is True and row["otif"] is True and row["substitution"] is False
    assert row["unit_fill_rate"] == 1.0 and row["short_value_dkk"] == 0
    assert row["variant_swap_lines"] == 2 and row["removed_lines"] == 0

def test_gum_variant_split_across_two_labels():
    row, lines = _order([("2", 100, 18)], [("2", 60), ("2.15", 40)])
    assert row["in_full"] is True
    row, _ = _order([("2", 100, 18)], [("2", 60), ("2.15", 30)])
    assert row["in_full"] is False and row["short_value_dkk"] == round(10 * 18 * 7.47512038, 2)

def test_different_flavour_or_private_label_is_still_substitution():
    # EUROBRANDS 6202 Sweet Peach -> 6204 Peanut Butter; Amazing Foods 6005 -> 7005 private label
    row, lines = _order([("6202", 2457, 10)], [("6204", 2457)])
    assert row["in_full"] is False and row["substitution"] is True
    assert {l["product"]: l["deviation"] for l in lines} == {"6202": "removed", "6204": "added"}
    row, _ = _order([("6005", 1890, 10)], [("7005", 1944)])
    assert row["in_full"] is False and row["substitution"] is True and row["unit_fill_rate"] == 0

def test_case_sizes_and_formats_not_merged():
    assert ox.flavour_key("101") == ox.flavour_key("101.15") == "101"
    assert ox.flavour_key("151") == "151"                       # 12-unit case, not 18-unit
    assert ox.flavour_key("6001.2") == ox.flavour_key("6301") == ox.flavour_key("6401.1") == "TD100-01"
    assert ox.flavour_key("8001") == "8001" and ox.flavour_key("7005") == "7005"
    assert ox.flavour_key("6602") == "6602" and ox.flavour_key("601") == "601"

def test_variant_rule_can_be_switched_off():
    ox.VARIANT_SWAP_OK = False
    try:
        row, _ = _order([("6305", 189, 10)], [("6005", 189)])
        assert row["in_full"] is False and row["substitution"] is True
    finally:
        ox.VARIANT_SWAP_OK = True

def test_possible_split_between_trucks_flagged():
    # real pattern: LIDL campaign 1021636 short 152 x Mint, order 1021906 has 152 x Mint extra
    a, la = _order([("2", 300, 18)], [("2", 148)])
    b, lb = _order([("6", 100, 18)], [("6", 100), ("2.15", 152)])
    a = dict(a, order_number="A", customer_id="1"); b = dict(b, order_number="B", customer_id="1")
    for l in la: l["order_number"] = "A"
    for l in lb: l["order_number"] = "B"
    ox.flag_possible_splits([a, b], la + lb)
    assert a["possible_split_with"] == "B"
    assert a["possible_split_value_dkk"] == round(152 * 18 * 7.47512038, 2)
    assert b["possible_split_with"] == ""
    c = dict(b, customer_id="2", possible_split_with="")
    a["possible_split_with"] = ""
    ox.flag_possible_splits([a, c], la + lb)
    assert a["possible_split_with"] == ""          # other customer: no flag

def test_weekly_series_and_customer_ranking():
    def O(cust, week_day, on_time, in_full, ou=10, fu=10, state="Shipped"):
        return {"customer": cust, "requested_date": week_day, "on_time": on_time,
                "otif": on_time is True and in_full, "in_full": in_full, "state": state,
                "on_time_basis": "shipped, date kept", "ordered_units": ou, "filled_units": fu,
                "short_value_dkk": 0 if in_full else 100}
    d1, d2 = dt.date(2026, 9, 21), dt.date(2026, 9, 28)
    orders = [O("A", d1, True, True), O("A", d1, True, False, 10, 8), O("A", d2, False, True),
              O("B", d2, True, True), O("B", d2, True, True), O("B", d2, True, True),
              O("C", d1, True, True)]
    w = {x["week"]: x for x in ox.weekly_series(orders)}
    assert w["2026-W39"]["orders"] == 3 and w["2026-W39"]["otif_pct"] == 66.7
    assert w["2026-W39"]["short_pick_orders_pct"] == 33.3 and w["2026-W39"]["unit_fill_pct"] == 93.3
    r = ox.customer_ranking(orders)
    assert [x["customer"] for x in r if x["ranked"]] == ["B", "A"]      # C has 1 order: not ranked
    a = [x for x in r if x["customer"] == "A"][0]
    assert a["otif_pct"] == 33.3 and a["order_fill_rate_pct"] == 93.3

def test_cancelled_and_backorder_excluded():
    d = dict(VF_D, genobj=[g("3.4", 0)])
    h = dict(VF_H, state="-- none --", locked="1")
    assert ox.analyse_order(h, d, TODAY)[1] == "cancelled (closed without shipping)"
    h2 = dict(VF_H, name="PO-00745_back order")
    assert ox.analyse_order(h2, VF_D, TODAY)[1].startswith("backorder")
    assert ox.analyse_order(VF_H, dict(VF_D, genobj=[]), TODAY)[1].startswith("no Faerdigvarer")

def test_overdue_not_shipped():
    h = dict(GKR_H, state="Ready for warehouse", locked="0", deadline_date="2026-09-21 15:00:00")
    d = dict(GKR_D, packslip=[])
    row, _ = ox.analyse_order(h, d, TODAY)
    assert row["on_time"] is False and row["in_full"] is False
    assert row["on_time_basis"] == "overdue, not shipped"

def test_redated_but_not_shipped_is_already_late():
    h = dict(GKR_H, state="Ready for warehouse", locked="0")
    row, _ = ox.analyse_order(h, dict(GKR_D, packslip=[]), TODAY)
    assert row["on_time"] is False and row["on_time_basis"] == "re-dated later"

def test_redated_earlier_is_on_time():
    h = dict(GKR_H, deadline_date="2026-09-19 15:00:00", start_date="2026-09-17 07:00:00")
    d = dict(GKR_D, packslip=[{"create_date": "2026-09-17 12:00:00"}])
    row, _ = ox.analyse_order(h, d, TODAY)
    assert row["delta_days"] == -2 and row["on_time"] is True

def test_suspect_missed_redate():
    h = dict(GKR_H, deadline_date="2026-09-21 15:00:00", start_date="2026-09-18 07:00:00")
    row, _ = ox.analyse_order(h, GKR_D, TODAY)          # note on 24th, plan 18th, date kept
    assert row["suspect_missed_redate"] is True and row["on_time"] is True
    assert row["dispatch_vs_plan_days"] == 6
    ox.SUSPECT_COUNTS_LATE = True
    try:
        assert ox.analyse_order(h, GKR_D, TODAY)[0]["on_time"] is False
    finally:
        ox.SUSPECT_COUNTS_LATE = False

def test_not_yet_due():
    h = dict(VF_H, state="Ready for warehouse")
    row, _ = ox.analyse_order(h, VF_D, TODAY)
    assert row["on_time"] is None and row["on_time_basis"] == "not yet due"

def test_duplicate_product_lines_aggregate():
    d = {"order": {"order_src_data": src([("6304", 100, 9.84), ("6304", 50, 9.84)], "2026-09-21")},
         "genobj": [g("6304", 100), g("6304", 50, as_str=False)],
         "packslip": [{"create_date": "2026-09-18 10:00:00"}]}
    h = dict(GKR_H, deadline_date="2026-09-21 15:00:00", start_date="2026-09-18 07:00:00")
    row, lines = ox.analyse_order(h, d, TODAY)
    assert row["ordered_units"] == 150 and row["in_full"] and row["on_time"] and row["otif"]
    assert len(lines) == 1

def test_partial_shipments_use_last_note():
    d = dict(GKR_D, packslip=[{"create_date": "2026-09-19 08:00:00"},
                              {"create_date": "2026-09-24 15:12:11"}])
    row, _ = ox.analyse_order(GKR_H, d, TODAY)
    assert row["first_dispatch"] == dt.date(2026, 9, 19)
    assert row["last_dispatch"] == dt.date(2026, 9, 24) and row["delivery_notes"] == 2

def test_exclusions():
    assert ox.analyse_order(GKR_H, {"order": {"order_src_data": ""}, "genobj": [], "packslip": []}, TODAY)[1] == "no e-conomic payload"
    bad = {"order": {"order_src_data": "{not json"}, "genobj": [], "packslip": []}
    assert ox.analyse_order(GKR_H, bad, TODAY)[0] is None
    nod = {"order": {"order_src_data": src([("6304", 1, 1)], "")}, "genobj": [], "packslip": []}
    assert "requested" in ox.analyse_order(GKR_H, nod, TODAY)[1]

def test_added_product_is_a_deviation():
    d = json.loads(json.dumps(GKR_D)); d["genobj"].append(g("9999", 5))
    row, lines = ox.analyse_order(GKR_H, d, TODAY)
    assert [l["deviation"] for l in lines if l["product"] == "9999"] == ["added"]

def test_month_windows():
    w = list(ox.month_windows(dt.date(2026, 6, 15), dt.date(2026, 8, 3)))
    assert w == [(dt.date(2026, 6, 15), dt.date(2026, 6, 30)), (dt.date(2026, 7, 1), dt.date(2026, 7, 31)),
                 (dt.date(2026, 8, 1), dt.date(2026, 8, 3))]

INT_H = dict(GKR_H, order_id="999", number="1022760", name="Hannover restock", customer_id="3019")

class FakeTL:
    def __init__(self): self.calls = 0
    def list_orders(self, filt):
        a, b = filt["create_date"][1:].split(",")
        return [h for h in (GKR_H, VF_H, INT_H) if a <= h["create_date"][:10] <= b[:10]]
    def read_order(self, oid):
        self.calls += 1
        return {"1311296": GKR_D, "1327132": VF_D}[oid]["order"]
    def module(self, m, oid):
        self.calls += 1
        return json.loads(json.dumps({"1311296": GKR_D, "1327132": VF_D}[oid][m]))

def test_run_end_to_end_and_cache():
    tmp = tempfile.mkdtemp()
    ox.OUTPUT_DIR, ox.CACHE_DIR = os.path.join(tmp, "out"), os.path.join(tmp, "cache")
    ox.DATE_FROM, ox.DATE_TO = "2026-09-01", "2026-10-10"
    tl = FakeTL()
    orders, lines, exc = ox.run(tl, today=TODAY)
    assert {o["order_number"] for o in orders} == {"1022681", "1022885"}
    assert [e["reason"] for e in exc] == ["internal customer 3019"]
    with open(os.path.join(ox.OUTPUT_DIR, "otif_lines.csv"), encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f, delimiter=";"))
    assert len(rows) == 3 + 9
    first = tl.calls
    ox.run(tl, today=TODAY)                      # locked GKR now cached
    assert tl.calls - first == 3                 # only unlocked VF re-fetched
    txt = open(os.path.join(ox.OUTPUT_DIR, "otif_summary.txt"), encoding="utf-8").read()
    assert "OTIF (orders):         0.0%  (0/2)" in txt
    assert "On time (orders):      50.0%  (1/2)" in txt
    assert "In full (orders):      0.0%  (0/2)" in txt
    assert "Possible split across orders/trucks: 0 orders" in txt
    assert os.path.getsize(os.path.join(ox.OUTPUT_DIR, "otif_weekly.csv")) > 0
    assert os.path.getsize(os.path.join(ox.OUTPUT_DIR, "otif_customers.csv")) > 0
    assert "Re-dated later:        1 orders, avg +7.0 days" in txt
    assert "Suspect missed re-date: 0 orders" in txt
    assert "shipped without delivery note:       1" in txt
    assert "Unit fill rate:        " in txt
    shutil.rmtree(tmp)

def test_http_layer_headers_and_paging_guard():
    seen = {}
    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            seen["token"] = self.headers.get("x-access-token")
            seen["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            out = json.dumps({"order": [{"order_id": "1"}], "total": 2}).encode()
            self.send_response(200); self.end_headers(); self.wfile.write(out)
        def log_message(self, *a): pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    ox.BASE_URL = f"http://127.0.0.1:{srv.server_port}"
    ox.TRACELINK_TOKEN, ox.SLEEP_S = "tok", 0
    try:
        ox.TraceLink().list_orders({"x": "=1"})
        assert False, "paging guard should fire"
    except RuntimeError as e:
        assert "Paging needed" in str(e)
    assert seen["token"] == "tok"
    assert seen["body"] == {"order": {"limit": 1000, "filter": {"x": "=1"}}}
    srv.shutdown()

if __name__ == "__main__":
    import sys
    fails = 0
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            try: f(); print("PASS", n)
            except Exception as e: fails += 1; print("FAIL", n, repr(e))
    sys.exit(fails)
