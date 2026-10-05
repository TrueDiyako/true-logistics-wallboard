"""
otif_extract.py - On-Time-In-Full / short-pick extract from TraceLink.

For every customer sales order (order group 129) whose ORIGINAL requested
delivery date falls inside the window, compares:

  * ordered   = the e-conomic order as imported into TraceLink
                (order_src_data - a frozen snapshot, never edited later)
  * picked    = the Faerdigvarer tab (module genobj, unit_order_count_f)
  * dispatch  = delivery-note (module packslip) creation date(s)
  * requested = order_src_data.delivery.deliveryDate (customer's requested
                delivery date, original)
On time = TraceLink deadline_date not moved later than requested (delta <= 0).
  * current   = TraceLink deadline_date (may have been re-dated since)

Outputs (in OUTPUT_DIR):
  otif_lines.csv    one row per order x product
  otif_orders.csv   one row per order, with on-time / in-full flags
  otif_excluded.csv orders left out of the KPI, with the reason
  otif_summary.txt  headline KPIs

Pure standard library (no pip install needed). Python 3.9+.
Run:  python otif_extract.py
"""

# ============================ CONFIG ======================================
TRACELINK_TOKEN = "PASTE_TRACELINK_TOKEN_HERE"      # x-access-token
BASE_URL        = "https://tracelink.app/rest"

DATE_FROM = "2026-07-01"   # requested delivery date, inclusive
DATE_TO   = None           # inclusive; None = yesterday
LOOKBACK_DAYS = 120        # also scan orders created this long before DATE_FROM

OUTPUT_DIR = "otif_output"
CACHE_DIR  = "otif_cache"  # locked orders are cached permanently
SLEEP_S    = 0.15          # pause between API calls

# Internal e-conomic customer numbers - excluded from the KPI entirely.
#   2    True. ApS (samples DK)       - samples, trade fairs
#   1128 True online sales (DK)       - not in use
#   1129 True online sales (abroad)   - webshop / Hive restocks
#   3019 True Hannover Restock        - transfers to Dachser Hannover
#   1364 True Company GmbH            - intercompany (DECISION: see notes)
INTERNAL_CUSTOMER_IDS = {"2", "1128", "1129", "3019", "1364"}

# On time = the TraceLink delivery date (deadline_date) was never moved later
# than the customer's original requested date (e-conomic snapshot).
# Transit is already built into TraceLink start_date (= planned dispatch).
# Guard against "forgot to re-date": a delivery note created after start_date
# is flagged as suspect_missed_redate. Set True to count those as late.
SUSPECT_COUNTS_LATE = False

# e-conomic product numbers that are not physical goods (freight, pallets,
# fees). Ordered lines with these numbers are ignored. Every other ordered
# product with no Faerdigvarer line in TraceLink counts as "removed" (0 picked):
# the first run showed 435 of 497 such lines were real SKUs whose line had been
# deleted or swapped for another product.
NON_STOCK_PRODUCTS = set()

# Backorder orders: the shortfall is already counted on the original order,
# so counting the backorder again would double-penalise one miss.
EXCLUDE_BACKORDERS = True
BACKORDER_WORDS = ("back order", "backorder", "back-order", "backlog")

# Same flavour, different label/market variant (e.g. 2 -> 2.15, 6004 -> 6004.1,
# 6305 -> 6005) in the same total quantity counts as IN FULL. In-full, fill
# rate and short value are then judged per flavour, not per article number.
# Flavour key: True Dates 100 g 60xx/62xx/63xx/64xx share the last two digits
# (6001/6201/6301/6401 = Sour Cola); everything else uses the number before
# the first dot (2, 2.15 = Mint; 101, 101.15 = Fresh Mint). Private label
# (7005...), 70 g (80xx), bulk, displays and 12-unit cases stay separate.
VARIANT_SWAP_OK = True
TD100_PREFIXES = ("60", "62", "63", "64")

# Any quantity change after the order reached TraceLink is a deviation.
# True  = a line counts as in full only if picked == ordered (over-shipping
#         or adding a product also fails the order).
# False = a line is in full if picked >= ordered.
EXACT_QTY_REQUIRED = True
# ==========================================================================

