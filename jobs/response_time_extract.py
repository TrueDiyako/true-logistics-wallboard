"""
response_time_extract.py - customer response time on order@truecompany.com.

Reads every message in the shared mailbox (all folders, so mails filed into
sub-folders are included) via Microsoft Graph, groups them by conversationId
and measures, for every time a CUSTOMER was waiting, how long until the team
replied TO THAT CUSTOMER.

Definitions
  inbound   = sent by an external, non-automated address
  team      = sent from the mailbox / aliases / internal domains
  wait      = starts at a customer message that follows a team reply (or opens
              the thread); later customer messages before the reply are
              "chasers" and do not restart the clock
  reply     = first team message whose To/Cc contains the customer's address
              (or domain, except freemail). Internal forwards do NOT count.
  clock     = business hours, Mon-Fri BUSINESS_START-BUSINESS_END Copenhagen
              time, Danish holidays excluded. Calendar hours also reported.

Headline KPI = first response on customer-initiated threads.

Outputs (OUTPUT_DIR):
  response_waits.csv     one row per customer wait (answered or still open)
  response_messages.csv  every message with its classification
  response_distribution_weekly.csv  per ISO week: waits per answer-time bucket
  response_unanswered_orders.csv  headline order mails with no reply found
  response_senders.csv   inbound senders with counts + how they were classified
                         (use it to tune AUTOMATED_* lists)
  response_summary.txt   headline numbers

Graph app needs APPLICATION permission Mail.Read (admin consented). Ideally
restricted to this mailbox with an Exchange ApplicationAccessPolicy.
Pure standard library. Python 3.9+.  Run:  python response_time_extract.py
"""

# ============================ CONFIG ======================================
TENANT_ID     = "PASTE_TENANT_ID_HERE"
CLIENT_ID     = "PASTE_CLIENT_ID_HERE"
CLIENT_SECRET = "PASTE_CLIENT_SECRET_HERE"

MAILBOX   = "order@truecompany.com"
DATE_FROM = "2026-07-01"          # messages received from this date (inclusive)
DATE_TO   = None                  # None = now

OUTPUT_DIR = "response_output"

# Who counts as "us"
TEAM_ADDRESSES = {"order@truecompany.com", "wholesale@truegum.com"}
TEAM_DOMAINS   = {"truecompany.com", "truegum.com", "truegum.de", "thetruegum.com"}

# Senders that never expect an e-mail reply (substring match on address).
# Tune after the first run using response_senders.csv.
AUTOMATED_PATTERNS = (
    "noreply", "no-reply", "no_reply", "donotreply", "do-not-reply",
    "do_not_reply", "mailer-daemon", "postmaster", "notification", "edi@",
    "edi-", "-edi", "logistik-disposition", "sys_", ".app-",
    "mailbestellungen", "posteingangdisposition",
)
AUTOMATED_DOMAINS = {
    "procuros.io", "link-edi.com", "b2bbackbone.com", "gls-denmark.com",
    "gls-group.eu", "dachser.com", "dhl.com", "ups.com", "citti.de",
    "eumail.docusign.net", "t.shopifyemail.com", "transport.coop.dk",
}
# Carriers, 3PLs and service partners: not customers, kept out of the KPI
# (DECISION: Stiller is LVK's UK warehouse - move it out if it should count)
PARTNER_DOMAINS = {
    "dsv.com", "dfds.com", "stiller.co.uk", "balticassist.com", "3pl.dk",
    "mariner.services", "schenker.com", "kuehne-nagel.com", "rhenus.com",
    "ntgneptuntransport.com",
    "lvk.co",            # True's UK operations: FYI forwards to Stiller / Qargo, not customer requests
}
# Only customer mails sent To/Cc these addresses are measured. Mails sent to a
# person's own mailbox (they also land here) are answered from that personal
# mailbox, which this script cannot see.
SHARED_ADDRESSES = {"order@truecompany.com", "wholesale@truegum.com"}
# Finance topics go to accounting, not logistics: reported, not in headline
FINANCE_WORDS = ("invoice", "faktura", "rechnung", "payment", "betaling",
                 "credit note", "kreditnota", "gutschrift", "overdue", "mahnung",
                 "statement", "factura", "fattura", "remittance", "zahlung",
                 "advisering om", "rykker", "betaalspecificatie", "invoicing", "debit note")
# Topics for QA / regulatory / master data, not logistics (DECISION: tune)
NON_LOGISTICS_WORDS = ("compliance", "ppwr", "bpa", "epr", "packaging weight",
                       "data request", "specification", "stammdaten", "1worldsync",
                       "product images", "barcode", "caneva", "new items")
# Customer -> responsible person, by sender domain (customer_owners.csv next to
# this script: domain;customer;responsible;confirm_required). confirm_required=no
# means the customer's mails don't expect an answer (e.g. Helsam's POs say
# "contact us only if the date can't be met") - they stay out of the KPI.
OWNERS_FILE = "customer_owners.csv"

