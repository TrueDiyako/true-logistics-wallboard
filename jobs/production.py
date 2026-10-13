"""
production.py - dashboard 5: this week's production on Laudenberg and Bossar, and which
open reservations the new production will cover.

Plan:      "Production plan and Data.xlsx" on Google Drive (shared by link), tabs
           "Production Plan Laudenberg" / "Production Plan Bossar":
           Date | Flavor 1 | Foil 1 | Units 1 | Flavor 2 | ... (units = bags, 12 per case).
           Comment columns are not read.
SKU:       language prefix + flavour code, e.g. Sour Watermelon CZ/SK -> 6206.
Actuals:   once a day is past, its planned quantities are replaced by what was actually
           produced: TraceLink production orders are named after the best-before date
           (production date + 18 months: 12 Oct 2026 -> "12/04-28 (US)"); their output line
           (output_product = 1) gives the article and unit_order_produced_f (cases). They are
           registered the next day at no fixed time, so a past day without them keeps its plan
           and shows "awaiting registration".
Allocate:  per SKU, production in date order (machine doesn't matter) covers open
           reservations (TraceLink unit_order_reserved_f) in start-date order. Current
           stock is ignored - this is only about new production. Shipped and internal
           orders are skipped. Reservations covered by goods already produced are marked
           "ready - pending picking".
"""
import datetime as dt
import io
import re
import urllib.request

PLAN_FILE_ID = "1-Fukp_lmo6f91qXlZ5jiqv6Rgo7Y4hdr"
PLAN_URL = f"https://drive.google.com/uc?export=download&id={PLAN_FILE_ID}"
LINES = ("Laudenberg", "Bossar")
BAGS_PER_CASE = 12
MAX_ROWS_PER_RUN = 4          # shown under a run; the rest is "+n more"

LANG_PREFIX = {"DE/NL": "60", "CZ/SK": "62", "EN/DK": "63", "AU/NZ": "64", "US": "66", "CA": "67"}
FLAVOUR_CODE = {"sour cola": "01", "sweet peach": "02", "cookie dough": "03", "peanut butter": "04",
                "salty liquorice": "05", "sour watermelon": "06", "sour apple": "07", "cinnamon roll": "08",
                "pistachio cream": "09", "caramel popcorn": "10", "banana caramel": "11",
                "sour pineapple": "12", "crunchy coconut": "13"}


BBD_MONTHS = 18


def bbd_name(day):
    """Production day -> the BBD prefix of its production orders: 2026-10-12 -> '12/04-28'."""
    import calendar
    y, m = day.year + (day.month - 1 + BBD_MONTHS) // 12, (day.month - 1 + BBD_MONTHS) % 12 + 1
    d = min(day.day, calendar.monthrange(y, m)[1])
    return f"{d:02d}/{m:02d}-{y % 100:02d}"


def fetch_actuals(tl, days):
    """{(day iso, sku): cases produced} for the given production days, from TraceLink."""
    out = {}
    for day in days:
        prefix = bbd_name(day)
        for o in tl.list_orders({"name": f"{prefix}%"}):
            if o.get("delete_dt") or not str(o.get("name", "")).startswith(prefix + " ("):
                continue
            for g in tl.module("genobj", o["order_id"]):
                if str(g.get("output_product")) != "1":
                    continue
                q = float(g.get("unit_order_produced_f") or 0)
                if q > 0:
                    key = (day.isoformat(), str(g.get("name") or "").strip())
                    out[key] = out.get(key, 0) + round(q)
    return out


def apply_actuals(runs, actuals, today):
    """Past days: planned cases -> produced cases (split over that day's runs of the same SKU
    in proportion to plan). Produced articles that weren't planned are added as extra runs."""
    seen = set()
    for r in runs:
        r["planned_cases"] = r["cases"]
        if r["day"] >= today.isoformat():
            r["status"] = "planned"
            continue
        key = (r["day"], r["sku"])
        if key not in actuals:
            r["status"] = "awaiting registration"
            continue
        same = [x for x in runs if (x["day"], x["sku"]) == key]
        plan_total = sum(x["planned_cases"] if "planned_cases" in x else x["cases"] for x in same) or 1
        r["cases"] = round(actuals[key] * r["planned_cases"] / plan_total)
        r["status"] = "produced"
        seen.add(key)
    for (day, sku), q in actuals.items():
        if (day, sku) not in seen and day < today.isoformat():
            runs.append({"line": LINES[0], "day": day, "flavour": f"Article {sku}", "lang": "", "bags": 0,
                         "cases": q, "planned_cases": 0, "sku": sku, "status": "produced", "unplanned": True})
    return runs