# Hosted runs (GitHub Actions) take the keys from environment secrets;
# locally the constants above are used.
import os as _os
for _k in ('TRACELINK_TOKEN',):
    if _os.environ.get(_k):
        globals()[_k] = _os.environ[_k]


import csv
import datetime as dt
import json
import re
import os
import sys
import time
import urllib.error
import urllib.request

SALES_GROUP = "129"


# ----------------------------- HTTP layer ---------------------------------
def _post(path, body=None, retries=4):
    url = BASE_URL.rstrip("/") + path
    data = json.dumps(body or {}).encode("utf-8")
    for attempt in range(retries):
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"x-access-token": TRACELINK_TOKEN,
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                time.sleep(SLEEP_S)
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"HTTP {e.code} on {path}") from None
        except urllib.error.URLError as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"Network error on {path}: {e.reason}") from None


class TraceLink:
    """Thin API wrapper. Swappable in tests."""

    def list_orders(self, filt):
        res = _post("/tracelink/order/list",
                    {"order": {"limit": 1000, "filter": filt}})
        orders = res.get("order") or []
        total = int(res.get("total") or 0)
        if len(orders) != total:
            raise RuntimeError(
                f"Paging needed: got {len(orders)} of {total} for {filt}. "
                "Shorten the window.")
        return orders

    def read_order(self, order_id):
        return _post(f"/tracelink/order/{order_id}").get("order") or {}

    def module(self, module, order_id):
        res = _post(f"/tracelink/order/list/module/{module}/{order_id}")
        return res.get("objects") or []


# ----------------------------- helpers ------------------------------------
def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def to_date(s):
    """'2026-09-24 15:12:11' / '2026-09-21' / '' -> date or None."""
    if not s or str(s).startswith("0000"):
        return None
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def flavour_key(pn):
    pn = str(pn).strip()
    head = pn.split(".")[0]
    if len(head) == 4 and head.isdigit() and head[:2] in TD100_PREFIXES:
        return "TD100-" + head[2:]
    return head


