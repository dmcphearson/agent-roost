"""roost --demo: made-up Sessions, projects, and notes; nothing real is read or changed."""
import atexit, os, shutil, tempfile, time
from datetime import datetime
from pathlib import Path

from roost import patch
from roost.core import config
from roost.core.config import HOME
from roost.core import formatting
from roost.core.formatting import short, slug
from roost.core import projectdata
from roost.core import notedata


# ---------- demo ----------

DEMO = [
    # name, state, kind, pinned, open, minutes ago, tokens, folder, summary, files
    ("Quarterly access review", "busy", "background", True, True, 1, 88_000, "it-ops",
     "Pulled the current user list for all 17 apps and matched it against the HR roster. Three accounts belong "
     "to people who left last month; drafted removal tickets for each and flagged one shared admin login.",
     ["access-review.csv", "removals.md"]),
    ("Laptop refresh rollout", "waiting", "interactive", False, True, 4, 41_000, "it-ops/devices",
     "The enrollment profile is ready. Before I push it to the pilot group, which five people should be first?",
     ["refresh-plan.md"]),
    ("Vendor security questionnaire", "idle", "background", True, False, 12, 132_000, "security/vendors",
     "Answered 46 of 52 questions from the existing policy set. The remaining six need a decision on data "
     "retention for backups, so I listed the options with what each one commits us to.",
     ["questionnaire.xlsx", "open-questions.md"]),
    ("Onboarding checklist automation", "idle", "background", True, False, 35, 97_000, "automation",
     "The workflow now creates accounts, assigns groups, and posts a welcome message. Tested it end to end with a "
     "fake new hire; all steps passed and the cleanup step removed the test account.",
     ["onboarding.json", "README.md"]),
    ("Backup restore drill", "idle", "background", True, False, 58, 156_000, "infra/backups",
     "Restored last night's database snapshot into a scratch account in 38 minutes, inside the 12-hour target. "
     "Wrote up the steps and the two places where the runbook was out of date.",
     ["restore-drill.md", "runbook.md"]),
    ("Helpdesk ticket triage", "idle", "background", True, False, 80, 64_000, "helpdesk",
     "Sorted 31 new tickets: 12 password resets, 9 software requests, 6 hardware, 4 that need a person. "
     "Drafted replies for the resets and requests.",
     ["triage-notes.md"]),
    ("SSO app onboarding", "idle", "background", False, False, 22, 52_000, "identity",
     "Set up single sign-on for the new design tool and mapped the three access groups. Waiting on the vendor "
     "to enable SCIM before provisioning can be turned on.",
     ["sso-setup.md"]),
    ("Policy doc cleanup", "idle", "background", False, False, 47, 73_000, "security/policies",
     "Merged the two acceptable use drafts, removed duplicated sections, and updated the review dates. "
     "The diff is ready for a final read.",
     ["acceptable-use.md"]),
    ("Network diagram refresh", "closed", None, False, False, 95, 118_000, "infra/network",
     "Redrew the office network with the new firewall and guest Wi-Fi split, and exported it for the wiki.",
     ["network.drawio", "network.png"]),
    ("License usage report", "closed", None, False, False, 160, 45_000, "finance/it",
     "Found 14 unused seats across four tools, worth about $3,100 a year. Listed who to check with before "
     "removing each one.",
     ["license-report.md"]),
    ("Printer driver rollout", "closed", None, False, False, 60 * 24 * 14, 39_000, "it-ops/devices",
     "Packaged the new driver and deployed it to the office Macs. Two machines needed a restart to pick it up.",
     ["printer.pkg"]),
    ("Incident report draft", "closed", None, False, False, 60 * 24 * 23, 210_000, "security/incidents",
     "Drafted the timeline, impact, and follow-up actions for last week's phishing report. All links in the "
     "email were blocked before anyone signed in.",
     ["incident-report.md", "timeline.md"]),
    ("MFA enforcement plan", "closed", None, False, False, 60 * 24 * 4, 86_000, "identity",
     "Planned a three-week rollout: admins first, then engineering, then everyone, with a help article and "
     "office hours for each wave.",
     ["mfa-plan.md"]),
    ("Wi-Fi certificate renewal", "blocked", "interactive", False, True, 2, 23_000, "infra/network",
     "The RADIUS certificate expires on Friday. Everything is staged, but the renewal needs an admin to approve "
     "it in the console. Can you approve it, or tell me who should?",
     ["radius-renewal.md"]),
    ("Asset inventory sync", "busy", "background", True, False, 0, 61_000, "it-ops/assets",
     "Matching 214 devices from the MDM against the asset sheet. 198 match so far; the rest are mostly "
     "renamed laptops, which I'm pairing by serial number.",
     ["assets.csv", "unmatched.md"]),
    ("Firewall rule audit", "idle", "background", True, False, 60 * 24 * 3, 184_000, "infra/network",
     "Reviewed all 312 rules. 41 haven't matched traffic in 90 days and 6 allow any source; the clean-up "
     "list is grouped by owner so each team can confirm theirs.",
     ["rule-audit.md", "unused-rules.csv"]),
    ("Shared drive permissions", "closed", None, False, False, 60 * 24 * 19, 58_000, "security/data",
     "Found 23 folders shared with anyone who has the link. Nine hold contracts or HR files; I drafted the "
     "list of owners to ask before changing them.",
     ["shared-links.csv"]),
    ("Password manager rollout", "idle", "background", True, False, 9, 77_000, "identity",
     "Wave one is live for 18 people. Two asked how to import from their browser, so I added a short how-to "
     "to the help article.",
     ["rollout.md", "how-to-import.md"]),
    ("Conference room displays", "closed", None, False, False, 60 * 24 * 2, 33_000, "it-ops/rooms",
     "Updated the firmware on all four room displays and turned on automatic updates for the overnight window.",
     ["rooms.md"]),
    ("Vendor renewal calendar", "closed", None, False, False, 60 * 24 * 21, 52_000, "finance/it",
     "Built a calendar of the 37 contract renewals with notice dates. Six need a decision within 60 days.",
     ["renewals.csv"]),
    ("Endpoint encryption check", "closed", None, False, False, 60 * 24 * 27, 71_000, "security/devices",
     "Every managed Mac reports disk encryption on. Two Windows laptops were missing recovery keys; both are "
     "escrowed now.",
     ["encryption-report.md"]),
    ("Access review (earlier run)", "closed", None, False, False, 60 * 24 * 6, 140_000, "it-ops",
     "Matched the first 20 apps against the HR roster before the session ran out of context.",
     ["access-review.csv"]),
    ("Backup alert tuning", "closed", None, False, False, 60 * 24 * 1, 44_000, "infra/backups",
     "Raised the warning threshold on the nightly job so a slow night no longer pages anyone, and kept the "
     "failure alert as it was.",
     ["alerts.md"]),
]
# Per-Session extras: a Claude Desktop Session is never cleaned up
DEMO_EXTRA = {"Shared drive permissions": {"entrypoint": "claude-desktop"}}