# Only order@ is read (no personal mailboxes). A reply sent from a personal
# mailbox without order@ in copy is detected from the customer's follow-up
# (addressed to that person, or quoting "From: x@truecompany.com / Sent: ..."),
# counted as answered and reported separately as "without order@ in copy".
# Optional: "auto" or a list of addresses also reads those people's Sent Items.
TEAM_REPLY_MAILBOXES = []
INFER_FROM_FOLLOWUPS = True
MAX_BODY_READS = 400             # cap on follow-up bodies read per run
PERSONAL_DOMAINS = {"truecompany.com"}
# shared / function mailboxes are not people - never read as personal Sent Items
SHARED_LOCAL_PARTS = {"order", "orders", "wholesale", "sales", "invoice", "invoices", "quality",
                      "info", "marketing", "hr", "noreply", "no-reply", "finance", "support"}

# A team mail in ANOTHER thread still counts as the reply if it goes to the
# customer within this many days and shares the subject or an order number.
FALLBACK_DAYS = 10
# A reply in a NEW thread (new subject, sometimes to a sister domain such as
# humblegroup.com for a PO from humblegroupusa.com) counts when the team mail goes
# to the same company and either
#   - mentions the PO / order reference of the customer's mail (subject or opening
#     text), within FALLBACK_DAYS, or
#   - is sent within SAME_COMPANY_BH business hours (any subject).
SAME_COMPANY_BH = 8
# Not customers: suppliers' own confirmations, newsletters, service vendors
NON_CUSTOMER_DOMAINS = {
    "exa.ai", "hive.app", "stamegna.eu", "cma-cgm.com", "pakkeshop.dk",
    "lomax.dk", "jakodan.dk", "valpak.co.uk", "di.dk",
}
# Subjects that never ask logistics for an answer (contains, lower-case)
NO_ANSWER_SUBJECT_WORDS = ("ordrebekræftelse", "auftragsbestätigung", "er modtaget",
                           "du kan nu hente pakke", "recall:", "congé",
                           "ingen brugeroplysninger")
AUTO_REPLY_SUBJECTS = ("automatic reply", "autosvar", "automatisk svar",
                       "out of office", "abwesenheit", "automatische antwort",
                       "undeliverable", "delivery status notification")

# Freemail: match the reply on the exact address, never on the domain
FREEMAIL = {"gmail.com", "hotmail.com", "outlook.com", "live.com", "yahoo.com",
            "icloud.com", "gmx.de", "web.de", "mail.dk", "hotmail.dk"}

# Subject words that tag a thread as an order thread
ORDER_WORDS = ("order", "purchase", " po ", "po#", "po-", "po ", "bestelling",
               "bestellung", "ordre", "købsordre", "pedido", "commande",
               "zamówienie", "rekvisition", "tilaus")

BUSINESS_START = 8     # local hour
BUSINESS_END   = 16
# Danish holidays (verify / extend each year)
HOLIDAYS = {
    "2026-01-01", "2026-04-02", "2026-04-03", "2026-04-06", "2026-05-14",
    "2026-05-15", "2026-05-25", "2026-06-05", "2026-12-24", "2026-12-25",
    "2026-12-31",
    "2027-01-01", "2027-03-25", "2027-03-26", "2027-03-29", "2027-05-06",
    "2027-05-07", "2027-05-17", "2027-06-05", "2027-12-24", "2027-12-31",
}
# ==========================================================================

# Hosted runs (GitHub Actions) take the keys from environment secrets;
# locally the constants above are used.
import os as _os
for _k in ('TENANT_ID', 'CLIENT_ID', 'CLIENT_SECRET'):
    if _os.environ.get(_k):
        globals()[_k] = _os.environ[_k]


import csv
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UTC = dt.timezone.utc
AUTH_BASE  = "https://login.microsoftonline.com"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"


