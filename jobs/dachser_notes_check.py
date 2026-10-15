"""
dachser_notes_check.py - local test of the dashboard's Dachser check (self-contained: the
matching below is a copy of jobs/dachser_notes.py). Every open TraceLink order (all customers)
with reservations is checked for a Dachser delivery note. Read-only.
    python dachser_notes_check.py      (in the jobs folder: needs otif_extract.py next to it)
Writes dachser_notes_check.csv. Do NOT commit this file with keys in it.
"""
# ============================ CONFIG ======================================
TRACELINK_TOKEN = "PASTE_TRACELINK_TOKEN_HERE"
DACHSER_NOTES_KEY = "PASTE_DELIVERYNOTES_KEY_HERE"          # deliverynotes API (required)
DACHSER_STATUS_KEY = "PASTE_DELIVERYORDERSTATUS_KEY_HERE"   # deliveryorderstatus API ("" = skip)
# ==========================================================================
import csv
import datetime as dt
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

DACHSER_BASE = "https://api-gateway.dachser.com"
GMBH_CUSTOMER = "1364"
INTERNAL_SKIP = {"0", "2", "1128", "1129", "3019"}       # internal customers (GmbH 1364 is checked)
MATCH_DAYS = 3
HORIZON_DAYS = 14   # Dachser picks shortly before shipping: orders starting later aren't checked yet
STATUS = {"WN": "sent to warehouse", "WK": "in preparation", "WB": "picking started",
          "WA": "ready for dispatch", "WV": "loading", "WF": "order faulty", "0": "no status"}


def refs_of(order):
    """Dachser reference(s) to try for a TraceLink order, most specific first."""
    name = str(order.get("name") or "").strip()
    po = name.split("|", 1)[1].strip() if "|" in name else name
    if not po:
        return []
    out = [po]
    if " - " in po:
        out.append(po.split(" - ", 1)[0].strip())
    return [r for r in out if r]