DEMO_PROJECTS = [
    # id, status, summary, DEMO sessions it lists (by index), hours since its last Handoff
    ("2026-10-access-review-q4", "active", "Quarterly access review for the 17 apps in scope, with removal tickets for leavers.", [0, 21], 2),
    ("2026-09-vendor-security-reviews", "active", "Security questionnaires and reviews for the three vendors up for renewal this quarter.", [2, 19], 30),
    ("2026-09-backup-restore-drill", "active", "Prove last night's backups restore inside the 12-hour target, and fix the runbook.", [4, 22], 5),
    ("2026-10-mfa-rollout", "planning", "Roll out MFA in three waves: admins, engineering, then everyone.", [], None),
    ("2026-08-network-refresh", "closed", "New firewall and guest Wi-Fi split for the office.", [8], 24 * 20),
    ("2026-10-laptop-refresh", "active", "Replace the six laptops past four years old, with a pilot group first.", [1, 10], 4),
    ("2026-10-network-hardening", "active", "Renew the Wi-Fi certificate and clean up firewall rules nobody uses.", [13, 15], 1),
    ("2026-09-password-manager", "active", "Company password manager for everyone, in two waves, with a how-to for importing.", [17], 9),
    ("2026-10-asset-inventory", "active", "One asset list that matches the MDM, with owners and warranty dates.", [14], None),
    ("2026-09-data-sharing-cleanup", "on hold", "Close public links on shared drives once each folder's owner confirms.", [16], 24 * 19),
    ("2026-07-endpoint-encryption", "closed", "Every company laptop encrypted, with its recovery key escrowed.", [20], 24 * 26),
]
DEMO_LABELS = {  # each README's heading: the name the screens show
    "2026-10-access-review-q4": "Q4 access review", "2026-09-vendor-security-reviews": "Vendor reviews",
    "2026-09-backup-restore-drill": "Backup restore drill", "2026-10-mfa-rollout": "MFA rollout",
    "2026-08-network-refresh": "Office network refresh", "2026-10-laptop-refresh": "Laptop refresh",
    "2026-10-network-hardening": "Network hardening", "2026-09-password-manager": "Password manager",
    "2026-10-asset-inventory": "Asset inventory", "2026-09-data-sharing-cleanup": "Shared link cleanup",
    "2026-07-endpoint-encryption": "Laptop encryption"}
