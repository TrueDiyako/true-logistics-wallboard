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
    """{(day iso, sku): {"cases", "left", "picked", "batches"}} for the given production days.
    cases = unit_order_produced_f of the production order's output line; the batch it created
    (batch_genobj with src_order_id = production order) gives what was picked from it
    (unit_used_f) and what is left (unit_back_f)."""
    out = {}
    for day in days:
        prefix = bbd_name(day)
        for o in tl.list_orders({"name": f"{prefix}%"}):
            if o.get("delete_dt") or not str(o.get("name", "")).startswith(prefix + " ("):
                continue
            outputs = [g for g in tl.module("genobj", o["order_id"]) if str(g.get("output_product")) == "1"]
            batches = tl.objects("batch_genobj", {"src_order_id": f"={o['order_id']}"}) if outputs else []
            for g in outputs:
                q = float(g.get("unit_order_produced_f") or 0)
                if q <= 0:
                    continue
                key = (day.isoformat(), str(g.get("name") or "").strip())
                mine = [bt for bt in batches if str(bt.get("genobj_id")) == str(g.get("object_id"))] or batches
                rec = out.setdefault(key, {"cases": 0, "left": 0, "picked": 0, "batches": []})
                rec["cases"] += round(q)
                rec["left"] += round(sum(float(bt.get("unit_back_f") or 0) for bt in mine))
                rec["picked"] += round(sum(float(bt.get("unit_used_f") or 0) for bt in mine))
                rec["batches"] += [str(bt.get("object_id")) for bt in mine]
    return out


def picks_from_lines(order_lines):
    """[(order, customer, start, line)] -> {batch id: [{"order","customer","start","cases"}]}
    from the 'batches' record of picked order lines."""
    import json as _json
    out = {}
    for order, customer, start, g in order_lines:
        try:
            recs = _json.loads(g.get("batches") or "[]")
        except ValueError:
            continue
        for r in recs:
            q = float(r.get("unit_count_f") or 0)
            if q > 0:
                out.setdefault(str(r.get("batch_genobj_id")), []).append(
                    {"order": order, "customer": customer, "start": start, "cases": round(q)})
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
        rec = actuals[key] if isinstance(actuals[key], dict) else {"cases": actuals[key]}
        same = [x for x in runs if (x["day"], x["sku"]) == key]
        plan_total = sum(x["planned_cases"] if "planned_cases" in x else x["cases"] for x in same) or 1
        r["cases"] = round(rec["cases"] * r["planned_cases"] / plan_total)
        r["status"] = "produced"
        if key not in seen:                     # batch figures go with the first run of that SKU/day
            r["batches"], r["left"], r["picked"] = rec.get("batches", []), rec.get("left"), rec.get("picked")
        seen.add(key)
    for (day, sku), q in actuals.items():
        if (day, sku) not in seen and day < today.isoformat():
            rec = q if isinstance(q, dict) else {"cases": q}
            runs.append({"line": LINES[0], "day": day, "flavour": f"Article {sku}", "lang": "", "bags": 0,
                         "cases": rec["cases"], "planned_cases": 0, "sku": sku, "status": "produced",
                         "unplanned": True, "batches": rec.get("batches", []), "left": rec.get("left"),
                         "picked": rec.get("picked")})
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
        dated_rows = 0
        for row in ws.iter_rows(values_only=True):
            cells = list(row)
            if triplets is None:
                # header row = the row with the "Flavor 1, Flavor 2, ..." titles. Its other titles
                # get overwritten now and then (seen: "Date" -> "9", "Foil 1" -> "O"), so only the
                # Flavor titles are relied on; the dates sit in the column left of "Flavor 1".
                low = [str(c or "").strip().lower() for c in cells]
                flav = [i for i, c in enumerate(low) if c.startswith("flavo")]
                if len(flav) >= 2:
                    triplets = flav
                    date_col = low.index("date") if "date" in low else max(0, flav[0] - 1)
                continue
            day = as_date(cells[date_col]) if date_col < len(cells) else None
            dated_rows += day is not None
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
        if triplets is None:
            raise RuntimeError(f"'{ws.title}': no header row with 'Flavor 1, Flavor 2, ...' found")
        if not dated_rows:
            raise RuntimeError(f"'{ws.title}': no dates found in the column left of 'Flavor 1'")
    return runs


def allocate(runs, reservations, picks=None):
    """reservations: [{sku, customer, order, start, cases}] (open, not shipped, not internal).
    Produced runs with batch figures: first what was already picked from the batch (green),
    then open reservations up to what is actually left on the batch."""
    picks = picks or {}
    left = {}
    for r in sorted(reservations, key=lambda x: (x["start"], x["order"])):
        left.setdefault(r["sku"], []).append(dict(r, left=r["cases"]))
    out = []
    for run in sorted(runs, key=lambda r: (r["day"], LINES.index(r["line"]))):
        done = []
        if run.get("status") == "produced" and run.get("batches") is not None and run.get("left") is not None:
            for b in run["batches"]:
                done += picks.get(b, [])
            found = sum(p["cases"] for p in done)
            done = [{"customer": p["customer"], "cases": p["cases"], "of": p["cases"], "start": p["start"],
                     "ready": False, "picked": True} for p in sorted(done, key=lambda p: p["start"])]
            if (run.get("picked") or 0) > found:      # picked by orders that are already closed
                done.append({"customer": "other orders", "cases": run["picked"] - found, "of": run["picked"] - found,
                             "start": "", "ready": False, "picked": True})
            cap = run["left"]
        else:
            cap = run["cases"]
        rows = []
        for x in left.get(run["sku"], []):
            if cap <= 0:
                break
            if x["left"] <= 0:
                continue
            take = min(cap, x["left"])
            rows.append({"customer": x["customer"], "cases": take, "of": x["cases"], "start": x["start"],
                         "ready": run.get("status") == "produced", "picked": False})
            x["left"] -= take
            cap -= take
        shown = done + rows
        out.append(dict(run, alloc=shown[:MAX_ROWS_PER_RUN + len(done)] if done else rows[:MAX_ROWS_PER_RUN],
                        more=max(0, len(rows) - MAX_ROWS_PER_RUN), free=cap))
    return out


def build(plan_bytes, reservations, today, actuals=None, picks=None):
    monday = today - dt.timedelta(days=today.weekday())
    runs = apply_actuals(parse_plan(plan_bytes, monday), actuals or {}, today)
    runs = allocate(runs, reservations, picks)
    days = [(monday + dt.timedelta(days=i)).isoformat() for i in range(7)]
    used = [d for i, d in enumerate(days) if i < 5 or any(r["day"] == d for r in runs)]
    iso = monday.isocalendar()
    return {"week": iso[1], "year": iso[0], "monday": monday.isoformat(), "days": used,
            "lines": {ln: [r for r in runs if r["line"] == ln] for ln in LINES}}
