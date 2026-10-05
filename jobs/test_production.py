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

if __name__ == "__main__":
    import traceback
    fails = 0
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            try: f(); print("PASS", n)
            except Exception as e: fails += 1; print("FAIL", n, repr(e)); traceback.print_exc()
    sys.exit(fails)