DEMO_WRAPPED = {10, 16, 19, 20, 21, 22}  # closed Sessions whose project's newest Handoff covers their last message
DEMO_README = {  # a README's Current state and Next, and its newest Handoff's first lines
    "2026-10-access-review-q4": (
        "12 of 17 apps reviewed. Removals filed for 9 accounts; 3 owners still owe sign-off on finance tools.",
        "Chase the 3 finance sign-offs, then export the evidence pack for the auditors.",
        ["Reviewed the CRM, wiki, and payroll exports.", "Filed removal tickets for 9 stale accounts.",
         "Flagged 2 shared logins to replace with SSO."]),
    "2026-09-backup-restore-drill": (
        "File server restored in 4h 10m, inside the 12-hour target. The database restore failed on a missing key.",
        "Rotate the backup encryption key into the vault, then rerun the database restore.",
        ["Restored the file server to a scratch VM.", "Timed each step against the runbook."]),
}
DEMO_NOT_ADOPTED = {"2026-09-data-sharing-cleanup"}  # no handoffs/ yet: its actions offer Adopt it
DEMO_CATEGORIES = {"2026-10-access-review-q4": "access", "2026-09-vendor-security-reviews": "vendors",
                   "2026-09-backup-restore-drill": "hardening", "2026-08-network-refresh": "infrastructure",
                   "2026-10-mfa-rollout": "access", "2026-10-laptop-refresh": "infrastructure",
                   "2026-10-network-hardening": "hardening", "2026-09-password-manager": "access",
                   "2026-10-asset-inventory": "infrastructure", "2026-09-data-sharing-cleanup": "hardening",
                   "2026-07-endpoint-encryption": "hardening"}
DEMO_CATEGORY_LIST = [("access", "accounts, permissions, and reviews"),
                      ("hardening", "controls, backups, and recovery drills"),
                      ("infrastructure", "laptops, networks, and shared tools"),
                      ("vendors", "renewals and security questionnaires"),
                      ("incidents", "what happened and what changed")]
DEMO_TAGS = {"2026-10-access-review-q4": ["access", "quarterly"], "2026-09-vendor-security-reviews": ["vendors"],
             "2026-09-backup-restore-drill": ["backups", "dr"], "2026-08-network-refresh": ["network"],
             "2026-10-mfa-rollout": ["mfa"], "2026-10-laptop-refresh": ["devices", "budget"],
             "2026-10-network-hardening": ["network", "certificates"], "2026-09-password-manager": ["passwords"],
             "2026-10-asset-inventory": ["devices", "mdm"], "2026-09-data-sharing-cleanup": ["data"],
             "2026-07-endpoint-encryption": ["devices"]}
DEMO_LINKS = {}  # Session id -> project id, as the a key and s set them in the demo
DEMO_RECAPS = [("printer-driver-rollout", "Two office Macs needed a restart to pick up the new printer driver.", 26),
               ("conference-room-displays", "All four room displays are on current firmware and update overnight.", 47),
               ("vendor-renewal-calendar", "37 renewals on one calendar; six need a decision within 60 days.", 24 * 20)]
