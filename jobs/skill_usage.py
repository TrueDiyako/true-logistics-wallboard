"""
skill_usage.py - dashboard 1 "AI adoption": how often colleagues use the three Claude skills.

Every use writes one small file to Google Drive "Internal operations/Claude logs", named
  "Skill usage log - <skill> - <YYYY-MM-DD HHMM> - <reference>.txt"
(content "<skill>;<YYYY-MM-DD HH:MM>;<reference>"); the file's owner is the colleague.
The folder is private, so the job reads it with a Google service account that has
Viewer access to the folder (secret GOOGLE_SERVICE_ACCOUNT_JSON).
"""
import datetime as dt
import json
import os
import re
import urllib.parse
import urllib.request

import common

FOLDER_ID = "1IySoDRSkBnAqzRP8R2fiLfJUuwzj4JxH"          # Internal operations / Claude logs
SKILLS = ["tracelink-order-closeout", "internal-purchase", "process-weclapp-order"]
# uses that happened without a log file, added by hand: {(ISO week, skill): count}
MANUAL_USES = {("2026-W42", "internal-purchase"): 24}     # logged by hand on 14 Oct 2026
TRACK_FROM = dt.date(2026, 10, 5)                           # week 41 2026 (logging started in week 42)
_NAME = re.compile(r"Skill usage log - (?P<skill>[\w-]+) - (?P<day>\d{4}-\d{2}-\d{2}) (?P<hm>\d{4})"
                   r"(?: - (?P<ref>.*?))?\.txt$", re.I)
# Drive owner -> first name shown on the TV (anything else: the part before "@"/"." capitalised)
PEOPLE = {"frederikke.truegum@gmail.com": "Frederikke", "valentina.truecompany@gmail.com": "Valentina",
          "andreas@truecompany.com": "Andreas", "marcblickerferrigno@gmail.com": "Marc",
          "djacko@truegum.com": "Djacko", "truedjacko@gmail.com": "Djacko", "tr@truecompany.com": "Tr",
          "ellen@truecompany.com": "Ellen", "frederik.truecompany@gmail.com": "Frederik",
          "raffaella.true@gmail.com": "Raffaella", "zabrinaziegler@gmail.com": "Zabrina"}


def person(email, display=""):
    e = (email or "").lower()
    if e in PEOPLE:
        return PEOPLE[e]
    base = re.split(r"[@.]", e or display or "?")[0]
    return re.sub(r"\d+", "", base).capitalize() or "?"


class Drive:
    """Minimal Drive v3 file listing with a service-account token."""

    def __init__(self, sa_json=None):
        self.sa_json = sa_json if sa_json is not None else os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
        self._token = None

    @property
    def connected(self):
        return bool(self.sa_json.strip())

    def token(self):
        if self._token is None:
            from google.oauth2 import service_account          # pip install google-auth
            from google.auth.transport.requests import Request
            creds = service_account.Credentials.from_service_account_info(
                json.loads(self.sa_json), scopes=["https://www.googleapis.com/auth/drive.readonly"])
            creds.refresh(Request())
            self._token = creds.token
        return self._token

    def list_folder(self, folder_id, since):
        out, page = [], None
        while True:
            params = {"q": f"'{folder_id}' in parents and trashed = false and createdTime >= "
                           f"'{since.isoformat()}T00:00:00Z'",
                      "fields": "nextPageToken,files(name,createdTime,owners(emailAddress,displayName))",
                      "pageSize": 1000, "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}
            if page:
                params["pageToken"] = page
            req = urllib.request.Request("https://www.googleapis.com/drive/v3/files?" + urllib.parse.urlencode(params),
                                         headers={"Authorization": f"Bearer {self.token()}"})
            with urllib.request.urlopen(req, timeout=60) as r:
                res = common.decode_json(r.read())
            out += res.get("files") or []
            page = res.get("nextPageToken")
            if not page:
                return out


def iso_week(d):
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def build(drive, today, track_from=TRACK_FROM):
    if not drive.connected:
        return {"connected": False, "skills": SKILLS}
    uses = []
    for f in drive.list_folder(FOLDER_ID, track_from - dt.timedelta(days=1)):
        m = _NAME.match(f.get("name") or "")
        if not m or m["skill"] not in SKILLS:
            continue
        day = dt.date.fromisoformat(m["day"])
        if day < track_from or day > today:
            continue
        owner = (f.get("owners") or [{}])[0]
        uses.append({"skill": m["skill"], "day": day, "who": person(owner.get("emailAddress"), owner.get("displayName"))})
    weeks, wk = [], track_from - dt.timedelta(days=track_from.weekday())
    while wk <= today:
        weeks.append(iso_week(wk))
        wk += dt.timedelta(days=7)
    this_week = iso_week(today)
    def count(s, w):
        return sum(1 for u in uses if u["skill"] == s and iso_week(u["day"]) == w) + MANUAL_USES.get((w, s), 0)
    weekly = [{"week": w, **{s: count(s, w) for s in SKILLS}} for w in weeks]
    people = {}
    for u in uses:
        if iso_week(u["day"]) == this_week:
            people[u["who"]] = people.get(u["who"], 0) + 1
    manual = sum(v for (w, _), v in MANUAL_USES.items() if w in weeks)
    return {"connected": True, "skills": SKILLS, "tracking_from": track_from.isoformat(), "total": len(uses) + manual,
            "this_week": {s: count(s, this_week) for s in SKILLS},
            "weekly": weekly, "people_this_week": sorted(people.items(), key=lambda x: (-x[1], x[0]))}