def month_windows(start, end):
    cur = dt.date(start.year, start.month, 1)
    while cur <= end:
        nxt = (cur.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        yield max(cur, start), min(nxt - dt.timedelta(days=1), end)
        cur = nxt


# ----------------------------- caching ------------------------------------
def cached_details(tl, header):
    """Order + genobj + packslip. Locked orders are cached permanently."""
    oid = header["order_id"]
    path = os.path.join(CACHE_DIR, f"{oid}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            c = json.load(f)
        if c.get("update_date") == header.get("update_date"):
            return c
    c = {
        "update_date": header.get("update_date"),
        "order": tl.read_order(oid),
        "genobj": tl.module("genobj", oid),
        "packslip": tl.module("packslip", oid),
    }
    # strip the heavy HTML from delivery notes before caching
    for p in c["packslip"]:
        p.pop("slip_html", None)
        p.pop("data", None)
    if header.get("locked") == "1":
        with open(path, "w", encoding="utf-8") as f:
            json.dump(c, f)
    return c


# ----------------------------- core logic ---------------------------------
def analyse_order(header, details, today):
    """Return (order_row, line_rows) or (None, reason) if excluded."""
    order = details["order"]
    raw = order.get("order_src_data") or ""
    try:
        src = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        src = None
    if not src or not src.get("lines"):
        return None, "no e-conomic payload"

    requested = to_date((src.get("delivery") or {}).get("deliveryDate"))
    if requested is None:
        return None, "no requested delivery date in e-conomic order"

    rate = fnum(src.get("exchangeRate")) / 100.0 or 1.0   # DKK per 1 unit

    # ---- ordered (original), aggregated per product ----
    ordered = {}
    for ln in src["lines"]:
        pn = str((ln.get("product") or {}).get("productNumber") or "").strip()
        if not pn:
            continue                      # text-only lines
        o = ordered.setdefault(pn, {"qty": 0.0, "value": 0.0,
                                    "desc": ln.get("description", "")})
        o["qty"] += fnum(ln.get("quantity"))
        o["value"] += fnum(ln.get("totalNetAmount"))

    # ---- picked, aggregated per product ----
    picked, last_pick = {}, None
    for g in details["genobj"]:
        pn = str(g.get("name") or "").strip()
        picked[pn] = picked.get(pn, 0.0) + fnum(g.get("unit_order_count_f"))
        batches = g.get("batches") or []
        if isinstance(batches, str):
            try:
                batches = json.loads(batches)
            except json.JSONDecodeError:
                batches = []
        for b in batches:
            d = to_date(b.get("create_date"))
            if d and (last_pick is None or d > last_pick):
                last_pick = d

    # ---- dispatch dates from delivery notes ----
    slip_dates = sorted(d for d in (to_date(p.get("create_date"))
                                    for p in details["packslip"]) if d)
    first_disp = slip_dates[0] if slip_dates else None
    last_disp = slip_dates[-1] if slip_dates else None

    state = header.get("state", "")
    shipped = state == "Shipped"
    current = to_date(header.get("deadline_date"))
    customer = (src.get("recipient") or {}).get("name", "")

    # ---- line comparison ----
    lines, tot_ord, tot_fill, val_ord, val_short = [], 0.0, 0.0, 0.0, 0.0
    removed = []
    for pn, o in ordered.items():
        if pn in NON_STOCK_PRODUCTS:
            continue
        if pn not in picked:
            removed.append(pn)            # line deleted / swapped in TraceLink
        p = picked.get(pn, 0.0)
        filled = min(p, o["qty"])
        ok = (p == o["qty"]) if EXACT_QTY_REQUIRED else (p >= o["qty"])
        unit_val = o["value"] / o["qty"] if o["qty"] else 0.0
        short_qty = max(o["qty"] - p, 0.0)
        tot_ord += o["qty"]
        tot_fill += filled
        val_ord += o["value"] * rate
        val_short += short_qty * unit_val * rate
        lines.append({
            "order_number": header["number"], "customer": customer,
            "product": pn, "description": o["desc"],
            "ordered_qty": o["qty"], "picked_qty": p,
            "short_qty": short_qty, "over_qty": max(p - o["qty"], 0.0),
            "line_in_full": ok,
            "deviation": ("removed" if pn not in picked else
                          "short" if p < o["qty"] else
                          "over" if p > o["qty"] else ""),
            "ordered_value_dkk": round(o["value"] * rate, 2),
            "short_value_dkk": round(short_qty * unit_val * rate, 2),
            "requested_date": requested, "state": state,
        })
    for pn in picked:                     # picked but never ordered
        if pn not in ordered and picked[pn] > 0:
            lines.append({
                "order_number": header["number"], "customer": customer,
                "product": pn, "description": "(not on original order)",
                "ordered_qty": 0.0, "picked_qty": picked[pn],
                "short_qty": 0.0, "over_qty": picked[pn],
                "line_in_full": not EXACT_QTY_REQUIRED, "deviation": "added",
                "ordered_value_dkk": 0.0,
                "short_value_dkk": 0.0, "requested_date": requested,
                "state": state,
            })

    if tot_ord == 0:
        return None, "no ordered goods lines"
    if not details["genobj"]:
        return None, "no Faerdigvarer lines at all (handled outside TraceLink?)"
    if (header.get("locked") == "1" and header.get("state") != "Shipped"
            and sum(picked.values()) == 0):
        return None, "cancelled (closed without shipping)"
    if EXCLUDE_BACKORDERS and any(w in header.get("name", "").lower() for w in BACKORDER_WORDS):
        return None, "backorder (shortfall counted on original order)"

    in_full = all(l["line_in_full"] for l in lines)
    variant_swaps = 0
    for l in lines:
        l["flavour"] = flavour_key(l["product"])

    if VARIANT_SWAP_OK:
        # re-judge per flavour: variants of the same flavour net off
        fl = {}
        for l in lines:
            f = fl.setdefault(l["flavour"], {"ord": 0.0, "pick": 0.0, "val": 0.0})
            f["ord"] += l["ordered_qty"]
            f["pick"] += l["picked_qty"]
            f["val"] += l["ordered_value_dkk"]
        tot_fill, val_short, in_full, bag_fix = 0.0, 0.0, True, 0
        for k, f in fl.items():
            if f["pick"] > 0 and abs(f["ord"] - BAG_FACTOR * f["pick"]) < 1e-6:
                tot_ord -= f["ord"] - f["pick"]          # ordered in bags, picked in cases
                f["ord"] = f["pick"]
                bag_fix += 1
            ok = (f["pick"] == f["ord"]) if EXACT_QTY_REQUIRED else (f["pick"] >= f["ord"])
            f["ok"] = ok
            in_full &= ok
            tot_fill += min(f["pick"], f["ord"])
            if f["ord"]:
                val_short += max(f["ord"] - f["pick"], 0.0) * f["val"] / f["ord"]
        for l in lines:
            f = fl[l["flavour"]]
            if l["deviation"] and f["ok"]:
                l["deviation"] = "variant swap"
                l["line_in_full"] = True
                variant_swaps += 1
            l["flavour_short_qty"] = max(f["ord"] - f["pick"], 0.0)

    # ---- on-time: delivery date never moved later than requested ----
    planned_dispatch = to_date(header.get("start_date"))
    delta = (current - requested).days if current else 0
    suspect = bool(shipped and last_disp and planned_dispatch
                   and last_disp > planned_dispatch and delta <= 0)
    if delta > 0:
        on_time, basis = False, "re-dated later"
    elif shipped:
        on_time = not (suspect and SUSPECT_COUNTS_LATE)
        basis = "shipped, date kept"
    elif current and current < today:
        # past the delivery date and nothing dispatched
        on_time, basis, in_full = False, "overdue, not shipped", False
    else:
        on_time, basis = None, "not yet due"

    row = {
        "order_number": header["number"], "order_id": header["order_id"],
        "name": header.get("name", ""), "customer": customer,
        "customer_id": header.get("customer_id"),
        "state": state, "locked": header.get("locked"),
        "created": to_date(header.get("create_date")),
        "requested_date": requested, "current_deadline": current,
        "delta_days": delta, "planned_dispatch": planned_dispatch,
        "first_dispatch": first_disp, "last_dispatch": last_disp,
        "delivery_notes": len(slip_dates), "last_pick": last_pick,
        "dispatch_vs_plan_days": ((last_disp - planned_dispatch).days
                                  if last_disp and planned_dispatch else None),
        "suspect_missed_redate": suspect,
        "on_time": on_time, "on_time_basis": basis,
        "in_full": in_full,
        "otif": (on_time is True) and in_full,
        "ordered_units": tot_ord, "filled_units": tot_fill,
        "unit_fill_rate": round(tot_fill / tot_ord, 4),
        "ordered_value_dkk": round(val_ord, 2),
        "short_value_dkk": round(val_short, 2),
        "variant_swap_lines": variant_swaps,
        "bag_case_fixes": bag_fix if VARIANT_SWAP_OK else 0,
        "short_lines": sum(1 for l in lines if l["deviation"] == "short"),
        "removed_lines": sum(1 for l in lines if l["deviation"] == "removed"),
        "over_lines": sum(1 for l in lines if l["deviation"] == "over"),
        "added_lines": sum(1 for l in lines if l["deviation"] == "added"),
        "substitution": (any(l["deviation"] == "removed" for l in lines)
                         and any(l["deviation"] == "added" for l in lines)),
        "removed_products": ";".join(l["product"] for l in lines if l["deviation"] == "removed"),
    }
    return row, lines


SPLIT_WINDOW_DAYS = 21
# Some customers order in bags while the warehouse picks cases (Matas, Heinemann):
# a flavour ordered at exactly BAG_FACTOR x the picked quantity is a unit difference, not a short.
BAG_FACTOR = 12


def flag_possible_splits(orders, lines):
    """A flavour short on one order while another order of the same customer
    (requested within SPLIT_WINDOW_DAYS) carries more of that flavour than it
    ordered: goods were probably moved between orders / trucks. Flagged only;
    the KPI is unchanged."""
    by_no = {o["order_number"]: o for o in orders}
    net = {}
    for l in lines:
        k = (l["order_number"], flavour_key(l["product"]))
        n = net.setdefault(k, {"ord": 0.0, "pick": 0.0, "val": 0.0})
        n["ord"] += l["ordered_qty"]
        n["pick"] += l["picked_qty"]
        n["val"] += l["ordered_value_dkk"]
    surplus = {}
    for (no, fl), n in net.items():
        if n["pick"] > n["ord"]:
            o = by_no[no]
            surplus.setdefault((o["customer_id"], fl), []).append((o, n["pick"] - n["ord"]))
    for o in orders:
        o["possible_split_with"], o["possible_split_value_dkk"] = "", 0.0
    for (no, fl), n in net.items():
        if n["pick"] >= n["ord"]:
            continue
        o = by_no[no]
        for other, extra in surplus.get((o["customer_id"], fl), []):
            if other is o or abs((other["requested_date"] - o["requested_date"]).days) > SPLIT_WINDOW_DAYS:
                continue
            short_qty = n["ord"] - n["pick"]
            moved = min(short_qty, extra)
            o["possible_split_value_dkk"] = round(o["possible_split_value_dkk"]
                                                  + moved * n["val"] / n["ord"], 2)
            if other["order_number"] not in o["possible_split_with"].split(";"):
                o["possible_split_with"] = ";".join(
                    x for x in (o["possible_split_with"], other["order_number"]) if x)
            break


def pct(n, d):
    return f"{100.0 * n / d:.1f}%" if d else "n/a"


def summarise(orders):
    # on-time / OTIF need a dispatch date; in-full only needs the goods to have left
    timed = [o for o in orders if o["on_time"] is not None]
    filled = [o for o in orders if o["state"] == "Shipped"
              or o["on_time_basis"] == "overdue, not shipped"]
    otif = sum(1 for o in timed if o["otif"])
    ontime = sum(1 for o in timed if o["on_time"])
    infull = sum(1 for o in filled if o["in_full"])
    u_ord = sum(o["ordered_units"] for o in filled)
    u_fill = sum(o["filled_units"] for o in filled)
    v_ord = sum(o["ordered_value_dkk"] for o in filled)
    v_short = sum(o["short_value_dkk"] for o in filled)
    redated = [o for o in orders if o["delta_days"] > 0]
    suspects = [o for o in orders if o["suspect_missed_redate"]]
    split_val = sum(o.get("possible_split_value_dkk", 0) for o in filled)
    split_n = sum(1 for o in filled if o.get("possible_split_with"))
    avg_slip = (sum(o["delta_days"] for o in redated) / len(redated)) if redated else 0
    no_note = sum(1 for o in orders if o["state"] == "Shipped" and o["delivery_notes"] == 0)
    return "\n".join([
        f"Orders in window (requested date):     {len(orders)}",
        f"  on-time base (shipped/late/overdue): {len(timed)}",
        f"  shipped/overdue (in-full base):      {len(filled)}",
        f"  shipped without delivery note:       {no_note}  (missed re-date not checkable)",
        f"  not yet due:                         {sum(1 for o in orders if o['on_time_basis']=='not yet due')}",
        "",
        f"OTIF (orders):         {pct(otif, len(timed))}  ({otif}/{len(timed)})",
        f"On time (orders):      {pct(ontime, len(timed))}  ({ontime}/{len(timed)})",
        f"In full (orders):      {pct(infull, len(filled))}  ({infull}/{len(filled)})",
        f"Unit fill rate:        {pct(u_fill, u_ord)}",
        f"Short value:           {v_short:,.0f} DKK of {v_ord:,.0f} DKK ordered",
        f"Re-dated later:        {len(redated)} orders, avg +{avg_slip:.1f} days",
        f"Possible split across orders/trucks: {split_n} orders, {split_val:,.0f} DKK of the short value "
        f"(same customer, same flavour, surplus on another order within {SPLIT_WINDOW_DAYS} days) "
        f"-> column possible_split_with",
        f"Variant swaps accepted (same flavour, other label): "
        f"{sum(1 for o in filled if o['variant_swap_lines'])} orders",
        f"Deviation types (in-full base): short {sum(1 for o in filled if o['short_lines'])}, "
        f"removed {sum(1 for o in filled if o['removed_lines'])}, "
        f"substituted {sum(1 for o in filled if o['substitution'])}, "
        f"over {sum(1 for o in filled if o['over_lines'])}, "
        f"added {sum(1 for o in filled if o['added_lines'])} orders",
        "",
        f"Suspect missed re-date: {len(suspects)} orders shipped after planned dispatch "
        f"with the date unchanged ({'counted late' if SUSPECT_COUNTS_LATE else 'counted on time'}) "
        f"-> column suspect_missed_redate",
    ])


RANK_MIN_ORDERS = 3      # customers need this many orders in the window to be ranked


def _filled_base(o):
    return o["state"] == "Shipped" or o["on_time_basis"] == "overdue, not shipped"


def weekly_series(orders):
    """Per ISO week of the requested date: OTIF % of all orders,
    short-pick % = orders with any deviation / shipped-or-overdue orders."""
    wk = {}
    for o in orders:
        y, w, _ = o["requested_date"].isocalendar()
        a = wk.setdefault(f"{y}-W{w:02d}", {"orders": 0, "ontime_base": 0, "on_time": 0,
                                             "otif": 0, "fill_base": 0, "short": 0,
                                             "ord_u": 0.0, "fill_u": 0.0})
        a["orders"] += 1
        if o["on_time"] is not None:
            a["ontime_base"] += 1
            a["on_time"] += o["on_time"] is True
            a["otif"] += o["otif"] is True
        if _filled_base(o):
            a["fill_base"] += 1
            a["short"] += not o["in_full"]
            a["ord_u"] += o["ordered_units"]
            a["fill_u"] += o["filled_units"]
    pct = lambda n, d: round(100 * n / d, 1) if d else None
    return [{"week": k, "orders": a["orders"],
             "otif_pct": pct(a["otif"], a["ontime_base"]),
             "on_time_pct": pct(a["on_time"], a["ontime_base"]),
             "short_pick_orders_pct": pct(a["short"], a["fill_base"]),
             "in_full_pct": pct(a["fill_base"] - a["short"], a["fill_base"]),
             "unit_fill_pct": pct(a["fill_u"], a["ord_u"])} for k, a in sorted(wk.items())]


_LEGAL = {"a/s", "aps", "as", "ab", "oy", "gmbh", "ltd", "ltd.", "llc", "llc.", "b.v.", "bv", "s.l.", "sl", "srl",
          "s.r.o.", "kft.", "kft", "d.o.o.", "oü", "sàrl", "sarl", "inc", "inc.", "pty", "co.,", "plc"}
_GENERIC_FIRST = {"the", "green", "real", "gebr.", "gebr", "dm", "out", "first", "good", "healthy", "premium",
                  "euro", "true", "new", "big", "camps", "nordic", "c", "a1"}


def company_key(name):
    """One key per company across e-conomic name variants:
    'HELSAM A/S' / 'Helsam Helsingør' -> helsam, 'Dagrofa ApS' / 'Dagrofa Logistik a/s' -> dagrofa."""
    toks = [t for t in re.split(r"[\s(),]+", (name or "").lower()) if t and t not in _LEGAL]
    if not toks:
        return (name or "").lower()
    return " ".join(toks[:2]) if toks[0] in _GENERIC_FIRST and len(toks) > 1 else toks[0]


def customer_ranking(orders):
    """Per customer: OTIF %, in full %, order fill rate (units). Ranked only
    with at least RANK_MIN_ORDERS orders, so one-order customers don't top the list."""
    cs, names = {}, {}
    for o in orders:
        key = company_key(o["customer"])
        names.setdefault(key, {}).setdefault(o["customer"], 0)
        names[key][o["customer"]] += 1
        a = cs.setdefault(key, {"orders": 0, "tb": 0, "otif": 0, "fb": 0,
                                          "full": 0, "ou": 0.0, "fu": 0.0, "short_dkk": 0.0})
        a["orders"] += 1
        if o["on_time"] is not None:
            a["tb"] += 1
            a["otif"] += o["otif"] is True
        if _filled_base(o):
            a["fb"] += 1
            a["full"] += o["in_full"] is True
            a["ou"] += o["ordered_units"]
            a["fu"] += o["filled_units"]
            a["short_dkk"] += o["short_value_dkk"]
    rows = [{"customer": max(names[c].items(), key=lambda x: x[1])[0], "orders": a["orders"],
             "otif_pct": round(100 * a["otif"] / a["tb"], 1) if a["tb"] else None,
             "in_full_pct": round(100 * a["full"] / a["fb"], 1) if a["fb"] else None,
             "order_fill_rate_pct": round(100 * a["fu"] / a["ou"], 1) if a["ou"] else None,
             "short_value_dkk": round(a["short_dkk"], 0),
             "ranked": a["orders"] >= RANK_MIN_ORDERS and a["tb"] > 0}
            for c, a in cs.items()]
    rows.sort(key=lambda r: (not r["ranked"], -(r["otif_pct"] or 0), -(r["order_fill_rate_pct"] or 0), -r["orders"]))
    return rows


def write_csv(path, rows):
    if not rows:
        open(path, "w").close()
        return
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), delimiter=";")
        w.writeheader()
        w.writerows(rows)