DEMO_NOTES = [  # title, project or None, tags, body, hours since the last edit
    ("Printer driver idea", None, ["devices"], "Push the driver from the MDM instead of by hand.\n\n- Try it on two Macs first\n"
     "- Check whether the older models need their own package", 3),
    ("Laptop refresh budget", "2026-10-laptop-refresh", ["budget", "devices"],
     "Six laptops are past four years old.\n\n| Model | Count | Each |\n|---|---|---|\n| 14-inch | 4 | $1,899 |\n"
     "| 16-inch | 2 | $2,499 |\n\nAbout **$12,600** in all. Ask finance which quarter it lands in.", 50),
    ("Questions for the access review", "2026-10-access-review-q4", ["access"], "- Who owns the shared admin accounts?\n"
     "- Do contractors keep access between engagements?\n- Should read-only accounts count?", 20),
    ("New hire week one", None, ["onboarding", "checklist"],
     "## Before day one\n\n- [x] Laptop enrolled and shipped\n- [x] Accounts created\n- [ ] Welcome message scheduled\n\n"
     "## Day one\n\n- [ ] Walk through the password manager\n- [ ] Turn on MFA together\n\n"
     "> Keep it to an hour. The rest can wait until week two.", 6),
    ("Firewall clean-up order", "2026-10-network-hardening", ["network"],
     "1. Rules that allow any source\n2. Rules with no traffic in 90 days\n3. Duplicates\n\n"
     "Export before each batch:\n\n```\nfw export --format csv > rules-$(date +%F).csv\n```", 30),
    ("Ideas for next quarter", None, ["backlog"],
     "- A self-serve page for software requests\n- Retire the old VPN once everyone is on the new one\n"
     "- Quarterly *lunch and learn* on phishing", 24 * 4),
    ("Backup drill notes", "2026-09-backup-restore-drill", ["backups", "dr"],
     "Restore took **38 minutes**. The runbook still names the old bucket, and step 6 is missing the "
     "`--region` flag.", 8),
    ("Vendor call prep", None, ["vendors"],
     "### Ask about\n\n- Where backups are stored, and for how long\n- SSO on the standard plan\n"
     "- Their last penetration test date", 75),
]


def notedata_home():
    """Where roost keeps notes and recaps for real, so the demo's temp folders are shown as those."""
    return config.ROOST_HOME


def demo_projects(sessions):
    now, out = time.time(), []
    ids = [s["id"] for s in demo_sessions()]
    last_of = {s["id"]: s["last"] for s in sessions}
    for pid, status, summary, linked, hours in DEMO_PROJECTS:
        sids = [ids[i] for i in linked if DEMO_LINKS.get(ids[i], pid) == pid]
        sids += [sid for sid, q in DEMO_LINKS.items() if q == pid and sid not in sids]
        times = [s["last"] for s in sessions if s["id"] in sids] + ([now - hours * 3600] if hours is not None else [])
        out.append({"id": pid, "label": DEMO_LABELS.get(pid, pid), "path": f"{HOME}/.roost/projects/{pid}",
                    "summary": summary, "status": status,
                    "category": DEMO_CATEGORIES.get(pid, ""), "tags": DEMO_TAGS.get(pid, []),
                    "sessions": sids, "last": max(times) if times else None, "closed": status == "closed",
                    "handed": now - hours * 3600 if hours is not None else None,
                    "readme": True, "adopted": pid not in DEMO_NOT_ADOPTED,
                    "covered": {ids[i]: last_of.get(ids[i], 0) + 60 for i in linked if i in DEMO_WRAPPED},
                    "handoffs": [(now - hours * 3600, next((s["name"] for s in sessions if s["id"] in sids), "Handoff"),
                                  f"{HOME}/.roost/projects/{pid}/handoffs/demo.md")]
                    if hours is not None else [],
                    "notes": [], "repo": "", "subs": [],
                    **dict(zip(("state", "next", "handoff_lines"), DEMO_README.get(pid, ("", "", [])))) })
    return out


def demo_sessions():
    now, out = time.time(), []
    for i, (name, state, kind, pinned, is_open, mins, tokens, folder, summary, files) in enumerate(DEMO):
        sid = f"{0xa1b2c300 + i * 7919:08x}-demo-0000-0000-000000000000"
        out.append({"id": sid, "ids": [sid], "name": name, "summary": summary, "tokens": tokens,
                    "files": [f"{HOME}/projects/{folder}/{f}" for f in files],
                    "cwd": f"{HOME}/projects/{folder}", "last": now - mins * 60, "state": state,
                    "started": now - mins * 60 - (i % 4 + 1) * 5400, "ttl": 3600,
                    "kind": kind if kind != "interactive" or is_open else "background",
                    "attach": sid[:8] if kind == "background" else None,
                    "pinned": pinned, "attached": is_open and kind == "background", "partial": None,
                    **DEMO_EXTRA.get(name, {})})
    return out


