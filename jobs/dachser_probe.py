"""
dachser_probe.py - which reference does Dachser Track & Trace find our orders by?

For the most recent shipped True ApS sales orders (TraceLink), asks Dachser's
shipmentstatus endpoint once per reference type:
  - TraceLink order number   (e.g. 1022685)
  - customer order / PO      (TraceLink order name, e.g. "PO-TRC-20260921-NEOW")
and reports how many orders each reference finds. Writes:
  dachser_probe.csv       one row per order x reference (HTTP status, shipments found)
  dachser_probe_raw.json  the raw Dachser answers for the first hits (to see the fields)

Run locally (python dachser_probe.py) with the keys below, or in GitHub Actions
(workflow "dachser-probe", keys from secrets). Read-only: it never books or changes anything.
"""
import csv
import datetime as dt
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

# ============================ CONFIG ======================================
TRACELINK_TOKEN = "PASTE_TRACELINK_TOKEN_HERE"
DACHSER_API_KEY = "PASTE_DACHSER_TRACK_AND_TRACE_KEY_HERE"   # the Track & Trace key
DACHSER_CUSTOMER_ID = "47379200"
DACHSER_BASE = "https://api-gateway.dachser.com"
# Dachser sells these as separate subscriptions with separate keys. Both take the
# same tracking-number and return the same shipments; history adds all events.
# The probe finds out which one your key is subscribed to.
ENDPOINTS = ["shipmenthistory", "shipmentstatus"]
ENDPOINT = ENDPOINTS[0]
SALES_GROUP = "129"
LOOKBACK_DAYS = 30        # shipped orders with start date in the last N days
MAX_ORDERS = 25
INTERNAL_CUSTOMERS = {"0", "2", "1128", "1129", "3019", "1364"}
OUT_DIR = "."
# ==========================================================================

# Keys pasted above win. Environment variables are only used when a constant is
# still a placeholder (GitHub Actions). A DACHSER_API_KEY left in the Windows
# environment by the MCP connector would otherwise silently replace your new key.
KEY_SOURCE = {}
for _k in ("TRACELINK_TOKEN", "DACHSER_API_KEY", "DACHSER_CUSTOMER_ID"):
    _v = str(globals()[_k]).strip().strip('"').strip("'")
    if _v.startswith("PASTE_") and os.environ.get(_k):
        _v, KEY_SOURCE[_k] = os.environ[_k].strip(), "environment variable"
    else:
        KEY_SOURCE[_k] = "constant in this script"
    globals()[_k] = _v


def mask(k):
    return f"{k[:4]}...{k[-4:]} ({len(k)} chars)" if len(k) > 8 else "(too short - check the key)"


def tracelink_shipped():
    import otif_extract as ox                     # same TraceLink client as the dashboard jobs
    ox.TRACELINK_TOKEN = TRACELINK_TOKEN
    since = (dt.date.today() - dt.timedelta(days=LOOKBACK_DAYS)).isoformat()
    rows = ox.TraceLink().list_orders({"order_group_id": f"={SALES_GROUP}", "state": "=Shipped",
                                       "start_date": f">{since}"})
    rows = [o for o in rows if str(o.get("customer_id")) not in INTERNAL_CUSTOMERS and not o.get("delete_dt")]
    rows.sort(key=lambda o: o.get("start_date") or "", reverse=True)
    return rows[:MAX_ORDERS]


def dachser(reference, accept_206=True):
    params = {"tracking-number": reference}
    if DACHSER_CUSTOMER_ID:
        params["customer-id"] = DACHSER_CUSTOMER_ID
    q = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{DACHSER_BASE}/rest/v2/{ENDPOINT}?{q}",
                                 headers={"X-API-Key": DACHSER_API_KEY, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = json.loads(r.read() or b"{}")
            status = r.status
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read() or b"{}")
        except Exception:
            body = {}
        status = e.code
    hits = body.get("shipments") if isinstance(body, dict) and "shipments" in body else (body or None)
    n = len(hits) if isinstance(hits, list) else (1 if hits and status == 200 else 0)
    time.sleep(0.25)                 # Dachser limit: 5 calls/second, 2000/day per key
    return status, n, body


