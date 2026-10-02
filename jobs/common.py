"""Shared helpers for the logistics wallboard jobs."""
import datetime as dt
import json
import os
import traceback
import urllib.request

# Vercel's Upstash integration names the variables KV_REST_API_*;
# plain Upstash uses UPSTASH_REDIS_REST_*. Accept both.
REDIS_URL = os.environ.get("KV_REST_API_URL") or os.environ.get("UPSTASH_REDIS_REST_URL", "")
REDIS_TOKEN = os.environ.get("KV_REST_API_TOKEN") or os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")

# Internal / intercompany e-conomic customers - out of every dashboard
INTERNAL_CUSTOMERS = {"0", "2", "1128", "1129", "3019", "1364"}

UTC = dt.timezone.utc


def cph_now():
    """Copenhagen local time without a tz database (EU DST rules)."""
    now = dt.datetime.now(UTC)

    def last_sunday(y, m):
        d = (dt.date(y, m + 1, 1) - dt.timedelta(days=1))
        return d - dt.timedelta(days=(d.weekday() + 1) % 7)
    start = dt.datetime.combine(last_sunday(now.year, 3), dt.time(1), UTC)
    end = dt.datetime.combine(last_sunday(now.year, 10), dt.time(1), UTC)
    off = 2 if start <= now < end else 1
    return (now + dt.timedelta(hours=off)).replace(tzinfo=None)


def _default(o):
    if isinstance(o, (dt.date, dt.datetime)):
        return o.isoformat()
    raise TypeError(type(o))


def redis_set(key, value):
    """SET key <json> via the Upstash REST API. No-op (prints) without credentials."""
    body = json.dumps(["SET", key, json.dumps(value, default=_default)]).encode()
    if not REDIS_URL:
        print(f"[dry-run] would SET {key} ({len(body)} bytes)")
        return
    req = urllib.request.Request(REDIS_URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {REDIS_TOKEN}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        res = json.loads(r.read())
    if res.get("result") != "OK":
        raise RuntimeError(f"Redis SET {key} failed: {res}")


def redis_get(key):
    """GET key -> parsed JSON (None if missing or no credentials)."""
    if not REDIS_URL:
        return None
    body = json.dumps(["GET", key]).encode()
    req = urllib.request.Request(REDIS_URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {REDIS_TOKEN}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        res = json.loads(r.read()).get("result")
    return json.loads(res) if res else None


def keep_previous(payload, key, sections):
    """A failed section keeps yesterday's data instead of blanking the TV."""
    missing = [s for s in sections if s not in payload]
    if missing:
        try:
            prev = redis_get(key) or {}
        except Exception:
            prev = {}
        for s in missing:
            if s in prev:
                payload[s] = prev[s]
                payload.setdefault("stale_sections", []).append(s)
    return payload


class Health:
    """Collects per-section errors; published so the TV can show what is failing."""

    def __init__(self, job):
        self.job, self.errors, self.ok_sections = job, [], []

    def section(self, name, fn, *a, **kw):
        try:
            out = fn(*a, **kw)
            self.ok_sections.append(name)
            return out
        except Exception as e:                      # one source failing must not stop the rest
            self.errors.append({"section": name, "error": f"{type(e).__name__}: {e}"[:300]})
            traceback.print_exc()
            return None

    def publish(self, extra=None):
        rec = {"job": self.job, "at": dt.datetime.now(UTC).isoformat(),
               "ok": not self.errors, "errors": self.errors, "sections_ok": self.ok_sections}
        rec.update(extra or {})
        redis_set(f"lk:health:{self.job}", rec)
        return rec


def last_weeks(rows, key="period", n=14):
    """Keep the last n ISO weeks (rows sorted by 'YYYY-Www')."""
    return sorted(rows, key=lambda r: r[key])[-n:]