def run(tl, today=None):
    today = today or dt.date.today()
    d_from = dt.date.fromisoformat(DATE_FROM)
    d_to = dt.date.fromisoformat(DATE_TO) if DATE_TO else today - dt.timedelta(days=1)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(CACHE_DIR, exist_ok=True)

    # B<a>,<b> compares against midnight, so end each window on the next day
    # and de-duplicate orders that land on a boundary.
    seen = {}
    for a, b in month_windows(d_from - dt.timedelta(days=LOOKBACK_DAYS), d_to):
        for h in tl.list_orders({"order_group_id": f"={SALES_GROUP}",
                                 "create_date": f"B{a},{b + dt.timedelta(days=1)}"}):
            seen[h["order_id"]] = h
    headers = [h for h in seen.values() if not h.get("delete_dt")]
    internal = [h for h in headers if str(h.get("customer_id")) in INTERNAL_CUSTOMER_IDS]
    headers = [h for h in headers if str(h.get("customer_id")) not in INTERNAL_CUSTOMER_IDS]
    print(f"Scanning {len(headers)} sales orders ...", flush=True)

    orders, lines = [], []
    excluded = [{"order_number": h["number"], "name": h.get("name"),
                 "reason": f"internal customer {h.get('customer_id')}"} for h in internal]
    for i, h in enumerate(headers, 1):
        if i % 25 == 0:
            print(f"  {i}/{len(headers)}", flush=True)
        try:
            det = cached_details(tl, h)
        except RuntimeError as e:
            excluded.append({"order_number": h["number"], "name": h.get("name"),
                             "reason": f"API error: {e}"})
            continue
        row, res = analyse_order(h, det, today)
        if row is None:
            excluded.append({"order_number": h["number"], "name": h.get("name"),
                             "reason": res})
            continue
        if not (d_from <= row["requested_date"] <= d_to):
            continue
        orders.append(row)
        lines += res

    flag_possible_splits(orders, lines)
    write_csv(os.path.join(OUTPUT_DIR, "otif_orders.csv"), orders)
    write_csv(os.path.join(OUTPUT_DIR, "otif_lines.csv"), lines)
    write_csv(os.path.join(OUTPUT_DIR, "otif_excluded.csv"), excluded)
    write_csv(os.path.join(OUTPUT_DIR, "otif_weekly.csv"), weekly_series(orders))
    write_csv(os.path.join(OUTPUT_DIR, "otif_customers.csv"), customer_ranking(orders))
    summary = (f"OTIF extract  {d_from} .. {d_to}   (run {dt.datetime.now():%Y-%m-%d %H:%M})\n\n"
               + summarise(orders)
               + f"\n\nExcluded orders: {len(excluded)}  (see otif_excluded.csv)")
    with open(os.path.join(OUTPUT_DIR, "otif_summary.txt"), "w", encoding="utf-8") as f:
        f.write(summary)
    print("\n" + summary)
    return orders, lines, excluded


if __name__ == "__main__":
    if TRACELINK_TOKEN.startswith("PASTE_"):
        sys.exit("Paste your TraceLink token into TRACELINK_TOKEN at the top of the script.")
    run(TraceLink())