# ----------------------------- Graph layer --------------------------------
class Graph:
    def __init__(self):
        self.token, self.expires = None, 0

    def _auth(self):
        if self.token and time.time() < self.expires - 60:
            return self.token
        body = urllib.parse.urlencode({
            "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials"}).encode()
        req = urllib.request.Request(
            f"{AUTH_BASE}/{TENANT_ID}/oauth2/v2.0/token",
            data=body, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                res = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Token request failed: HTTP {e.code} "
                               f"{e.read().decode(errors='replace')[:300]}") from None
        self.token = res["access_token"]
        self.expires = time.time() + int(res.get("expires_in", 3600))
        return self.token

    def get(self, url, retries=5):
        for attempt in range(retries):
            req = urllib.request.Request(url, headers={
                "Authorization": f"Bearer {self._auth()}",
                "Prefer": 'outlook.body-content-type="text"'})
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                    time.sleep(int(e.headers.get("Retry-After") or 2 ** attempt))
                    continue
                msg = e.read().decode(errors="replace")[:300]
                if e.code == 403:
                    msg += ("  -> the app needs APPLICATION permission Mail.Read "
                            "with admin consent, and access to " + MAILBOX)
                raise RuntimeError(f"Graph HTTP {e.code}: {msg}") from None

    def body(self, message_id):
        """Text body of one order@ message (follow-ups only, to read quoted reply headers)."""
        url = (f"{GRAPH_BASE}/users/{urllib.parse.quote(MAILBOX)}/messages/"
               f"{urllib.parse.quote(message_id, safe='')}?$select=body")
        return ((self.get(url) or {}).get("body") or {}).get("content", "")

    def messages(self, since_iso, until_iso, mailbox=None, folder=None):
        sel = ("id,conversationId,subject,from,toRecipients,ccRecipients,"
               "receivedDateTime,sentDateTime,isDraft,parentFolderId,bodyPreview")
        flt = f"receivedDateTime ge {since_iso} and receivedDateTime le {until_iso}"
        box = urllib.parse.quote(mailbox or MAILBOX)
        base = f"{GRAPH_BASE}/users/{box}/" + (f"mailFolders/{folder}/messages" if folder else "messages")
        url = (base + "?"
               + urllib.parse.urlencode({"$select": sel, "$filter": flt, "$top": "500"},
                                       quote_via=urllib.parse.quote))   # spaces as %20, not +
        out = []
        while url:
            res = self.get(url)
            out += res.get("value", [])
            url = res.get("@odata.nextLink")
            print(f"  fetched {len(out)} messages", flush=True)
        return out


# ----------------------------- time helpers -------------------------------
def parse_ts(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)


def _last_sunday(year, month):
    d = dt.date(year, month + 1, 1) - dt.timedelta(days=1) if month < 12 else dt.date(year, 12, 31)
    return d - dt.timedelta(days=(d.weekday() + 1) % 7)


def cph_offset(t_utc):
    """Copenhagen UTC offset (EU DST rules), no tz database needed."""
    y = t_utc.year
    start = dt.datetime.combine(_last_sunday(y, 3), dt.time(1), UTC)
    end = dt.datetime.combine(_last_sunday(y, 10), dt.time(1), UTC)
    return dt.timedelta(hours=2 if start <= t_utc < end else 1)


def to_local(t_utc):
    return (t_utc + cph_offset(t_utc)).replace(tzinfo=None)


def business_hours(a_utc, b_utc):
    """Business hours between two UTC datetimes."""
    if b_utc <= a_utc:
        return 0.0
    a, b = to_local(a_utc), to_local(b_utc)
    total, day = 0.0, a.date()
    while day <= b.date():
        if day.weekday() < 5 and day.isoformat() not in HOLIDAYS:
            ws = dt.datetime.combine(day, dt.time(BUSINESS_START))
            we = dt.datetime.combine(day, dt.time(BUSINESS_END))
            s, e = max(a, ws), min(b, we)
            if e > s:
                total += (e - s).total_seconds() / 3600
        day += dt.timedelta(days=1)
    return total


# ----------------------------- classification -----------------------------
def addr(x):
    return ((x or {}).get("emailAddress") or {}).get("address", "").strip().lower()


def domain(a):
    return a.rsplit("@", 1)[-1] if "@" in a else ""


_SLD = {"co", "com", "org", "net", "ac", "gov", "edu"}


def reg_domain(a):
    """mg.firtal.com -> firtal.com, shop.co.uk -> shop.co.uk"""
    parts = domain(a).split(".")
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in _SLD:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def in_domains(a, doms):
    d = domain(a)
    return any(d == x or d.endswith("." + x) for x in doms)


_PFX = re.compile(r"^\s*((re|sv|aw|vs|fw|fwd|wg|r|tr|ant|odp|antw|rif|回复|答复)"
                  r"(\[\d+\])?\s*[:：]\s*)+", re.I)


def is_reply_subject(s):
    return bool(_PFX.match(s or ""))


def norm_subject(s):
    return _PFX.sub("", s or "").strip().lower()


def order_numbers(s):
    return set(re.findall(r"\d{5,}", s or ""))


_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9_/-]*\d{5,}[A-Za-z0-9_/-]*")


def references(s):
    """PO / order references in a text: codes with 5+ digits ('PO-TRC-20260921-NEOW',
    '238436', 'KO054417'), lower-case, trailing punctuation dropped."""
    return {t.strip("-_/").lower() for t in _REF.findall(s or "")}


def mentions(text, refs):
    low = (text or "").lower()
    return any(r in low for r in refs)


_OWNERS = None


def owners():
    global _OWNERS
    if _OWNERS is None:
        _OWNERS = {}
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), OWNERS_FILE)
        if os.path.exists(path):
            with open(path, encoding="utf-8-sig") as f:
                for row in csv.DictReader(f, delimiter=";"):
                    _OWNERS[row["domain"].strip().lower()] = row
    return _OWNERS


