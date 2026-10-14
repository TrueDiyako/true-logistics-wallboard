"""Tests for dashboard 5 (production plan) - layout copied from the real plan, week 41 2026."""
import datetime as dt, io, sys
import openpyxl
import production as p

def plan_xlsx():
    wb = openpyxl.Workbook(); wb.remove(wb.active)
    hdr = ["Date"] + [f"{k} {i}" for i in range(1, 6) for k in ("Flavor", "Foil", "Units")] + ["Comments"]
    L = wb.create_sheet("Production Plan Laudenberg")
    L.append(["Laudenberg production plan"]); L.append(hdr)
    L.append([dt.datetime(2026, 10, 2), "Sour Cola", "CZ/SK/RO/PL/HU/SI/HR/BG/BA", 10000])            # last week
    L.append([dt.datetime(2026, 10, 5), "Sour Watermelon", "CA", "35000 (all)",
              "Sour Watermelon", "CZ/SK/RO/PL/HU/SI/HR/BG/BA", 12000, None, None, None, None, None, None, None, None, None, "12000 bags extra"])
    L.append([dt.datetime(2026, 10, 6), "Caramel Popcorn (NEW)", "EN/DK/SE/NO/FI/IS (New)", 35000])
    L.append([dt.datetime(2026, 10, 7), "Sour Pineapple", "Bulk", "400kg"])                            # bulk: skipped
    B = wb.create_sheet("Production Plan Bossar")
    B.append(hdr)
    B.append([dt.datetime(2026, 10, 7), "Sour Watermelon", "CZ/SK/RO/PL/HU/SI/HR/BG/BA", 35000])
    B.append([dt.datetime(2026, 10, 10), "Mystery flavour", "XX/YY", 1200])                           # Saturday run, unknown SKU
    f = io.BytesIO(); wb.save(f); return f.getvalue()

RES = [  # real reservations (5 Oct 2026)
    {"sku": "6706", "order": "1022816", "customer": "Humble Group USA", "start": "2026-10-07", "cases": 576},
    {"sku": "6706", "order": "1022811", "customer": "Humble Group USA", "start": "2026-10-15", "cases": 2256},
    {"sku": "6206", "order": "1022847", "customer": "A1 d.o.o.", "start": "2026-10-09", "cases": 945},
    {"sku": "6206", "order": "1022823", "customer": "EUROBRANDS", "start": "2026-10-22", "cases": 1890},
    {"sku": "6206", "order": "1022848", "customer": "A1 d.o.o.", "start": "2026-10-23", "cases": 567},
]

def test_parse_week_only_and_sku_mapping():
    runs = p.parse_plan(plan_xlsx(), dt.date(2026, 10, 5))
    got = [(r["line"], r["day"], r["sku"], r["lang"], r["cases"]) for r in runs]
    assert got == [("Laudenberg", "2026-10-05", "6706", "CA", 2916), ("Laudenberg", "2026-10-05", "6206", "CZ/SK", 1000),
                   ("Laudenberg", "2026-10-06", "6310", "EN/DK", 2916), ("Bossar", "2026-10-07", "6206", "CZ/SK", 2916),
                   ("Bossar", "2026-10-10", "", "XX/YY", 100)]

def test_allocation_by_start_date_across_both_lines():
    out = p.build(plan_xlsx(), RES, dt.date(2026, 10, 5))
    L, B = out["lines"]["Laudenberg"], out["lines"]["Bossar"]
    ca = L[0]
    assert [(a["customer"], a["cases"], a["of"]) for a in ca["alloc"]] == [("Humble Group USA", 576, 576), ("Humble Group USA", 2256, 2256)]
    assert ca["free"] == 84
    cz_mon = L[1]                                   # Monday Laudenberg covers the earliest start first
    assert [(a["cases"], a["of"], a["start"]) for a in cz_mon["alloc"]] == [(945, 945, "2026-10-09"), (55, 1890, "2026-10-22")]
    cz_wed = B[0]                                   # Wednesday Bossar continues where Monday stopped
    assert [(a["cases"], a["of"]) for a in cz_wed["alloc"]] == [(1835, 1890), (567, 567)] and cz_wed["free"] == 514
    assert out["week"] == 41 and out["days"][-1] == "2026-10-10" and len(out["days"]) == 6   # Saturday shown: a run is planned

def test_max_rows_and_more():
    many = [{"sku": "6706", "order": str(i), "customer": f"C{i}", "start": f"2026-10-{10+i}", "cases": 10} for i in range(7)]
    out = p.build(plan_xlsx(), many, dt.date(2026, 10, 8))
    ca = out["lines"]["Laudenberg"][0]
    assert len(ca["alloc"]) == 4 and ca["more"] == 3 and ca["free"] == 2916 - 70

def test_not_excel_is_a_clear_error():
    class R:
        def __enter__(s): return s
        def __exit__(s, *a): pass
        def read(s): return b"<html>Sign in</html>"
    orig = p.urllib.request.urlopen; p.urllib.request.urlopen = lambda *a, **k: R()
    try:
        p.download_plan(); assert False
    except RuntimeError as e:
        assert "anyone with the link" in str(e)
    finally:
        p.urllib.request.urlopen = orig