def main():
    print(f"Dachser key used: {mask(DACHSER_API_KEY)} from {KEY_SOURCE['DACHSER_API_KEY']}")
    global ENDPOINT
    works = None
    for ep in ENDPOINTS:                               # key check: 422 = key works, nothing found
        ENDPOINT = ep
        status, _, body = dachser("0")
        msg = (body.get("message") if isinstance(body, dict) else "") or ""
        print(f"Key check on /rest/v2/{ep}: HTTP {status}"
              + (" - key accepted" if status in (200, 206, 422) else (f" - {msg}" if msg else "")))
        if status in (200, 206, 422):
            works = ep
            break
    if not works:
        print("\nThe key is rejected by both shipmenthistory and shipmentstatus.\n"
              "  - check it is the key of the Track & Trace subscription, not the trial/stock key\n"
              "  - each API subscription in the Dachser portal has its own key\n"
              "  - a new subscription can take a while to become active")
        return 1
    ENDPOINT = works
    print(f"Using /rest/v2/{ENDPOINT}\n")
    print("Reading recent shipped orders from TraceLink ...", flush=True)
    orders = tracelink_shipped()
    print(f"  {len(orders)} shipped orders, start date in the last {LOOKBACK_DAYS} days\n", flush=True)
    rows, raw, found = [], {}, {"order number": 0, "customer order (PO)": 0}
    for o in orders:
        refs = {"order number": str(o["number"]), "customer order (PO)": (o.get("name") or "").strip()}
        line = f"  {o['number']}  {refs['customer order (PO)'][:32]:<32}"
        for kind, ref in refs.items():
            if not ref:
                rows.append({"order": o["number"], "reference_type": kind, "reference": "", "http": "", "shipments": 0})
                continue
            status, n, body = dachser(ref)
            if status == 429:
                print("\nDachser rate limit hit (429) - wait a minute and run again.")
                return 1
            rows.append({"order": o["number"], "reference_type": kind, "reference": ref, "http": status, "shipments": n})
            found[kind] += n > 0
            if n and len(raw) < 5:
                raw[f"{kind}: {ref}"] = body
            line += f"   {kind}: {'FOUND' if n else '-'} ({status})"
        print(line, flush=True)
    with open(os.path.join(OUT_DIR, "dachser_probe.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["order", "reference_type", "reference", "http", "shipments"], delimiter=";")
        w.writeheader()
        w.writerows(rows)
    with open(os.path.join(OUT_DIR, "dachser_probe_raw.json"), "w", encoding="utf-8") as f:
        json.dump(raw, f, indent=1, ensure_ascii=False)
    print(f"\nSummary over {len(orders)} shipped orders:")
    for kind, n in found.items():
        print(f"  found by {kind:<20} {n:>3}  ({round(100 * n / len(orders)) if orders else 0}%)")
    print("  (orders shipped ex-works / DSV / GLS / Stiller are expected to be missing)")
    print("\nWrote dachser_probe.csv and dachser_probe_raw.json")
    return 0


def lookup_refs(refs):
    """python dachser_probe.py <ref> [<ref> ...] - look up any references (consignment
    number, SSCC, PO, delivery note) on all accounts the key can see."""
    global DACHSER_CUSTOMER_ID, ENDPOINT
    DACHSER_CUSTOMER_ID = ""
    print(f"Dachser key used: {mask(DACHSER_API_KEY)} from {KEY_SOURCE['DACHSER_API_KEY']}")
    out = {}
    for ref in refs:
        for ep in ENDPOINTS:
            ENDPOINT = ep
            status, n, body = dachser(ref)
            if status != 401:
                break
        ships = body.get("shipments") if isinstance(body, dict) else None
        print(f"\n{ref}: HTTP {status} via {ENDPOINT} - {n} shipment(s)")
        for sh in ships or []:
            snd = sh.get("consignor") or {}
            cns = sh.get("consignee") or {}
            last = (sh.get("status") or [{}])[0]
            print(f"  Dachser {sh.get('id')}  date {sh.get('shipmentDate')}  from customer {snd.get('id')} "
                  f"{' '.join(snd.get('names') or [])} ({(snd.get('addressInformation') or {}).get('city', '')})"
                  f" -> {' '.join(cns.get('names') or [])}")
            print(f"    references: " + ", ".join(f"{r.get('code')}={r.get('value')}" for r in sh.get("references") or []))
            print(f"    latest status: {(last.get('event') or {}).get('description', '')} {last.get('statusDate', '')}")
        out[ref] = body
    with open(os.path.join(OUT_DIR, "dachser_lookup_raw.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    print("\nWrote dachser_lookup_raw.json")
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(lookup_refs(sys.argv[1:]) if len(sys.argv) > 1 else main())
