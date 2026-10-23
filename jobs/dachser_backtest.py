"""
dachser_backtest.py - how many days before the planned dispatch does logistics book
Dachser, and when do shipments actually leave and arrive?

For every shipped True ApS sales order of the last LOOKBACK_DAYS (TraceLink), looks the
customer order (PO = TraceLink order name) up in Dachser shipmenthistory - the probe showed
Dachser stores the PO (reference codes 003 and 100), never the TraceLink order number.

Per order it records:
  booked     Dachser shipmentDate (the day the shipment was created = booked in eLogistics)
  left       first "Outbound" (A) event      delivered  "Delivered" (Z) event
  start      TraceLink start date (planned dispatch)   deadline  TraceLink delivery date
A hit only counts when the Dachser shipment date lies within MATCH_WINDOW of the start
date, so a generic PO ("2903", "Order - September") can't match someone else's shipment.

Writes dachser_backtest.csv (one row per order) and prints the lead-time summary.
Read-only. One or two Dachser calls per order (limit 2000/day per key).
"""
import csv
import datetime as dt
import json
import os
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request

# ============================ CONFIG ======================================
TRACELINK_TOKEN = "PASTE_TRACELINK_TOKEN_HERE"
# One Track & Trace key per Dachser customer number (empty = account skipped)
ACCOUNTS = {
    "47379200": "PASTE_KEY_HANNOVER",
    "72695099": "PASTE_KEY_72695099",
    "47133021": "PASTE_KEY_47133021",
    "47222882": "PASTE_KEY_47222882",
}
DACHSER_URL = "https://api-gateway.dachser.com/rest/v2/shipmenthistory"
SALES_GROUP = "129"
LOOKBACK_DAYS = 90            # Dachser keeps 180 days of history
MATCH_WINDOW = (-21, 14)      # booked between 21 days before and 14 days after the start date
INTERNAL_CUSTOMERS = {"0", "2", "1128", "1129", "3019", "1364"}
OUT_DIR = "."
# ==========================================================================

for _k in ("TRACELINK_TOKEN",):
    _v = str(globals()[_k]).strip().strip('"').strip("'")
    if _v.startswith("PASTE_") and os.environ.get(_k):
        _v = os.environ[_k].strip()
    globals()[_k] = _v


def d(s):
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def shipped_orders():
    import otif_extract as ox
    ox.TRACELINK_TOKEN = TRACELINK_TOKEN
    since = (dt.date.today() - dt.timedelta(days=LOOKBACK_DAYS)).isoformat()
    rows = ox.TraceLink().list_orders({"order_group_id": f"={SALES_GROUP}", "state": "=Shipped",
                                       "start_date": f">{since}"})
    return [o for o in rows if str(o.get("customer_id")) not in INTERNAL_CUSTOMERS and not o.get("delete_dt")]