def test_header_titles_overwritten_still_parsed():
    # real (14 Oct 2026): Laudenberg header "Date" became "9", Bossar "Foil 1" became "O"
    wb = openpyxl.Workbook(); wb.remove(wb.active)
    L = wb.create_sheet("Production Plan Laudenberg")
    L.append(["9", "Flavor 1", "Foil 1", "Units 1", "Flavor 2", "Foil 2", "Units 2", "Comments"])
    L.append([dt.datetime(2026, 10, 12), "Sour Watermelon", "US", 6000, "Sour Watermelon", "CZ/SK/RO/PL/HU/SI/HR/BG", 45000])
    B = wb.create_sheet("Production Plan Bossar")
    B.append(["Date", "Flavor 1", "O", "Units 1", "Flavor 2", "Foil 2", "Units 2"])
    B.append([dt.datetime(2026, 10, 12), "Sour Apple", "CZ/SK/RO/PL", 12000])
    f = io.BytesIO(); wb.save(f)
    runs = p.parse_plan(f.getvalue(), dt.date(2026, 10, 12))
    assert [(r["line"], r["sku"], r["cases"]) for r in runs] == [("Laudenberg", "6606", 500), ("Laudenberg", "6206", 3750),
                                                                 ("Bossar", "6207", 1000)]

def test_missing_header_is_a_clear_error_not_an_empty_week():
    wb = openpyxl.Workbook(); wb.remove(wb.active)
    L = wb.create_sheet("Production Plan Laudenberg"); L.append(["Date", "Taste 1", "Foil 1", "Units 1"])
    B = wb.create_sheet("Production Plan Bossar"); B.append(["Date", "Flavor 1", "Foil 1", "Units 1", "Flavor 2"])
    f = io.BytesIO(); wb.save(f)
    try:
        p.parse_plan(f.getvalue(), dt.date(2026, 10, 12)); assert False
    except RuntimeError as e:
        assert "Laudenberg" in str(e) and "header" in str(e)

def test_bbd_names():
    assert p.bbd_name(dt.date(2026, 10, 12)) == "12/04-28"          # real: Monday's production orders
    assert p.bbd_name(dt.date(2026, 8, 31)) == "29/02-28"           # no 31 Feb -> last day of month

class FakeTLProd:
    # real (13 Oct 2026): production orders for Monday 12 Oct, registered on Tuesday
    ORDERS = {"12/04-28%": [
        {"order_id": "1333701", "name": "12/04-28 (US)", "delete_dt": None},
        {"order_id": "1333706", "name": "12/04-28 (CZ/SK/RO/PL/HU/SI/HR/BA/BG)", "delete_dt": None},
        {"order_id": "1333799", "name": "12/04-28 (US) old", "delete_dt": "2026-10-13"}]}
    LINES = {"1333701": [{"name": "6606", "output_product": "1", "unit_order_produced_f": "439.000000"},
                         {"name": "5.15.06.01", "output_product": "0", "unit_order_produced_f": "0"}],
             "1333706": [{"name": "6206", "output_product": "1", "unit_order_produced_f": "3600.000000"}]}
    def list_orders(self, f): return self.ORDERS.get(f["name"], [])
    def module(self, m, oid): return self.LINES.get(oid, [])

def test_actuals_from_production_orders():
    a = p.fetch_actuals(FakeTLProd(), [dt.date(2026, 10, 12)])
    assert a == {("2026-10-12", "6606"): 439, ("2026-10-12", "6206"): 3600}

def plan_42():
    wb = openpyxl.Workbook(); wb.remove(wb.active)
    hdr = ["Date"] + [f"{k} {i}" for i in range(1, 6) for k in ("Flavor", "Foil", "Units")]
    L = wb.create_sheet("Production Plan Laudenberg"); L.append(hdr)
    L.append([dt.datetime(2026, 10, 12), "Sour Watermelon", "US", 6000, "Sour Watermelon", "CZ/SK/RO/PL", 45000])
    L.append([dt.datetime(2026, 10, 14), "Banana Caramel", "CZ/SK/RO/PL", 80000])
    B = wb.create_sheet("Production Plan Bossar"); B.append(hdr)
    B.append([dt.datetime(2026, 10, 12), "Sour Apple", "CZ/SK/RO/PL", 12000])
    f = io.BytesIO(); wb.save(f); return f.getvalue()

def test_past_days_show_actual_and_ready_pending_picking():
    res = [{"sku": "6606", "order": "1", "customer": "BEBIDAS", "start": "2026-11-03", "cases": 20},
           {"sku": "6206", "order": "2", "customer": "EUROBRANDS", "start": "2026-10-22", "cases": 420},
           {"sku": "6211", "order": "3", "customer": "EUROBRANDS", "start": "2026-10-22", "cases": 3402}]
    actuals = p.fetch_actuals(FakeTLProd(), [dt.date(2026, 10, 12)])
    out = p.build(plan_42(), res, dt.date(2026, 10, 13), actuals)
    L, B = out["lines"]["Laudenberg"], out["lines"]["Bossar"]
    us = L[0]
    assert us["status"] == "produced" and us["planned_cases"] == 500 and us["cases"] == 439
    assert us["alloc"][0]["ready"] and us["free"] == 419
    cz = L[1]
    assert cz["status"] == "produced" and cz["cases"] == 3600 and cz["alloc"][0]["ready"]
    assert B[0]["status"] == "awaiting registration" and B[0]["cases"] == 1000 and not any(a["ready"] for a in B[0]["alloc"])
    bc = L[2]                                                   # Wednesday: still the plan
    assert bc["status"] == "planned" and bc["cases"] == 6666 and not bc["alloc"][0]["ready"]

def test_unplanned_production_is_shown():
    out = p.build(plan_42(), [], dt.date(2026, 10, 13), {("2026-10-12", "6309"): 250})
    extra = [r for r in out["lines"]["Laudenberg"] if r.get("unplanned")]
    assert len(extra) == 1 and extra[0]["cases"] == 250 and extra[0]["sku"] == "6309"

if __name__ == "__main__":
    import traceback
    fails = 0
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            try: f(); print("PASS", n)
            except Exception as e: fails += 1; print("FAIL", n, repr(e)); traceback.print_exc()
    sys.exit(fails)