def dachser(path, ref):
    key = DACHSER_NOTES_KEY if path.endswith("deliverynotes") else DACHSER_STATUS_KEY
    req = urllib.request.Request(f"{DACHSER_BASE}{path}?{urllib.parse.urlencode({'reference-number1': ref})}",
                                 headers={"X-API-Key": key, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            time.sleep(0.25)                                # Dachser: max 5 calls/second
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        time.sleep(0.25)
        if e.code == 401 and path.endswith("deliverynotes"):
            raise RuntimeError("Dachser answered 401 on deliverynotes: DACHSER_NOTES_KEY is not "
                               "subscribed to the delivery notes API")
        if e.code == 401:
            globals()["DACHSER_STATUS_KEY"] = ""             # status column off, check carries on
        return e.code, {}                                    # 400 odd PO text / 422 nothing found


def match(order, refs, calls):
    """Find the Dachser delivery order for this TraceLink order. Returns a dict or None."""
    deadline = str(order.get("deadline_date", ""))[:10]
    for ref in refs:
        code, body = dachser("/rest/v2/deliverynotes", ref)
        calls[0] += 1
        notes = {}
        for x in body.get("deliveryOrders") or []:
            ts = [str(d.get("timestamp", "")) for d in (x.get("documents") or [])]
            if ts:
                notes[x.get("id")] = max(ts)
        if not notes:
            continue
        status = {}
        if DACHSER_STATUS_KEY:
            _, sbody = dachser("/rest/v2/deliveryorderstatus", ref)
            calls[0] += 1
            for x in sbody.get("deliveryOrders") or []:
                st = sorted(x.get("status") or [], key=lambda s: s.get("statusSequence") or 99)
                ev = (st[0].get("event") or {}) if st else {}
                status[x.get("id")] = {"delivery": str(x.get("deliveryDate", ""))[:10], "code": ev.get("code", ""),
                                       "since": str(st[0].get("statusDate", ""))[:16] if st else ""}
        ids = set(notes)
        if len(ids) == 1:
            pick = next(iter(ids))                           # one Dachser order on this PO: it is this one
        else:                                                # several: the delivery date decides
            cands = [(abs((dt.date.fromisoformat(status[i]["delivery"]) - dt.date.fromisoformat(deadline)).days), i)
                     for i in ids if i in status and status[i]["delivery"] and deadline]
            cands = [c for c in cands if c[0] <= MATCH_DAYS]
            if not cands:
                return {"ref": ref, "ambiguous": len(ids)} if not status else None
            pick = min(cands)[1]
        s = status.get(pick, {})
        return {"ref": ref, "dachser_order": pick, "note": notes[pick],
                "status": f'{s.get("code", "")} {STATUS.get(s.get("code", ""), "")}'.strip(),
                "status_since": s.get("since", ""), "dachser_delivery": s.get("delivery", "")}
    return None


def check(tl, orders, names=None, today=None):
    """One row per open order (all customers), with the result of the Dachser check."""
    names, rows, calls = names or {}, [], [0]
    horizon = ((today or dt.date.today()) + dt.timedelta(days=HORIZON_DAYS)).isoformat()
    for o in sorted(orders, key=lambda o: o.get("start_date") or ""):
        cust = str(o.get("customer_id", ""))
        if o.get("delete_dt") or cust in INTERNAL_SKIP or str(o.get("locked", "0")) == "1":
            continue
        name = str(o.get("name") or "")
        customer = name.split("|", 1)[0].strip() if cust == GMBH_CUSTOMER and "|" in name else names.get(cust, cust)
        row = {"order": o["number"], "customer": customer, "name": name, "customer_id": cust,
               "tracelink_state": o.get("state", ""), "start": str(o.get("start_date", ""))[:10],
               "delivery": str(o.get("deadline_date", ""))[:10], "reserved_cases": 0,
               "dachser_ref": "", "dachser_order": "", "dachser_delivery": "", "dachser_status": "",
               "status_since": "", "delivery_note": "", "flag": ""}
        refs = refs_of(o)
        reserved = sum(float(g.get("unit_order_reserved_f") or 0) for g in tl.module("genobj", o["order_id"]))
        row["reserved_cases"] = round(reserved)
        if not refs:
            row["flag"] = "no order name - not checked"
        elif reserved <= 0:
            row["flag"] = "fully picked in TraceLink"
        elif row["start"] and row["start"] > horizon:
            row["flag"] = f"starts after {horizon} - not checked yet"
        else:
            m = match(o, refs, calls)
            if m is None:
                row["flag"] = "no Dachser delivery note"
            elif m.get("ambiguous"):
                row.update(dachser_ref=m["ref"], flag=f"PO on {m['ambiguous']} Dachser orders - check by hand")
            else:
                row.update(dachser_ref=m["ref"], dachser_order=m["dachser_order"], delivery_note=m["note"],
                           dachser_status=m["status"], status_since=m["status_since"],
                           dachser_delivery=m["dachser_delivery"], flag="DELIVERY NOTE - NOT PICKED IN TRACELINK")
        rows.append(row)
    return rows, calls[0]


def build(tl, orders, names, now_utc=None):
    """Payload for the dashboard: the flagged orders, oldest delivery note first."""
    if not DACHSER_NOTES_KEY:
        return {"connected": False}
    rows, calls = check(tl, orders, names)
    now_utc = now_utc or dt.datetime.now(dt.timezone.utc)
    hits = []
    for r in rows:
        if not r["flag"].startswith(("DELIVERY NOTE", "PO on")):
            continue
        try:
            note = dt.datetime.fromisoformat(r["delivery_note"].replace("Z", "+00:00")) if r["delivery_note"] else None
            if note and note.tzinfo is None:
                note = note.replace(tzinfo=dt.timezone.utc)
        except ValueError:
            note = None
        hits.append({"order": r["order"], "customer": r["customer"], "po": r["dachser_ref"],
                     "reserved_cases": r["reserved_cases"], "note": r["delivery_note"],
                     "dachser_status": r["dachser_status"], "check_by_hand": r["flag"].startswith("PO on"),
                     "note_days": (now_utc.date() - note.date()).days if note else None})
    hits.sort(key=lambda h: h["note"] or "9")
    return {"connected": True, "count": len(hits), "orders": hits, "checked": sum(1 for r in rows if r["dachser_ref"]
            or r["flag"] == "no Dachser delivery note"), "dachser_calls": calls, "status_key": bool(DACHSER_STATUS_KEY),
            "checked_at": now_utc.isoformat()}


def main():
    global DACHSER_STATUS_KEY
    import otif_extract as ox
    ox.TRACELINK_TOKEN = TRACELINK_TOKEN.strip()
    if DACHSER_STATUS_KEY.startswith("PASTE_"):
        DACHSER_STATUS_KEY = ""
    tl = ox.TraceLink()
    print("Reading open TraceLink orders and checking each against Dachser ...", flush=True)
    orders = tl.list_orders({"locked": "=0", "order_group_id": f"={ox.SALES_GROUP}"})
    rows, calls = check(tl, orders, today=dt.date.today())
    with open("dachser_notes_check.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["order"], delimiter=";")
        w.writeheader()
        w.writerows(rows)
    hits = [r for r in rows if r["flag"].startswith(("DELIVERY NOTE", "PO on"))]
    print(f"\n{len(rows)} open orders checked with {calls} Dachser calls - "
          f"{len(hits)} with a delivery note but still reserved in TraceLink:\n")
    for r in sorted(hits, key=lambda r: r["delivery_note"] or "9"):
        print(f'  {r["order"]}  cust {r["customer_id"]:<5} {r["name"][:44]:<44} start {r["start"]}  '
              f'note {r["delivery_note"][:16]:<16} {r["dachser_status"]:<22} reserved {r["reserved_cases"]} cs  '
              f'{"" if r["flag"].startswith("DELIVERY") else r["flag"]}')
    other = {}
    for r in rows:
        if r not in hits:
            other[r["flag"]] = other.get(r["flag"], 0) + 1
    print("\nOther orders: " + ", ".join(f"{v} {k}" for k, v in sorted(other.items(), key=lambda x: -x[1])))
    print("\nWrote dachser_notes_check.csv")


if __name__ == "__main__":
    main()