def use_demo():
    """Swap every function that reads or changes real sessions for in-memory stand-ins."""
    sessions = demo_sessions()
    find = lambda job: next((s for s in sessions if s.get("attach") == job), None)

    def set_pin(job, pinned):
        find(job)["pinned"] = pinned

    def revive(s):
        s.update(state="idle", kind="background", attach=s["id"][:8], pinned=True)

    def stop(job):
        s = find(job)
        s.update(state="closed", kind=None, attach=None, pinned=False, attached=False)

    def open_win(s, term):
        if s["kind"] == "background":
            s["attached"] = True
        return f"Opened {s['name']} (demo)"

    def delete(s):
        sessions.remove(s)

    def rename(s, name):
        s["name"] = " ".join(name.split())

    made = []  # projects created in the demo, kept in memory only

    def new_project(pid, summary="", label=None):
        if not pid.strip():
            raise ValueError("give it an id")
        made.insert(0, {"id": pid.strip(), "label": " ".join((label or "").split()) or pid.strip(), "path": f"{HOME}/.roost/projects/{pid.strip()}", "summary": " ".join(summary.split()),
                        "status": "active", "category": "", "tags": [], "sessions": [], "last": None,
                        "closed": False, "readme": True})
        return Path(made[0]["path"])

    now = time.time()
    real_set_tags = projectdata.set_tags

    def demo_set_tags(path, tags):  # a demo project's tags are kept in memory; a note's are in its file
        pid = Path(path).name
        if any(p["id"] == pid for p in made + demo_projects(sessions)):
            DEMO_TAGS[pid] = tags
            for p in made:
                if p["id"] == pid:
                    p["tags"] = tags
        else:
            real_set_tags(path, tags)

    def demo_add_category(name):
        DEMO_CATEGORY_LIST.append((name, ""))

    def demo_set_category(folder, category):
        pid = Path(folder).name
        DEMO_CATEGORIES[pid] = category
        for p in made:
            if p["id"] == pid:
                p["category"] = category

    def assign(sid, name, folder):
        pid = Path(folder).name
        listed = {p["id"]: p["sessions"] for p in demo_projects(sessions)}
        moved = [q for q, ss in listed.items() if q != pid and sid in ss]
        DEMO_LINKS[sid] = pid
        return sid not in listed.get(pid, []), moved

    patch(assign_session=assign, start_session=lambda pid, cwd, term, name=None: f"Started a Session for {pid} in {short(cwd)} (demo)")
    patch(read_projects=lambda ss=(): made + demo_projects(ss), create_project=new_project, set_tags=demo_set_tags,
             set_category=demo_set_category, read_categories=lambda: list(DEMO_CATEGORY_LIST),
             add_category=demo_add_category,
             projects_dir=lambda: Path(HOME, ".roost", "projects"),
             promote_recap=lambda path, pid, summary, label=None: (Path(path).unlink(), new_project(pid, summary, label))[1])
    # Notes and recaps are real files in a folder that's removed when roost quits, so the editor can open them
    scratch = Path(tempfile.mkdtemp(prefix="roost-demo-"))
    atexit.register(shutil.rmtree, str(scratch), True)
    config.NOTES, config.RECAPS, filed = scratch / "notes", scratch / "sessions", scratch / "projects"
    config.NOTES.mkdir()
    for name, text, hrs in DEMO_RECAPS:  # each written by the closed Session with the same name: a note tagged recap
        path = config.NOTES / f"{name}.md"
        s = next(s for s in sessions if slug(s["name"]) == name)
        path.write_text(f"---\ntitle: {s['name']}\ncreated: {datetime.fromtimestamp(now - hrs * 3600):%Y-%m-%d}\nproject:\n"
                        f"summary: {text}\nsessions:\n  - name: {s['name']}\n    id: {s['id']}\ntags: [recap]\n---\n\n"
                        f"# {s['name']}\n\n### What I did\n- {text}\n")
        os.utime(path, (now - hrs * 3600, now - hrs * 3600))
    formatting.SHOWN[:] = [(str(config.NOTES), short(str(notedata_home() / "notes"))),
                           (str(filed), short(str(Path(HOME, ".roost", "projects"))))]
    for title, pid, tags, body, hrs in DEMO_NOTES:
        folder = filed / pid / "notes" if pid else config.NOTES
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{slug(title)}.md"
        path.write_text(f"---\ntitle: {title}\ncreated: {datetime.fromtimestamp(now - (hrs + 48) * 3600):%Y-%m-%d}\n"
                        f"project: {pid or ''}\nsessions: []\ntags: [{', '.join(tags)}]\n---\n\n# {title}\n\n{body}\n")
        os.utime(path, (now - hrs * 3600, now - hrs * 3600))
    real_file_note = notedata.file_note

    def demo_file_note(path, folder):
        (filed / Path(folder).name).mkdir(parents=True, exist_ok=True)
        return real_file_note(path, filed / Path(folder).name)

    patch(note_dirs=lambda: [(None, config.NOTES)] + [(d.name, d / "notes") for d in sorted(filed.glob("*")) if (d / "notes").is_dir()],
             file_note=demo_file_note)
    patch(retention=lambda: (30, None), gather=lambda: sessions, regather=lambda old: sessions, live_sessions=lambda: {}, attach_live=lambda ss, live: sessions,
             mark_open=lambda ss, term: sessions, set_pin=set_pin, revive_pinned=revive,
             stop_session=stop, open_in_window=open_win, delete_session=delete,
             rename_session=rename, check_compat=lambda: "", update_available=lambda: "",
             plugin_problem=lambda: "", taken_plugin_change=lambda: "", plugin_registered=lambda s=None: True,
             chosen_terminal=lambda: "ghostty", open_session=lambda s: None)

    def fake_summary(sid, model=None, cap=None, progress=None, **kw):
        for step in (1, 2, 3):
            if progress:
                progress(step)
            time.sleep(1.5)
        pid = DEMO_LINKS.get(sid) or next((p["id"] for p in demo_projects(sessions) if sid in p["sessions"]), "project")
        return Path(HOME, ".roost", "projects", pid, "handoffs", f"{datetime.now():%Y-%m-%d-%H%M}-{sid[:8]}.md"), [], 0.41, True
    patch(summary_plan=lambda sid: {}, summary_estimate=lambda plan, model, table: 0.42, prices=lambda now=None: None,
             summarize=fake_summary)
    patch(gather_spend=lambda table=None, now=None: demo_spend(sessions), monthly_limit=lambda: 1500.0)