def owner_of(address):
    """Exact domain first, then the registrable domain (mg.firtal.com -> firtal.com)."""
    o = owners()
    return o.get(domain(address)) or o.get(reg_domain(address)) or {}


_MONTHS = {}
for _i, _names in enumerate([
        ("january", "jan", "januar", "januari"), ("february", "feb", "februar", "februari"),
        ("march", "mar", "marts", "mars", "märz", "maerz"), ("april", "apr"),
        ("may", "maj", "mai"), ("june", "jun", "juni"), ("july", "jul", "juli"),
        ("august", "aug", "augusti"), ("september", "sep", "sept"),
        ("october", "oct", "oktober", "okt"), ("november", "nov"),
        ("december", "dec", "december", "dezember", "dez")], start=1):
    for _n in _names:
        _MONTHS[_n] = _i
_FROM = re.compile(r"^\s*(from|fra|från|von|de|da|od)\s*:", re.I)
_SENT = re.compile(r"^\s*(sent|sendt|skickat|gesendet|date|dato|datum|envoyé|inviato|wysłano)\s*:(.*)$", re.I)
_WROTE = re.compile(r"^\s*(on|den|am|le|il)\s+(.*?)<([^>]+@[^>]+)>.*(wrote|skrev|schrieb|a écrit|ha scritto)", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")


def parse_local_datetime(text):
    """'Friday, October 2, 2026 9:04 AM' / '2. oktober 2026 11:15' / 'den 2 oktober 2026 09:04'
    -> naive local datetime, or None."""
    t = text.lower()
    y = re.search(r"\b(20\d\d)\b", t)
    hm = re.search(r"\b(\d{1,2})[:.](\d{2})(?:[:.]\d{2})?\s*(am|pm)?\b", t[y.end():] if y else t)
    mon, day = None, None
    for w in re.findall(r"[a-zäöüé]+", t):
        if w in _MONTHS:
            mon = _MONTHS[w]
            break
    if mon:
        d = re.search(r"\b(\d{1,2})\.?\s*(?:[a-zäöüé]+\.?\s*)?(?:20\d\d)", t) or re.search(r"\b(\d{1,2})\b", t)
        mon_pos = min((t.find(k) for k in _MONTHS if t.find(k) >= 0 and _MONTHS[k] == mon), default=-1)
        before = re.findall(r"\b(\d{1,2})\b", t[:mon_pos]) if mon_pos >= 0 else []
        after = re.match(r"\s*(\d{1,2})\b", t[mon_pos:].split(None, 1)[1] if mon_pos >= 0 and len(t[mon_pos:].split(None, 1)) > 1 else "")
        day = int(before[-1]) if before else (int(after.group(1)) if after else (int(d.group(1)) if d else None))
    else:                                       # numeric: 02.10.2026 / 2026-10-02 / 10/2/2026
        m = re.search(r"\b(\d{1,2})[./-](\d{1,2})[./-](20\d\d)\b", t) or None
        iso = re.search(r"\b(20\d\d)-(\d{2})-(\d{2})\b", t)
        if iso:
            mon, day = int(iso.group(2)), int(iso.group(3))
        elif m:
            day, mon = int(m.group(1)), int(m.group(2))
    if not (y and mon and day and hm):
        return None
    hh, mm = int(hm.group(1)), int(hm.group(2))
    if hm.group(3) == "pm" and hh < 12:
        hh += 12
    if hm.group(3) == "am" and hh == 12:
        hh = 0
    try:
        return dt.datetime(int(y.group(1)), mon, day, hh, mm)
    except ValueError:
        return None


def local_to_utc(local):
    guess = local.replace(tzinfo=UTC) - dt.timedelta(hours=1)
    return local.replace(tzinfo=UTC) - cph_offset(guess)


def quoted_team_replies(body):
    """[(address, utc_time)] for every quoted header written by a personal True address."""
    lines = (body or "").replace("\r", "").split("\n")
    out = []
    for i, line in enumerate(lines):
        if _FROM.match(line):
            em = _EMAIL.search(line)
            if not em or domain(em.group(0).lower()) not in PERSONAL_DOMAINS:
                continue
            for nxt in lines[i + 1:i + 5]:
                sm = _SENT.match(nxt)
                if sm:
                    when = parse_local_datetime(sm.group(2))
                    if when:
                        out.append((em.group(0).lower(), local_to_utc(when)))
                    break
        wm = _WROTE.match(line)
        if wm and domain(wm.group(3).lower()) in PERSONAL_DOMAINS:
            when = parse_local_datetime(wm.group(2))
            if when:
                out.append((wm.group(3).lower(), local_to_utc(when)))
    return out


def is_personal_team(a):
    return domain(a) in PERSONAL_DOMAINS and a.split("@")[0] not in SHARED_LOCAL_PARTS and a not in TEAM_ADDRESSES


def personal_mailboxes(messages):
    """Personal True addresses that appear as sender/recipient on order@ mail."""
    found = set()
    for m in messages:
        for r in [m.get("from")] + (m.get("toRecipients") or []) + (m.get("ccRecipients") or []):
            a = addr(r)
            if domain(a) in PERSONAL_DOMAINS and a.split("@")[0] not in SHARED_LOCAL_PARTS \
                    and a not in TEAM_ADDRESSES:
                found.add(a)
    return sorted(found)


def is_finance(s):
    s = (s or "").lower()
    return any(w in s for w in FINANCE_WORDS)


def is_non_logistics(s):
    s = (s or "").lower()
    return any(w in s for w in NON_LOGISTICS_WORDS)


def is_team(a):
    return a in TEAM_ADDRESSES or domain(a) in TEAM_DOMAINS


def is_automated(a, subject):
    s = (subject or "").lower()
    return (in_domains(a, AUTOMATED_DOMAINS)
            or any(p in a for p in AUTOMATED_PATTERNS)
            or any(s.startswith(p) for p in AUTO_REPLY_SUBJECTS)
            or any(w in s for w in NO_ANSWER_SUBJECT_WORDS))


def is_order_subject(subject):
    s = f" {(subject or '').lower()} "
    return any(w in s for w in ORDER_WORDS)


def classify(m):
    a = addr(m.get("from"))
    if m.get("isDraft"):
        return "draft"
    if is_team(a):
        return "team"
    if is_automated(a, m.get("subject")):
        return "automated"
    if in_domains(a, PARTNER_DOMAINS):
        return "partner"
    if in_domains(a, NON_CUSTOMER_DOMAINS):
        return "non-customer"
    return "customer"


def reaches(team_msg, customer_addr):
    """Does this team message go to the waiting customer?"""
    rcpt = [addr(r) for r in (team_msg.get("toRecipients") or []) + (team_msg.get("ccRecipients") or [])]
    d = reg_domain(customer_addr)
    for r in rcpt:
        if r == customer_addr:
            return True
        if d and domain(customer_addr) not in FREEMAIL and reg_domain(r) == d:
            return True
    return False


def company_of(address):
    """Customer company: the customer-list name's first word ('Humble - USA' and
    'Humble - CAN' -> humble), else the registrable domain. Free-mail has no company."""
    o = owner_of(address).get("customer", "")
    if o:
        return re.split(r"[\s\-/(]+", o.strip().lower())[0]
    return "" if domain(address) in FREEMAIL else reg_domain(address)


def reaches_company(team_msg, customer_addr):
    comp = company_of(customer_addr)
    for r in recipients(team_msg):
        if r == customer_addr or (comp and not is_team(r) and company_of(r) == comp):
            return True
    return False


def recipients(m):
    return {addr(r) for r in (m.get("toRecipients") or []) + (m.get("ccRecipients") or [])}


# ----------------------------- core ---------------------------------------
def build_waits(messages, now_utc, body_of=None):
    msgs = []
    for m in messages:
        c = classify(m)
        if c == "draft":
            continue
        t = parse_ts(m.get("sentDateTime") if c == "team" and m.get("sentDateTime")
                     else m["receivedDateTime"])
        msgs.append({**m, "_class": c, "_from": addr(m.get("from")), "_t": t})

    # de-duplicate (same message in Inbox + a copy elsewhere)
    seen, uniq = set(), []
    for m in sorted(msgs, key=lambda x: x["_t"]):
        k = (m["_from"], m["_t"].isoformat(), m.get("subject"))   # same mail, any folder/thread
        if k not in seen:
            seen.add(k)
            uniq.append(m)

    convs = {}
    for m in uniq:
        convs.setdefault(m.get("conversationId") or m["id"], []).append(m)

    waits = []
    for cid, thread in convs.items():
        thread.sort(key=lambda x: x["_t"])
        relevant = [m for m in thread if m["_class"] in ("team", "customer")]
        if not relevant:
            continue
        initiated_by_customer = relevant[0]["_class"] == "customer"
        open_waits = {}      # customer address -> wait dict
        first_done = False
        for m in relevant:
            if m["_class"] == "customer":
                w = open_waits.get(m["_from"])
                if w:
                    w["chasers"] += 1
                    continue
                open_waits[m["_from"]] = {
                    "conversation_id": cid, "subject": m.get("subject", ""),
                    "customer": m["_from"], "customer_domain": domain(m["_from"]),
                    "received": m["_t"], "chasers": 0,
                    "first_in_thread": not first_done and initiated_by_customer,
                    "customer_initiated_thread": initiated_by_customer,
                    "is_order": is_order_subject(relevant[0].get("subject")),
                    "new_topic": not is_reply_subject(m.get("subject")),
                    "to_shared": bool(recipients(m) & SHARED_ADDRESSES),
                    "finance": is_finance(m.get("subject")),
                    "non_logistics": is_non_logistics(m.get("subject")),
                    "owner": owner_of(m["_from"]).get("responsible", ""),
                    "owner_customer": owner_of(m["_from"]).get("customer", ""),
                    "no_reply_needed": owner_of(m["_from"]).get("confirm_required", "yes").strip().lower() == "no",
                }
                first_done = True
            else:  # team message: closes every wait it reaches
                for ca in list(open_waits):
                    if reaches(m, ca):
                        w = open_waits.pop(ca)
                        w.update(replied=m["_t"], replied_by=m["_from"], via="thread")
                        waits.append(w)
                first_done = True
        for w in open_waits.values():
            w.update(replied=None, replied_by="", via="")
            waits.append(w)

    # fallback: reply sent in another thread (broken threading, new mail)
    team = [m for m in uniq if m["_class"] == "team"]
    for w in waits:
        if w["replied"] is not None:
            continue
        ns, nums = norm_subject(w["subject"]), order_numbers(w["subject"])
        limit = w["received"] + dt.timedelta(days=FALLBACK_DAYS)
        for m in team:                       # uniq is time-sorted
            if m["_t"] <= w["received"]:
                continue
            if m["_t"] > limit:
                break
            if not reaches(m, w["customer"]):
                continue
            if (ns and norm_subject(m.get("subject")) == ns) or (nums & order_numbers(m.get("subject"))):
                w.update(replied=m["_t"], replied_by=m["_from"], via="other thread")
                break

    # new thread to the same company: PO/order reference mentioned, or sent within a business day
    for w in waits:
        if w["replied"] is not None:
            continue
        refs = references(w["subject"])
        limit = w["received"] + dt.timedelta(days=FALLBACK_DAYS)
        loose = None
        for m in team:
            if m["_t"] <= w["received"]:
                continue
            if m["_t"] > limit:
                break
            if m.get("conversationId") == w["conversation_id"] or not reaches_company(m, w["customer"]):
                continue
            if refs and (mentions(m.get("subject"), refs) or mentions(m.get("bodyPreview"), refs)):
                w.update(replied=m["_t"], replied_by=m["_from"], via="new thread (reference)")
                break
            if loose is None and business_hours(w["received"], m["_t"]) <= SAME_COMPANY_BH:
                loose = m
        if w["replied"] is None and loose is not None:
            w.update(replied=loose["_t"], replied_by=loose["_from"], via="new thread (same company)")

    # reply sent from a personal mailbox without order@ in copy: the customer's
    # follow-up shows it (addressed to that person and/or quoting their reply)
    if INFER_FROM_FOLLOWUPS:
        reads = [0]
        customers = [m for m in uniq if m["_class"] == "customer"]
        for w in waits:
            w.setdefault("outside", False)
            if w["replied"] is not None:
                continue
            ns, cd = norm_subject(w["subject"]), reg_domain(w["customer"])
            limit = w["received"] + dt.timedelta(days=FALLBACK_DAYS)
            for m in customers:
                if m["_t"] <= w["received"] or m["_t"] > limit or reg_domain(m["_from"]) != cd:
                    continue
                if m.get("conversationId") != w["conversation_id"] and norm_subject(m.get("subject")) != ns:
                    continue
                person = next((a for a in recipients(m) if is_personal_team(a)), None)
                when = None
                if body_of and reads[0] < MAX_BODY_READS:
                    reads[0] += 1
                    try:
                        quoted = [(a, t) for a, t in quoted_team_replies(body_of(m["id"]))
                                  if w["received"] < t <= m["_t"] and is_personal_team(a)]
                    except Exception:
                        quoted = []
                    if quoted:
                        person, when = min(quoted, key=lambda x: x[1])
                if person:
                    w.update(replied=when or m["_t"], replied_by=person, outside=True,
                             via="without order@ in copy" + ("" if when else " (time = customer follow-up)"))
                    break

    rows = []
    for w in waits:
        end = w["replied"] or now_utc
        rows.append({
            "subject": w["subject"], "customer": w["customer"],
            "customer_domain": w["customer_domain"], "is_order": w["is_order"],
            "first_in_thread": w["first_in_thread"],
            "customer_initiated_thread": w["customer_initiated_thread"],
            "received_local": to_local(w["received"]).strftime("%Y-%m-%d %H:%M"),
            "replied_local": to_local(w["replied"]).strftime("%Y-%m-%d %H:%M") if w["replied"] else "",
            "replied_by": w["replied_by"], "replied_via": w["via"],
            "without_order_copy": w.get("outside", False),
            "answered": w["replied"] is not None,
            "new_topic": w["new_topic"], "to_shared_address": w["to_shared"],
            "finance": w["finance"], "non_logistics": w["non_logistics"],
            "owner": w["owner"], "owner_customer": w["owner_customer"],
            "no_reply_needed": w["no_reply_needed"],
            "in_kpi": (w["first_in_thread"] and w["new_topic"] and w["to_shared"]
                       and not w["finance"] and not w["non_logistics"] and not w["no_reply_needed"]),
            "business_hours": round(business_hours(w["received"], end), 2),
            "calendar_hours": round((end - w["received"]).total_seconds() / 3600, 2),
            "chasers_before_reply": w["chasers"],
            "conversation_id": w["conversation_id"],
        })
    rows.sort(key=lambda r: r["received_local"])
    return rows, uniq


# Answer-time buckets (business hours, BUSINESS_END-BUSINESS_START per day)
BUCKETS = (("1 day", 1), ("2 days", 2), ("3 days", 3))   # stacked bar segments


def bucket(r):
    if not r["answered"]:
        return "no reply"
    day = BUSINESS_END - BUSINESS_START
    for name, days in BUCKETS:
        if r["business_hours"] <= days * day:
            return f"within {name}"
    return "over 3 days"


BUCKET_ORDER = [f"within {n}" for n, _ in BUCKETS] + ["over 3 days", "no reply"]


def distribution(rows, keyfn):
    """Per period: count in each bucket, share of ALL waits (no-reply included)."""
    agg = {}
    for r in rows:
        k = keyfn(r)
        a = agg.setdefault(k, {b: 0 for b in BUCKET_ORDER})
        a[bucket(r)] += 1
    out = []
    for k in sorted(agg):
        a, n = agg[k], sum(agg[k].values())
        row = {"period": k, "waits": n}
        for b in BUCKET_ORDER:
            row[b] = a[b]
            row[b + " %"] = round(100 * a[b] / n, 1)
        out.append(row)
    return out


def iso_week_of(local_str):
    y, w, _ = dt.date.fromisoformat(local_str[:10]).isocalendar()
    return f"{y}-W{w:02d}"


def median(xs):
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return None
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def summarise(rows):
    def block(title, rs):
        ans = [r for r in rs if r["answered"]]
        bh = [r["business_hours"] for r in ans]
        within8 = sum(1 for x in bh if x <= 8)
        within4 = sum(1 for x in bh if x <= 4)
        chased = sum(1 for r in rs if r["chasers_before_reply"] > 0)
        med = median(bh)
        p90 = sorted(bh)[int(0.9 * (len(bh) - 1))] if bh else None
        return [
            f"{title}: {len(rs)} waits, {len(ans)} answered, {len(rs) - len(ans)} without reply",
            f"  median first response:   {med:.1f} business hours" if med is not None else "  median: n/a",
            f"  90th percentile:         {p90:.1f} business hours" if p90 is not None else "  p90: n/a",
            f"  answered within 4 bh:    {100 * within4 / len(ans):.0f}%" if ans else "",
            f"  answered within 1 bday:  {100 * within8 / len(ans):.0f}%  (8 business hours)" if ans else "",
            f"  customer had to chase:   {chased}",
        ]
    kpi = [r for r in rows if r["in_kpi"]]
    orders = [r for r in kpi if r["is_order"]]
    first = [r for r in rows if r["first_in_thread"]]
    excl = [
        ("opener is a reply (thread began before window / broken thread)",
         sum(1 for r in first if not r["new_topic"])),
        ("sent to a personal mailbox, not order@/wholesale@",
         sum(1 for r in first if r["new_topic"] and not r["to_shared_address"])),
        ("finance topic", sum(1 for r in first if r["new_topic"] and r["to_shared_address"] and r["finance"])),
        ("QA / regulatory / master-data topic", sum(1 for r in first if r["new_topic"] and r["to_shared_address"]
                                                  and not r["finance"] and r["non_logistics"])),
        ("customer marked 'no confirmation needed'", sum(1 for r in first if r["new_topic"] and r["to_shared_address"]
                                                        and not r["finance"] and not r["non_logistics"]
                                                        and r["no_reply_needed"])),
    ]
    via = sum(1 for r in kpi if r["replied_via"] == "other thread")
    via_ref = sum(1 for r in kpi if r["replied_via"] == "new thread (reference)")
    via_comp = sum(1 for r in kpi if r["replied_via"] == "new thread (same company)")
    outside = [r for r in kpi if r.get("without_order_copy")]
    by_person = {}
    for r in outside:
        p = r["replied_by"].split("@")[0].capitalize()
        by_person[p] = by_person.get(p, 0) + 1
    lines = (block("FIRST RESPONSE - headline (new customer topic to order@/wholesale@)", kpi)
             + [f"  replies found in another thread: {via}",
                f"  replies in a new thread: {via_ref} by PO/order reference, {via_comp} by same company within 1 business day",
                f"  answered WITHOUT order@ in copy: {len(outside)} of {sum(1 for r in kpi if r['answered'])}"
                + (" (" + ", ".join(f"{k} {v}" for k, v in sorted(by_person.items(), key=lambda x: -x[1])) + ")"
                   if by_person else ""), ""]
             + block("  of which order threads", orders) + [""]
             + block("ALL waits on order@/wholesale@ mail (incl. follow-ups)",
                     [r for r in rows if r["to_shared_address"] and not r["finance"]
                      and not r["non_logistics"]]) + [""]
             + ["First-in-thread waits left out of the headline:"]
             + [f"  {k}: {v}" for k, v in excl])
    day = BUSINESS_END - BUSINESS_START
    dist = distribution(kpi, lambda r: "all")
    if dist:
        d0 = dist[0]
        lines += ["", f"Headline waits by answer time (share of ALL {d0['waits']}, no-reply included):"]
        cum = 0.0
        for b in BUCKET_ORDER:
            if b != "no reply":
                cum += d0[b + " %"]
            lines.append(f"  {b:<16} {d0[b]:>4}  {d0[b + ' %']:>5.1f}%"
                         + (f"   cumulative {cum:5.1f}%" if b != "no reply" else ""))
        ans = sorted((r for r in kpi if r["answered"]), key=lambda r: -r["business_hours"])
        lines += ["", "Slowest answered (business days):"]
        lines += [f"  {r['business_hours'] / day:5.1f}  {r['received_local']}  {r['customer']}  {r['subject'][:50]}"
                  for r in ans[:5]]
    lines += ["", "Note: 'without reply' includes messages that need none (e.g. 'thank you'),",
              "so read it as an upper bound; the response-time figures use answered waits only."]
    return "\n".join(l for l in lines if l is not None)


def write_csv(path, rows):
    if not rows:
        open(path, "w").close()
        return
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), delimiter=";")
        w.writeheader()
        w.writerows(rows)


