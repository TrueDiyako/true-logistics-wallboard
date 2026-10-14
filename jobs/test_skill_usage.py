"""AI adoption (dashboard 1) - real log file names from the Claude logs folder, 13 Oct 2026."""
import datetime as dt, sys
import skill_usage as su

def F(name, owner, created="2026-10-13T10:18:59Z"):
    return {"name": name, "createdTime": created, "owners": [{"emailAddress": owner, "displayName": owner.split("@")[0]}]}

FILES = [
    F("Skill usage log - process-weclapp-order - 2026-10-13 1218 - 4668.txt", "frederikke.truegum@gmail.com"),
    F("Skill usage log - process-weclapp-order - 2026-10-13 1140 - 4706.txt", "frederikke.truegum@gmail.com"),
    F("Skill usage log - process-weclapp-order - 2026-10-13 1122 - 4680.txt", "frederikke.truegum@gmail.com"),
    F("Skill usage log - process-weclapp-order - 2026-10-13 1346 - 4682.txt", "frederikke.truegum@gmail.com"),
    F("Skill usage log - tracelink-order-closeout - 2026-10-12 0915 - 1022786.txt", "valentina.truecompany@gmail.com"),
    F("Skill usage log - internal-purchase - 2026-10-20 1001 - 2069180098.txt", "andreas@truecompany.com"),   # next week
    F("Skill usage log - internal-purchase - 2026-10-02 1001 - 2069100001.txt", "andreas@truecompany.com"),   # before start
    F("Skill usage log - some-other-skill - 2026-10-13 1001 - x.txt", "andreas@truecompany.com"),           # not tracked
    F("notes.txt", "andreas@truecompany.com"),
]

class FakeDrive:
    connected = True
    def __init__(self, files): self.files, self.calls = files, []
    def list_folder(self, folder, since): self.calls.append((folder, since)); return self.files

def test_weekly_counts_from_week_42():
    out = su.build(FakeDrive(FILES), dt.date(2026, 10, 21))
    assert out["connected"] and out["tracking_from"] == "2026-10-05"
    assert out["weekly"] == [{"week": "2026-W41", "tracelink-order-closeout": 0, "internal-purchase": 0, "process-weclapp-order": 0},
                             {"week": "2026-W42", "tracelink-order-closeout": 1, "internal-purchase": 0, "process-weclapp-order": 4},
                             {"week": "2026-W43", "tracelink-order-closeout": 0, "internal-purchase": 1, "process-weclapp-order": 0}]
    assert out["this_week"] == {"tracelink-order-closeout": 0, "internal-purchase": 1, "process-weclapp-order": 0}
    assert out["people_this_week"] == [("Andreas", 1)] and out["total"] == 6

def test_today_and_people():
    out = su.build(FakeDrive(FILES), dt.date(2026, 10, 13))
    assert out["this_week"]["process-weclapp-order"] == 4 and out["total"] == 5     # the 20 Oct file is in the future
    assert out["people_this_week"] == [("Frederikke", 4), ("Valentina", 1)]

def test_not_connected_without_secret():
    out = su.build(su.Drive(sa_json=""), dt.date(2026, 10, 13))
    assert out == {"connected": False, "skills": su.SKILLS}

def test_person_names():
    assert su.person("frederikke.truegum@gmail.com") == "Frederikke"
    assert su.person("someone.new@truecompany.com") == "Someone"
    assert su.person("fred6341@gmail.com") == "Fred"

if __name__ == "__main__":
    import traceback
    fails = 0
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            try: f(); print("PASS", n)
            except Exception as e: fails += 1; print("FAIL", n, repr(e)); traceback.print_exc()
    sys.exit(fails)