def demo_spend(sessions, days=120):
    """Four months of made-up spend for the Stats tab: busier on weekdays, a few quiet days, and
    roost's own calls now and then. The same every run."""
    import random
    from datetime import date, datetime, timedelta
    rnd, today, ids = random.Random(7), date.today(), [s["id"] for s in sessions]
    rows, tokens = [], {}
    for i in range(days):
        d = today - timedelta(days=days - 1 - i)
        if d != today and rnd.random() < (0.35 if d.weekday() >= 5 else 0.06):  # today always has some
            continue
        day, grow = d.isoformat(), 0.5 + i / days
        for _ in range(rnd.randint(2, 6)):
            rows.append((day, "Live work", rnd.choice(ids), None, round(rnd.uniform(1, 14) * grow, 2)))
        if rnd.random() < 0.6:
            rows.append((day, "Wrap-ups", rnd.choice(ids), None, round(rnd.uniform(0.5, 3), 2)))
        if rnd.random() < 0.5:
            rows.append((day, "Recaps and Handoffs", rnd.choice(ids), None, round(rnd.uniform(0.2, 1.2), 2)))
        if rnd.random() < 0.15:
            rows.append((day, "Ask", rnd.choice(ids), None, round(rnd.uniform(0.05, 0.3), 2)))
        if rnd.random() < 0.05:
            rows.append((day, "Adopt", None, "2026-09-data-sharing-cleanup", round(rnd.uniform(0.3, 1), 2)))
        tokens[day] = [int(9e6 * grow), int(1e7 * grow)]
    hours, now = {}, datetime.now().hour  # each row at an hour of a working day (today's, so far): the Today chart's line
    for day, kind, _, _, cost in rows:
        last = min(18, now) if day == today.isoformat() else 18
        for part in range(3):  # a reply's spend comes over a few hours
            hours.setdefault(day, {}).setdefault(kind, [0.0] * 24)[rnd.randint(min(8, last), last)] += cost / 3
    return {"rows": rows, "hours": hours, "tokens": tokens, "unpriced": [], "priced": True}