def download_plan(url=PLAN_URL):
    req = urllib.request.Request(url, headers={"User-Agent": "true-logistics-wallboard"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    if not data.startswith(b"PK"):
        raise RuntimeError("Production plan link did not return an Excel file "
                           "(check that the file is still shared 'anyone with the link')")
    return data


def language(foil):
    s = re.sub(r"\(.*?\)", "", str(foil or "")).strip()
    parts = [p.strip() for p in s.split("/") if p.strip()]
    return "/".join(parts[:2]) if parts else ""


def flavour_key(name):
    s = re.sub(r"\(.*?\)", "", str(name or "")).lower()
    s = re.sub(r"\b(old|new)\b", "", s)
    return re.sub(r"\s+", " ", s).strip()


def bags(v):
    if isinstance(v, (int, float)):
        return int(v)
    m = re.match(r"\s*([\d.,]+)", str(v or ""))
    if not m:
        return 0
    try:
        return int(float(m.group(1).replace(".", "").replace(",", "")))
    except ValueError:
        return 0


def as_date(v):
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def parse_plan(xlsx_bytes, week_start):
    """[{line, day, flavour, lang, bags, cases, sku}] for the 7 days from week_start."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), data_only=True, read_only=True)
    week = {week_start + dt.timedelta(days=i) for i in range(7)}
    runs = []
    for line in LINES:
        ws = next((w for w in wb.worksheets if line.lower() in w.title.lower()), None)
        if ws is None:
            raise RuntimeError(f"No '{line}' tab in the production plan")
        triplets, date_col = None, None
        for row in ws.iter_rows(values_only=True):
            cells = list(row)
            if triplets is None:
                low = [str(c or "").strip().lower() for c in cells]
                if "date" in low and any(c.startswith("flavo") for c in low):
                    date_col = low.index("date")
                    triplets = [i for i, c in enumerate(low) if c.startswith("flavo")]
                continue
            day = as_date(cells[date_col]) if date_col < len(cells) else None
            if day not in week:
                continue
            for i in triplets:
                fl, foil, units = (cells[i:i + 3] + [None, None, None])[:3]
                if not fl or not str(fl).strip():
                    continue
                lang = language(foil)
                if str(foil or "").strip().lower() in ("bulk", "") or not lang:
                    continue
                n = bags(units)
                sku = (LANG_PREFIX.get(lang) or "") + (FLAVOUR_CODE.get(flavour_key(fl)) or "")
                runs.append({"line": line, "day": day.isoformat(), "flavour": str(fl).strip(),
                             "lang": lang, "bags": n, "cases": n // BAGS_PER_CASE,
                             "sku": sku if len(sku) == 4 else ""})
    return runs


def allocate(runs, reservations):
    """reservations: [{sku, customer, order, start, cases}] (open, not shipped, not internal)."""
    left = {}
    for r in sorted(reservations, key=lambda x: (x["start"], x["order"])):
        left.setdefault(r["sku"], []).append(dict(r, left=r["cases"]))
    out = []
    for run in sorted(runs, key=lambda r: (r["day"], LINES.index(r["line"]))):
        cap, rows = run["cases"], []
        for x in left.get(run["sku"], []):
            if cap <= 0:
                break
            if x["left"] <= 0:
                continue
            take = min(cap, x["left"])
            rows.append({"customer": x["customer"], "cases": take, "of": x["cases"], "start": x["start"],
                         "ready": run.get("status") == "produced"})
            x["left"] -= take
            cap -= take
        out.append(dict(run, alloc=rows[:MAX_ROWS_PER_RUN], more=max(0, len(rows) - MAX_ROWS_PER_RUN),
                        free=cap))
    return out


def build(plan_bytes, reservations, today, actuals=None):
    monday = today - dt.timedelta(days=today.weekday())
    runs = apply_actuals(parse_plan(plan_bytes, monday), actuals or {}, today)
    runs = allocate(runs, reservations)
    days = [(monday + dt.timedelta(days=i)).isoformat() for i in range(7)]
    used = [d for i, d in enumerate(days) if i < 5 or any(r["day"] == d for r in runs)]
    iso = monday.isocalendar()
    return {"week": iso[1], "year": iso[0], "monday": monday.isoformat(), "days": used,
            "lines": {ln: [r for r in runs if r["line"] == ln] for ln in LINES}}
