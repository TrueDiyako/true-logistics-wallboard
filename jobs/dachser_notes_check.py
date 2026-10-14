"""
dachser_notes_check.py - Hannover orders that Dachser has picked (a delivery note exists in
eLogistics) but that are not yet mirrored as picked in TraceLink (reservations left).

TraceLink: open orders of True Company GmbH (customer 1364), named "<Customer> | <PO>".
           Only orders with reservations left (unit_order_reserved_f > 0) are checked.
Dachser:   warehouse delivery notes by reference number 1 = the PO
           (GET /rest/v2/deliverynotes; 422 "No Documents were found" = not picked yet),
           plus the warehouse status (GET /rest/v2/deliveryorderstatus) for context:
           WN sent to warehouse - WK in preparation - WB picking started -
           WA ready for dispatch - WV loading.

Read-only. Run locally:  python dachser_notes_check.py
Writes dachser_notes_check.csv (one row per checked order).
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
DACHSER_NOTES_KEY = "PASTE_DELIVERYNOTES_KEY_HERE"     # key subscribed to "deliverynotes" (needed)
DACHSER_STATUS_KEY = ""                               # key subscribed to "deliveryorderstatus" (optional:
                                                      # only adds the Dachser status column; empty = skip)
DACHSER_BASE = "https://api-gateway.dachser.com"
GMBH_CUSTOMER = "1364"
MATCH_DAYS = 3        # one PO on several Dachser orders: delivery dates must be this close
OUT_DIR = "."
# ==========================================================================

for _k in ("TRACELINK_TOKEN", "DACHSER_NOTES_KEY", "DACHSER_STATUS_KEY"):
    _v = str(globals()[_k]).strip().strip('"').strip("'")
    if _v.startswith("PASTE_") and os.environ.get(_k):
        _v = os.environ[_k].strip()
    globals()[_k] = _v

STATUS = {"WN": "sent to warehouse", "WK": "in preparation", "WB": "picking started",
          "WA": "ready for dispatch", "WV": "loading", "WF": "order faulty", "0": "no status"}


def po_of(name):
    """'dm drogerie markt GmbH | 1003725225' -> ['1003725225'];
    'Bela ... | 01673087 - Sour Cola' -> ['01673087 - Sour Cola', '01673087'] (Dachser may hold either)."""
    if "|" not in (name or ""):
        return []
    po = name.split("|", 1)[1].strip()
    out = [po]
    if " - " in po:
        out.append(po.split(" - ", 1)[0].strip())
    return [p for p in out if p]


def dachser(path, ref):
    key = DACHSER_NOTES_KEY if path.endswith("deliverynotes") else DACHSER_STATUS_KEY
    q = urllib.parse.urlencode({"reference-number1": ref})
    req = urllib.request.Request(f"{DACHSER_BASE}{path}?{q}",
                                 headers={"X-API-Key": key, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            time.sleep(0.25)
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        time.sleep(0.25)
        if e.code == 401 and path.endswith("deliverynotes"):
            raise SystemExit("Dachser answered 401 on deliverynotes: DACHSER_NOTES_KEY is not subscribed "
                             "to the delivery notes API.")
        if e.code == 401:
            print("  (status key not accepted - Dachser status column left empty)", flush=True)
            globals()["DACHSER_STATUS_KEY"] = ""
        return e.code, {}


def check(tl, today):
    orders = tl.list_orders({"customer_id": f"={GMBH_CUSTOMER}", "locked": "=0"})
    rows = []
    for o in sorted(orders, key=lambda o: o.get("start_date") or ""):
        if o.get("delete_dt"):
            continue
        refs = po_of(o.get("name"))
        lines = tl.module("genobj", o["order_id"])
        reserved = sum(float(g.get("unit_order_reserved_f") or 0) for g in lines)
        picked = sum(float(g.get("unit_order_count_f") or 0) for g in lines)
        row = {"order": o["number"], "name": o.get("name", ""), "tracelink_state": o.get("state", ""),
               "start": str(o.get("start_date", ""))[:10], "delivery": str(o.get("deadline_date", ""))[:10],
               "reserved_cases": round(reserved), "picked_cases": round(picked),
               "dachser_ref": "", "dachser_order": "", "dachser_delivery": "", "dachser_status": "",
               "status_since": "", "delivery_note": "",
               "flag": ""}
        if not refs:
            row["flag"] = "no PO in name - not checked"
            rows.append(row)
            continue
        if reserved <= 0:
            row["flag"] = "fully picked in TraceLink"
            rows.append(row)
            continue
        deadline = str(o.get("deadline_date", ""))[:10]
        for ref in refs:
            code, body = dachser("/rest/v2/deliverynotes", ref)
            if code != 200 and ref != refs[-1]:
                continue
            row["dachser_ref"] = ref
            # Dachser orders on this PO with their newest delivery-note time
            notes = {}
            for x in body.get("deliveryOrders") or []:
                ts = [str(d.get("timestamp", "")) for d in (x.get("documents") or [])]
                if ts:
                    notes[x.get("id")] = max(ts)
            status = {}
            if DACHSER_STATUS_KEY:
                scode, sbody = dachser("/rest/v2/deliveryorderstatus", ref)
                for x in sbody.get("deliveryOrders") or []:
                    st = sorted(x.get("status") or [], key=lambda s: s.get("statusSequence") or 99)
                    ev = (st[0].get("event") or {}) if st else {}
                    status[x.get("id")] = {"delivery": str(x.get("deliveryDate", ""))[:10],
                                           "code": ev.get("code", ""), "since": str(st[0].get("statusDate", ""))[:16] if st else ""}
            ids = set(notes) | set(status)
            pick = None
            if len(ids) == 1:
                # one Dachser order on this PO: it is this order, whenever TraceLink created it
                pick = next(iter(ids))
            elif len(ids) > 1:
                # one PO, several Dachser orders (seen: Bela 01673087 x3): the delivery date decides
                cands = [(abs((dt.date.fromisoformat(v["delivery"]) - dt.date.fromisoformat(deadline)).days), i)
                         for i, v in status.items() if v["delivery"] and deadline]
                cands = [c for c in cands if c[0] <= MATCH_DAYS]
                if cands:
                    pick = min(cands)[1]
                elif not status:
                    row["flag"] = f"PO on {len(ids)} Dachser orders - check by hand"
            if pick:
                row["dachser_order"] = pick
                row["delivery_note"] = notes.get(pick, "")
                if pick in status:
                    v = status[pick]
                    row["dachser_status"] = f'{v["code"]} {STATUS.get(v["code"], "")}'.strip()
                    row["status_since"] = v["since"]
                    row["dachser_delivery"] = v["delivery"]
            break
        if row["flag"].startswith("PO on"):
            rows.append(row)
            continue
        row["flag"] = ("DELIVERY NOTE - NOT PICKED IN TRACELINK" if row["delivery_note"]
                       else "not picked by Dachser yet")
        rows.append(row)
    return rows


def main():
    import otif_extract as ox                  # the same TraceLink client the dashboard uses
    ox.TRACELINK_TOKEN = TRACELINK_TOKEN
    today = dt.date.today()
    print("Reading open True Company GmbH orders from TraceLink and checking Dachser ...", flush=True)
    rows = check(ox.TraceLink(), today)
    with open(os.path.join(OUT_DIR, "dachser_notes_check.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["order"], delimiter=";")
        w.writeheader()
        w.writerows(rows)
    hits = [r for r in rows if r["flag"].startswith("DELIVERY NOTE")]
    print(f"\n{len(rows)} open orders - {len(hits)} with a Dachser delivery note but still reserved in TraceLink:\n")
    for r in hits:
        print(f'  {r["order"]}  {r["name"][:48]:<48}  note {r["delivery_note"][:16]}  '
              f'Dachser: {r["dachser_status"]:<24} reserved {r["reserved_cases"]} cs')
    others = {}
    for r in rows:
        if not r["flag"].startswith("DELIVERY NOTE"):
            others[r["flag"]] = others.get(r["flag"], 0) + 1
    print("\nOther orders: " + ", ".join(f"{v} {k}" for k, v in others.items()))
    print("\nWrote dachser_notes_check.csv")


if __name__ == "__main__":
    main()