def lookup(ref):
    """Ask every account; shipments are tagged with the customer number that found them."""
    found, last = [], 422
    for cust, key in ACCOUNTS.items():
        if not key or key.startswith("PASTE_"):
            continue
        q = urllib.parse.urlencode({"tracking-number": ref, "customer-id": cust})
        req = urllib.request.Request(f"{DACHSER_URL}?{q}", headers={"X-API-Key": key, "Accept": "application/json"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    time.sleep(0.25)
                    ships = json.loads(r.read() or b"{}").get("shipments") or []
                    for sh in ships:
                        sh["_account"] = cust
                    found += ships
                    last = r.status
                    break
            except urllib.error.HTTPError as e:
                time.sleep(0.25)
                if e.code == 429 or e.code >= 500:
                    time.sleep(5 * (attempt + 1))
                    continue
                if e.code == 401:
                    print(f"  key for {cust} refused (401) - check it", flush=True)
                    ACCOUNTS[cust] = ""
                break
            except (TimeoutError, OSError):
                time.sleep(3 * (attempt + 1))
    return (200 if found else last), found


def events(ship, code):
    return sorted(d(s.get("statusDate")) for s in ship.get("status") or []
                  if (s.get("event") or {}).get("code") == code and d(s.get("statusDate")))


def main():
    orders = shipped_orders()
    print(f"{len(orders)} shipped orders, start date in the last {LOOKBACK_DAYS} days. "
          f"Looking up {len(orders)} customer orders in Dachser ...", flush=True)
    rows = []
    for i, o in enumerate(sorted(orders, key=lambda o: o.get("start_date") or "")):
        po, start = (o.get("name") or "").strip(), d(o.get("start_date"))
        row = {"order": o["number"], "customer_order": po, "customer_id": o.get("customer_id"),
               "start": start, "deadline": d(o.get("deadline_date")), "dachser": "not found",
               "booked": None, "left": None, "delivered": None, "consignee": "", "country": "",
               "shipped_from": "", "consignor_id": "",
               "matched_by": "", "booked_days_before_start": None, "left_vs_start_days": None,
               "delivered_vs_deadline_days": None, "http": ""}
        ok = []
        # Glostrup (Dachser Denmark) bookings carry the TraceLink order number,
        # Hannover bookings the customer PO - try the order number first, then the PO
        for kind, ref in (("order number", str(o["number"])), ("customer order", po)):
            if ok or not ref or not start:
                continue
            status, ships = lookup(ref)
            row["http"] = status
            ok = [s for s in ships if d(s.get("shipmentDate"))
                  and MATCH_WINDOW[0] <= (d(s["shipmentDate"]) - start).days <= MATCH_WINDOW[1]]
            if ok:
                row["matched_by"] = kind
            elif ships:
                row["dachser"] = "found, outside date window"
        if True:
            if ok:
                s = min(ok, key=lambda s: abs((d(s["shipmentDate"]) - start).days))
                left, dlv = events(s, "A"), events(s, "Z")
                cons = s.get("consignee") or {}
                snd = s.get("consignor") or {}
                row["consignor_id"] = s.get("_account") or snd.get("id", "")
                row["shipped_from"] = " ".join(snd.get("names") or []) + ", " + \
                    (snd.get("addressInformation") or {}).get("city", "")
                row.update(dachser="booked", booked=d(s["shipmentDate"]),
                           left=left[0] if left else None, delivered=dlv[-1] if dlv else None,
                           consignee=" ".join(cons.get("names") or []),
                           country=(cons.get("addressInformation") or {}).get("countryCode", ""))
                row["booked_days_before_start"] = (start - row["booked"]).days
                if row["left"]:
                    row["left_vs_start_days"] = (row["left"] - start).days
                if row["delivered"] and row["deadline"]:
                    row["delivered_vs_deadline_days"] = (row["delivered"] - row["deadline"]).days
        rows.append(row)
        if (i + 1) % 25 == 0:
            print(f"  {i + 1}/{len(orders)}", flush=True)

    with open(os.path.join(OUT_DIR, "dachser_backtest.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["order"], delimiter=";")
        w.writeheader()
        w.writerows(rows)

    hit = [r for r in rows if r["dachser"] == "booked"]
    lead = [r["booked_days_before_start"] for r in hit]
    print(f"\nFound in Dachser: {len(hit)} of {len(rows)} shipped orders "
          f"(by order number: {sum(1 for r in hit if r['matched_by'] == 'order number')}, "
          f"by customer order: {sum(1 for r in hit if r['matched_by'] == 'customer order')})")
    origins = {}
    for r in hit:
        key = (r["consignor_id"], r["shipped_from"])
        origins[key] = origins.get(key, 0) + 1
    print("Found by Dachser account (customer number):")
    for (cid, frm), n in sorted(origins.items(), key=lambda x: -x[1]):
        print(f"  {n:>4}  customer no. {cid:<10} {frm}")
    if lead:
        q = statistics.quantiles(lead, n=10) if len(lead) >= 10 else [min(lead)] * 9
        print("Booked how many days BEFORE the planned dispatch (start date):")
        print(f"  median {statistics.median(lead):.0f} d · 10% of bookings {q[0]:.0f} d or less · "
              f"90% {q[-1]:.0f} d or less")
        for label, test in (("2+ days before", lambda x: x >= 2), ("1 day before", lambda x: x == 1),
                            ("same day", lambda x: x == 0), ("after the start date", lambda x: x < 0)):
            n = sum(1 for x in lead if test(x))
            print(f"  {label:<22} {n:>4}  ({round(100 * n / len(lead))}%)")
        dep = [r["left_vs_start_days"] for r in hit if r["left_vs_start_days"] is not None]
        if dep:
            print(f"Left Hannover vs start date: median {statistics.median(dep):+.0f} d, "
                  f"{round(100 * sum(1 for x in dep if x <= 0) / len(dep))}% on or before the start date")
        dl = [r["delivered_vs_deadline_days"] for r in hit if r["delivered_vs_deadline_days"] is not None]
        if dl:
            print(f"Delivered vs TraceLink delivery date: {len(dl)} delivered, "
                  f"{round(100 * sum(1 for x in dl if x <= 0) / len(dl))}% on or before the delivery date, "
                  f"median {statistics.median(dl):+.0f} d")
    other = sum(1 for r in rows if r["dachser"] == "found, outside date window")
    if other:
        print(f"{other} POs matched a Dachser shipment far from the start date (ignored - generic PO text)")
    print("\nWrote dachser_backtest.csv")


if __name__ == "__main__":
    main()