def run(graph, now_utc=None):
    now_utc = now_utc or dt.datetime.now(UTC)
    since = f"{DATE_FROM}T00:00:00Z"
    until = (f"{DATE_TO}T23:59:59Z" if DATE_TO else now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"))
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print(f"Reading {MAILBOX} {since} .. {until}", flush=True)
    raw = graph.messages(since, until)
    boxes = personal_mailboxes(raw) if TEAM_REPLY_MAILBOXES == "auto" else list(TEAM_REPLY_MAILBOXES)
    read_boxes, skipped = [], []
    for mbx in boxes:                          # any reply counts, from any True mailbox
        try:
            raw += graph.messages(since, until, mailbox=mbx, folder="sentitems")
            read_boxes.append(mbx)
        except RuntimeError as e:              # alias / group / no licence: not a mailbox
            skipped.append(mbx)
            print(f"  skipped {mbx}: {str(e)[:120]}", flush=True)
    body_of = getattr(graph, "body", None) if INFER_FROM_FOLLOWUPS else None
    rows, msgs = build_waits(raw, now_utc, body_of=body_of)

    write_csv(os.path.join(OUTPUT_DIR, "response_waits.csv"), rows)
    write_csv(os.path.join(OUTPUT_DIR, "response_distribution_weekly.csv"),
              distribution([r for r in rows if r["in_kpi"]], lambda r: iso_week_of(r["received_local"])))
    write_csv(os.path.join(OUTPUT_DIR, "response_unanswered_orders.csv"),
              [r for r in rows if r["in_kpi"] and r["is_order"] and not r["answered"]])
    write_csv(os.path.join(OUTPUT_DIR, "response_messages.csv"), [{
        "time_local": to_local(m["_t"]).strftime("%Y-%m-%d %H:%M"), "class": m["_class"],
        "from": m["_from"], "subject": m.get("subject", ""),
        "to_cc": ",".join(addr(r) for r in (m.get("toRecipients") or []) + (m.get("ccRecipients") or [])),
        "conversation_id": m.get("conversationId", "")} for m in msgs])
    senders = {}
    for m in msgs:
        if m["_class"] in ("customer", "automated", "partner", "non-customer"):
            s = senders.setdefault(m["_from"], {"sender": m["_from"], "class": m["_class"], "messages": 0})
            s["messages"] += 1
    write_csv(os.path.join(OUTPUT_DIR, "response_senders.csv"),
              sorted(senders.values(), key=lambda s: -s["messages"]))

    counts = {}
    for m in msgs:
        counts[m["_class"]] = counts.get(m["_class"], 0) + 1
    summary = (f"Replies counted from: {MAILBOX} + {len(read_boxes)} personal mailboxes"
               + (f" (skipped {len(skipped)}: {', '.join(skipped)})" if skipped else "") + "\n"
               + f"Response time  {MAILBOX}  {since[:10]} .. {until[:10]}  "
               f"(business hours {BUSINESS_START}-{BUSINESS_END} Copenhagen)\n"
               f"Messages: {len(msgs)}  " + "  ".join(f"{k} {v}" for k, v in sorted(counts.items()))
               + "\n\n" + summarise(rows))
    with open(os.path.join(OUTPUT_DIR, "response_summary.txt"), "w", encoding="utf-8") as f:
        f.write(summary)
    print("\n" + summary)
    return rows, msgs


if __name__ == "__main__":
    if "PASTE_" in TENANT_ID + CLIENT_ID + CLIENT_SECRET:
        sys.exit("Paste TENANT_ID, CLIENT_ID and CLIENT_SECRET at the top of the script.")
    run(Graph())
