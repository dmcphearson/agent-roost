from support import *  # noqa: F401,F403 (the shared namespace, helpers, and fixtures)
from support import _SCRATCH, own_tests  # noqa: F401
from test_projects import Projects  # for their helpers; their tests run in test_projects.py

load_tests = own_tests(__name__)


class Handoffs(unittest.TestCase):
    """#50: roost handoff writes facts from the transcript plus a summary; roost ledger prints the facts."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.saved = (roost.CONFIG, roost.DONE, roost.RECAPS, roost.PROJECTS, roost.PRICES,
                      roost.urllib.request.urlopen, roost.LOG, roost.NOTES)
        roost.CONFIG, roost.DONE, roost.RECAPS, roost.NOTES = base / "config.json", base / "_done", base / "sessions", base / "notes"
        roost.PROJECTS, roost.PRICES, roost.LOG = base / "transcripts", base / "prices.json", base / "roost.log"
        self.fetches = []
        roost.urllib.request.urlopen = self.urlopen
        self.page = PRICE_PAGE
        self.root = base / "projects"
        self.root.mkdir()
        roost.save_config(projects_dir=str(self.root))
        self.folder = self.root / "printers"
        (self.folder / "handoffs").mkdir(parents=True)
        (self.folder / "README.md").write_text(f"---\nsummary: Printers\nsessions:\n  - name: Printer fix\n    id: {SID}\n---\n")
        self.transcript(TRANSCRIPT)

    def tearDown(self):
        (roost.CONFIG, roost.DONE, roost.RECAPS, roost.PROJECTS, roost.PRICES,
         roost.urllib.request.urlopen, roost.LOG, roost.NOTES) = self.saved
        self.tmp.cleanup()

    def urlopen(self, req, timeout=None):
        self.fetches.append(req.get_header("User-agent"))
        if self.page is None:
            raise OSError("offline")
        import io
        return io.BytesIO(self.page.encode())

    def transcript(self, records, sid=SID, sub=None):
        d = roost.PROJECTS / "-w"
        path = d / f"{sid}.jsonl" if sub is None else d / sid / "subagents" / f"{sub}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r) + "\n" for r in records))

    def handoff(self, summary="Fixed the printers.", now=None):
        return roost.write_handoff(SID, summary, now=now or time.mktime((2026, 10, 9, 16, 5, 0, 0, 0, -1)))

    # prices

    def test_prices_come_from_the_model_and_fast_mode_tables(self):
        t = roost.parse_prices(PRICE_PAGE)
        self.assertEqual(t["standard"]["claude-opus-5-5"], [[None, [4, 5, 8, 0.2, 20]]])
        self.assertIn("claude-opus-4-1", t["standard"])
        self.assertEqual([tier for tier, _ in t["standard"]["claude-haiku-5-5"]], [100_000, None])
        self.assertEqual(t["fast"], {"claude-opus-5-5": [8, 40], "claude-opus-5": [10, 50], "claude-opus-4-8": [10, 50]})
        self.assertNotIn("claude-opus-5-5", {k for k, v in t["standard"].items() if v[0][1][0] == 2})  # batch table ignored
        with self.assertRaises(ValueError):
            roost.parse_prices("# Pricing\n\nNothing here.")

    def test_reply_cost_at_list_prices(self):
        t = roost.parse_prices(PRICE_PAGE)
        u = {"input_tokens": 1_000_000, "output_tokens": 1_000_000, "cache_read_input_tokens": 1_000_000,
             "cache_creation_input_tokens": 2_000_000,
             "cache_creation": {"ephemeral_5m_input_tokens": 1_000_000, "ephemeral_1h_input_tokens": 1_000_000}}
        self.assertAlmostEqual(roost.reply_cost("claude-opus-5-5", u, t), 4 + 5 + 8 + 0.2 + 20)
        self.assertAlmostEqual(roost.reply_cost("claude-opus-5-5", dict(u, inference_geo="us"), t), 37.2 * 1.1)
        self.assertAlmostEqual(roost.reply_cost("claude-opus-5-5", dict(u, speed="fast"), t), 2 * 37.2)
        small = {"input_tokens": 1000, "output_tokens": 0}
        self.assertAlmostEqual(roost.reply_cost("claude-haiku-5-5-20261001", small, t), 0.1 * 1000 / 1e6)
        self.assertAlmostEqual(roost.reply_cost("claude-haiku-5-5", {"input_tokens": 200_000}, t), 0.5 * 0.2)
        self.assertIsNone(roost.reply_cost("claude-sonnet-9", u, t))
        self.assertIsNone(roost.reply_cost("claude-opus-4-1", dict(u, speed="fast"), t))  # no fast price listed

    def test_prices_are_fetched_once_a_day_and_the_cache_covers_a_failed_fetch(self):
        first = roost.prices(now=1000)
        self.assertEqual((len(self.fetches), first["fetched"]), (1, 1000))
        self.assertTrue(self.fetches[0].startswith("roost/"))  # the site refuses Python's default agent
        roost.prices(now=1000 + 3600)
        self.assertEqual(len(self.fetches), 1)
        self.page = None
        self.assertEqual(roost.prices(now=1000 + 2 * 86400)["fetched"], 1000)
        roost.PRICES.unlink()
        self.assertIsNone(roost.prices(now=1000 + 2 * 86400))

    def test_a_failed_fetch_waits_an_hour_before_the_next(self):
        self.page = None
        self.assertIsNone(roost.prices(now=1000))
        self.assertIsNone(roost.prices(now=1000 + 60))
        self.assertEqual(len(self.fetches), 1)
        self.assertIsNone(roost.prices(now=1000 + 3700))
        self.assertEqual(len(self.fetches), 2)
        self.page = PRICE_PAGE
        roost.PRICES.unlink()
        roost.prices(now=5000)
        self.page = None
        roost.prices(now=5000 + 2 * 86400)
        self.assertEqual(roost.prices(now=5000 + 2 * 86400 + 60)["fetched"], 5000)  # the old copy, no new fetch
        self.assertEqual(len(self.fetches), 4)

    # facts

    def test_facts_come_from_the_transcript(self):
        t = roost.parse_prices(PRICE_PAGE)
        f = roost.facts(SID, table=t)
        self.assertEqual(f["name"], "Printer fix")
        self.assertEqual((f["from"], f["to"]), ("2026-10-09T10:00:00.000Z", "2026-10-09T11:00:00.000Z"))
        self.assertEqual(f["models"], {"claude-opus-5-5": 2})
        self.assertEqual(f["tokens"], {"input": 20, "cache write": 4000, "cache read": 200_000, "output": 2000})
        self.assertEqual(f["files"], ["/w/printers.md", "/w/later.md"])
        self.assertEqual(f["failed"], [("make test", 2)])
        self.assertEqual(f["commits"], [("on main", "abc1234def", "Fix printers")])
        self.assertEqual(f["prs"], ["https://github.com/o/r/pull/7"])
        one = roost.reply_cost("claude-opus-5-5", USAGE, t)
        self.assertAlmostEqual(f["cost"], one * 3)  # the fast reply costs twice as much
        self.assertEqual(roost.facts(SID, table=t), f)

    def test_facts_since_a_time_and_subagents_count(self):
        self.transcript([reply("2026-10-09T10:30:00.000Z", "s1", USAGE)], sub="agent-1")
        f = roost.facts(SID, since="2026-10-09T10:05:00.000Z")
        self.assertEqual(f["from"], "2026-10-09T10:30:00.000Z")
        self.assertEqual(f["models"], {"claude-opus-5-5": 2})
        self.assertEqual((f["files"], f["failed"], f["commits"], f["prs"]), (["/w/later.md"], [], [], []))
        self.assertEqual(f["unpriced"], ["claude-opus-5-5"])  # no price table given

    def test_quiet_commits_are_read_from_git_in_the_commands_repo(self):
        calls = []
        saved = roost.subprocess.run
        roost.subprocess.run = lambda argv, **kw: calls.append(argv) or type(
            "R", (), {"returncode": 0, "stdout": "fed9876\tQuiet commit\n"})()
        try:
            self.transcript([reply("2026-10-09T10:01:00.000Z", "m1", USAGE, [bash("q", "cd ~/repo && git commit -qm x")]),
                             result("2026-10-09T10:01:05.000Z", "q", "", stdout="")])
            f = roost.facts(SID)
        finally:
            roost.subprocess.run = saved
        self.assertEqual(f["commits"], [("in repo", "fed9876", "Quiet commit")])
        self.assertEqual(calls[0][:3], ["git", "-C", str(Path.home() / "repo")])

    # redaction

    def test_secrets_are_redacted_by_kind(self):
        pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----"
        text = (f"pk_12345678_ABCDEFGHIJKLMNOP ghp_{'a' * 36} github_pat_{'b' * 30} AKIAABCDEFGHIJKLMNOP "
                f"sk-ant-api03-{'c' * 30} xoxb-1234-5678-abcdef {pem} password=hunter2 "
                f"access_token=\"s3cr3t\" token=ghp_{'d' * 36} pk_id stays")
        out, kinds = roost.redact(text)
        for kind in ("pk", "github", "aws", "anthropic", "slack", "private-key", "password", "token"):
            self.assertIn(f"[REDACTED:{kind}]", out)
        self.assertNotIn("hunter2", out)
        self.assertNotIn("s3cr3t", out)
        self.assertNotIn("MIIabc", out)
        self.assertIn("token=[REDACTED:github]", out)  # not redacted twice
        self.assertIn("pk_id stays", out)
        self.assertEqual(sorted(kinds), sorted(["pk", "github", "github", "github", "aws", "anthropic", "slack",
                                                "private-key", "password", "token"]))
        okta = "00" + "Ab3_x-" * 6 + "Zz9q"  # an Okta API token: 00 and 40 more
        out, kinds = roost.redact(f"pasted {okta} here, but a sha {'0' * 40} and 00{'a' * 41} stay")
        self.assertEqual((out.count("[REDACTED:okta]"), kinds), (1, ["okta"]))
        self.assertNotIn(okta, out)

    # roost handoff

    def test_a_session_two_places_list_has_no_owner(self):
        """One project listing a Session owns its Handoffs; two (or a project and a recap) is refused, naming them."""
        self.assertEqual(roost.owner([SID]), self.folder)
        other = self.root / "toner"
        (other / "handoffs").mkdir(parents=True)
        (other / "README.md").write_text(f"---\nsummary: Toner\nsessions:\n  - name: Printer fix\n    id: {SID}\n---\n")
        with self.assertRaises(ValueError) as e:
            roost.owner([SID])
        self.assertIn("printers", str(e.exception))
        self.assertIn("toner", str(e.exception))
        with self.assertRaises(ValueError):
            self.handoff()
        self.assertEqual(list((self.folder / "handoffs").iterdir()) + list((other / "handoffs").iterdir()), [])
        (other / "README.md").write_text("---\nsummary: Toner\nsessions: []\n---\n")
        roost.NOTES.mkdir(parents=True, exist_ok=True)
        recap = roost.NOTES / "printer-fix.md"
        recap.write_text(f"---\ntitle: Printer fix\nproject:\nsummary: Fixed\nsessions:\n  - name: Printer fix\n"
                         f"    id: {SID}\ntags: [recap]\n---\n\n# Printer fix\n")
        before = recap.read_text()
        with self.assertRaises(ValueError) as e:
            roost.owner([SID])
        self.assertIn("printers", str(e.exception))
        self.assertIn("printer-fix", str(e.exception))
        with self.assertRaises(ValueError):
            self.handoff()
        self.assertEqual(list((self.folder / "handoffs").iterdir()), [])
        self.assertEqual(recap.read_text(), before)

    def test_handoff_writes_facts_and_summary_and_never_overwrites(self):
        path, kinds = self.handoff(f"Fixed it. The key was ghp_{'a' * 36}.")
        self.assertEqual(path, self.folder / "handoffs" / "2026-10-09-1605-11111111.md")
        text = path.read_text()
        meta = roost.frontmatter(text)
        self.assertEqual((meta["session"], meta["name"], meta["to"]), (SID, "Printer fix", "2026-10-09T11:00:00.000Z"))
        self.assertIn("## Facts", text)
        self.assertIn("- **Failed commands (1):**\n  - `make test` (exit 2)", text)
        self.assertIn("- **Cost:** about $", text)
        self.assertIn("## Summary\n\nFixed it. The key was [REDACTED:github].", text)
        self.assertEqual(kinds, ["github"])
        with self.assertRaisesRegex(ValueError, "never overwrites"):
            self.handoff("Again.")
        self.assertEqual(path.read_text(), text)
        self.assertEqual([p.name for p in (self.folder / "handoffs").iterdir()], [path.name])

    def test_a_later_handoff_covers_only_what_came_after(self):
        self.handoff()
        self.transcript(TRANSCRIPT + [reply("2026-10-09T17:00:00.000Z", "m3", USAGE, [
            {"type": "tool_use", "id": "e9", "name": "Edit", "input": {"file_path": "/w/new.md"}}])])
        path, _ = self.handoff("More.", now=time.mktime((2026, 10, 9, 17, 30, 0, 0, 0, -1)))
        text = path.read_text()
        self.assertIn("- **Files changed (1):**\n  - `/w/new.md`", text)
        self.assertIn("claude-opus-5-5 (1 reply)", text)
        path, _ = self.handoff("Nothing new.", now=time.mktime((2026, 10, 9, 18, 0, 0, 0, 0, -1)))
        self.assertIn("no activity since its last Handoff", path.read_text())

    def test_handoff_refusals(self):
        with self.assertRaisesRegex(ValueError, "no summary"):
            self.handoff("  \n")
        with self.assertRaisesRegex(ValueError, "isn't a Session id"):
            roost.write_handoff("*", "x")
        with self.assertRaisesRegex(ValueError, "no transcript"):
            roost.write_handoff("99999999-2222-3333-4444-555555555555", "x")
        (self.folder / "README.md").write_text("---\nsummary: Printers\nsessions: []\n---\n")
        with self.assertRaisesRegex(ValueError, "no project lists Session 11111111"):
            self.handoff()
        self.assertEqual(list((self.folder / "handoffs").iterdir()), [])

    def test_offline_handoff_says_cost_unknown(self):
        self.page = None
        path, _ = self.handoff()
        self.assertIn("- **Cost:** unknown: roost couldn't fetch Anthropic's prices", path.read_text())

    # roost ledger, and last worked

    def test_ledger_prints_every_handoffs_facts_oldest_first(self):
        self.handoff("Summary one.", now=time.mktime((2026, 10, 9, 16, 5, 0, 0, 0, -1)))
        (self.folder / "handoffs" / "2026-10-01-0900-aaaaaaaa.md").write_text("Written by hand.\n")
        text = roost.ledger(self.folder)
        self.assertTrue(text.startswith("# Ledger of printers: 2 Handoffs, oldest first"))
        self.assertLess(text.index("2026-10-01-0900-aaaaaaaa.md"), text.index("2026-10-09-1605-11111111.md"))
        self.assertIn("(No Facts section", text)
        self.assertIn("- **Session:** Printer fix", text)
        self.assertNotIn("Summary one.", text)
        self.assertIn("No Handoffs yet.", roost.ledger(self.root / "missing"))

    def test_a_sub_project_listing_the_session_owns_its_handoffs(self):
        (self.folder / "README.md").write_text("---\nsummary: Printers\nsessions: []\n---\n")
        sub = self.folder / "sub" / "toner"
        (sub / "handoffs").mkdir(parents=True)
        (sub / "README.md").write_text(f"---\nsummary: Toner\ntype: feature\nstatus: active\nsessions:\n"
                                       f"  - name: Printer fix\n    id: {SID}\n---\n")
        self.assertEqual(roost.owner([SID]), sub)
        path, _ = self.handoff()
        self.assertEqual(path, sub / "handoffs" / "2026-10-09-1605-11111111.md")
        self.assertIn("## Summary\n\nFixed the printers.", path.read_text())
        self.assertEqual(list((self.folder / "handoffs").iterdir()), [])

    def test_last_worked_uses_a_newer_handoff(self):
        (self.folder / "handoffs" / "2026-10-09-1605-11111111.md").write_text("x")
        p = roost.read_project(self.folder, [{"id": SID, "ids": [SID], "last": time.mktime((2026, 10, 8, 9, 0, 0, 0, 0, -1))}])
        self.assertEqual(p["last"], time.mktime((2026, 10, 9, 16, 5, 0, 0, 0, -1)))
        p = roost.read_project(self.folder, [{"id": SID, "ids": [SID], "last": time.mktime((2026, 10, 10, 9, 0, 0, 0, 0, -1))}])
        self.assertEqual(p["last"], time.mktime((2026, 10, 10, 9, 0, 0, 0, 0, -1)))


class TranscriptSummary(unittest.TestCase):
    """#52: the Handoff and README update from a transcript, without resuming the Session."""
    setUp, tearDown, urlopen, transcript = Handoffs.setUp, Handoffs.tearDown, Handoffs.urlopen, Handoffs.transcript

    def run_summary(self, replies=None, cap=5.0, fail_at=None, during=None):
        """summarize() with a stand-in for the model: each call gets the next reply and costs $1.
        during(n) runs while call n is out, standing in for someone working meanwhile."""
        replies = list(replies or [DRAFT, DRAFT + "\n<!-- review: 0 changes: none -->",
                                   "# Printers\n\n## Current state\n\nFixed.\n\n## Next\n\nCheck the second office.\n"])
        self.calls, self.steps = [], []

        def call(model, system, prompt, budget):
            self.calls.append((model, prompt, budget))
            if fail_at == len(self.calls):
                raise ValueError("the model is down")
            if during:
                during(len(self.calls))
            return replies.pop(0), 1.0
        saved = roost.read_only
        roost.read_only = lambda cmd: "(stub)"
        try:
            return roost.summarize(SID, "claude-opus-5-5", cap, now=time.mktime((2026, 10, 9, 16, 5, 0, 0, 0, -1)), call=call,
                                   progress=self.steps.append)
        finally:
            roost.read_only = saved

    def test_the_conversation_is_numbered_without_reminders_or_thinking(self):
        self.transcript([
            {"type": "user", "timestamp": "2026-10-09T10:00:00.000Z", "message": {"content": "go <system-reminder>noise</system-reminder>"}},
            {"type": "assistant", "timestamp": "2026-10-09T10:00:01.000Z", "message": {"content": [
                {"type": "thinking", "thinking": "secret plan"}, {"type": "text", "text": "ok"}, bash("b1", "ls")]}},
            result("2026-10-09T10:00:02.000Z", "b1", "x" * 5000 + " arn:aws:iam::123456789012:role/Admin"),
            {"type": "attachment", "timestamp": "2026-10-09T10:00:03.000Z", "attachment": {"type": "queued_command", "prompt": "also this", "origin": {"kind": "human"}}},
        ])
        entries, ids, _ = roost.conversation(next(roost.PROJECTS.glob(f"*/{SID}.jsonl")))
        self.assertEqual([e.split(":", 1)[0] for e in entries],
                         ["[1] USER", "[2] CLAUDE", "[3] TOOL CALL Bash", "[4] RESULT Bash", "[5] USER (sent while Claude worked)"])
        self.assertNotIn("noise", entries[0])
        self.assertFalse([e for e in entries if "secret plan" in e])
        self.assertIn("chars cut", entries[3])
        self.assertEqual(ids["arn:aws:iam::123456789012:role/Admin"], 4)  # kept although the output was cut
        later, _, _ = roost.conversation(next(roost.PROJECTS.glob(f"*/{SID}.jsonl")), since="2026-10-09T10:00:01.000Z")
        self.assertEqual(len(later), 2)

    def test_it_writes_the_handoff_and_the_readme_body_and_keeps_the_frontmatter(self):
        readme = self.folder / "README.md"
        readme.write_text(readme.read_text() + "\n# Printers\n\nOld state.\n")
        path, kinds, spent, updated = self.run_summary()
        self.assertTrue(updated)
        self.assertEqual(self.steps, [1, 2, 3])  # draft, review, README: the row shows which
        self.assertEqual(roost.UI.progress({"step": 2, "steps": 3, "started": time.time() - 100}), "2/3 · 1m40s")
        text = path.read_text()
        self.assertIn("## Facts", text)
        self.assertIn("- Fixed the printers\n", text)  # citations come out
        self.assertNotIn("[state]", text)
        self.assertNotIn("<!-- review", text)
        self.assertEqual((kinds, spent), ([], 3.0))
        self.assertTrue(readme.read_text().startswith(f"---\nsummary: Printers\nsessions:\n  - name: Printer fix\n    id: {SID}\n---\n"))
        self.assertIn("## Current state\n\nFixed.", readme.read_text())
        self.assertNotIn("Old state", readme.read_text())
        self.assertEqual([b for _, _, b in self.calls], [5.0, 4.0, 3.0])  # each call gets what's left of the cap
        self.assertIn("Old state", self.calls[2][1])  # the rewrite starts from the README body
        self.assertIn("[1] USER: go", self.calls[0][1])

    def test_secrets_never_reach_the_readme_rewrite(self):
        readme = self.folder / "README.md"
        _, _, _, updated = self.run_summary([DRAFT, DRAFT, "# Printers\n\n## Current state\n\n"
                                             "Fixed with AKIAABCDEFGHIJKLMNOP and token=s3cr3t-value.\n"])
        self.assertTrue(updated)
        text = readme.read_text()
        self.assertIn("## Current state\n\nFixed with", text)
        self.assertNotIn("AKIAABCDEFGHIJKLMNOP", text)
        self.assertNotIn("s3cr3t-value", text)

    def test_a_readme_edited_during_the_rewrite_keeps_the_edit(self):
        readme = self.folder / "README.md"
        readme.write_text(readme.read_text() + "\n# Printers\n\nOld state.\n")

        def edit(n):
            if n == 3:  # the person edits the body while the rewrite is out
                readme.write_text(readme.read_text().replace("Old state.", "Ordered toner."))
        path, _, spent, updated = self.run_summary(during=edit)
        self.assertFalse(updated)
        self.assertTrue(path.exists())  # the Handoff is still written
        self.assertIn("Ordered toner.", readme.read_text())
        self.assertNotIn("## Current state", readme.read_text())
        self.assertEqual(spent, 3.0)

    def test_frontmatter_written_during_the_rewrite_doesnt_stop_it(self):
        readme = self.folder / "README.md"

        def record(n):
            if n == 3:  # roost records another Session meanwhile
                readme.write_text(readme.read_text().replace("sessions:\n", "sessions:\n  - name: Other\n    id: other\n", 1))
        _, _, _, updated = self.run_summary(during=record)
        self.assertTrue(updated)
        self.assertIn("id: other", readme.read_text())
        self.assertIn("## Current state", readme.read_text())

    def test_two_summaries_in_one_project_take_turns_on_the_readme(self):
        import threading
        other = "99999999-2222-3333-4444-555555555555"
        readme = self.folder / "README.md"
        readme.write_text(f"---\nsummary: Printers\nsessions:\n  - name: Printer fix\n    id: {SID}\n"
                          f"  - name: Toner\n    id: {other}\n---\n\n# Printers\n\nOld state.\n")
        self.transcript(TRANSCRIPT, sid=other)
        in_rewrite, prompts = threading.Event(), {}

        def model(name):
            def call(model, system, prompt, budget):
                if "Old state" in prompt or "## Current state" in prompt:  # the README rewrite
                    prompts[name] = prompt
                    if name == "first":
                        in_rewrite.set()
                        time.sleep(0.3)  # the second would read the README now, without a turn
                    return f"# Printers\n\n## Current state\n\n{name} done.\n", 1.0
                return DRAFT, 1.0
            return call
        saved = roost.read_only
        roost.read_only = lambda cmd: "(stub)"
        try:
            a = threading.Thread(target=roost.summarize, args=(SID, "m", 5.0), kwargs={"call": model("first")})
            a.start()
            in_rewrite.wait(5)
            _, _, _, updated = roost.summarize(other, "m", 5.0, call=model("second"))
            a.join(5)
        finally:
            roost.read_only = saved
        self.assertTrue(updated)
        self.assertIn("first done.", prompts["second"])  # the second rewrote what the first wrote
        self.assertIn("second done.", readme.read_text())
        self.assertEqual(len(list((self.folder / "handoffs").iterdir())), 2)

    def test_a_failure_writes_nothing(self):
        readme = (self.folder / "README.md").read_text()
        for kwargs in ({"fail_at": 2}, {"fail_at": 3}, {"cap": 1.5},
                       {"replies": [DRAFT, DRAFT, "# Printers\n\nNo sections."]}, {"replies": [DRAFT, "Just prose."]}):
            with self.assertRaises(ValueError):
                self.run_summary(**kwargs)
            self.assertEqual(list((self.folder / "handoffs").iterdir()), [], kwargs)
            self.assertEqual((self.folder / "README.md").read_text(), readme)

    def test_it_covers_only_what_came_after_the_last_handoff(self):
        self.run_summary()
        with self.assertRaisesRegex(ValueError, "nothing new since its last Handoff"):
            roost.summary_plan(SID)
        self.transcript(TRANSCRIPT + [
            {"type": "user", "timestamp": "2026-10-09T12:00:00.000Z", "message": {"content": "one more thing"}}])
        plan = roost.summary_plan(SID)
        self.assertEqual(plan["since"], "2026-10-09T11:00:00.000Z")
        self.assertEqual(plan["entries"], ["[1] USER: one more thing"])

    def test_with_no_project_it_writes_a_session_note_and_no_readme(self):
        (self.folder / "README.md").write_text("---\nsummary: Printers\n---\n")
        plan = roost.summary_plan(SID)
        self.assertEqual((plan["folder"], plan["project"]), (None, False))
        table = roost.parse_prices(PRICE_PAGE)
        self.assertLess(roost.summary_estimate(plan, "claude-opus-5-5", table),
                        roost.summary_estimate(dict(plan, project=True), "claude-opus-5-5", table))
        path, _, spent, updated = self.run_summary()
        self.assertEqual((path, spent, updated), (roost.NOTES / "printer-fix.md", 2.0, None))  # no README call
        self.assertEqual(len(self.calls), 2)

    def test_the_estimate_comes_from_the_input_size_and_list_prices(self):
        table = roost.parse_prices(PRICE_PAGE)
        plan = roost.summary_plan(SID)
        est = roost.summary_estimate(plan, "claude-opus-5-5", table)
        self.assertGreater(est, 0)
        self.assertLess(est, 1)
        self.assertIsNone(roost.summary_estimate(plan, "claude-sonnet-9", table))
        self.assertEqual(roost.summary_settings(), ("claude-sonnet-5-5", 5.0))
        roost.save_config(summary_model="claude-opus-5-5", summary_budget_usd=2)
        self.assertEqual(roost.summary_settings(), ("claude-opus-5-5", 2.0))

    def test_the_model_call_has_no_tools_and_a_budget(self):
        seen, out = [], {"stdout": json.dumps({"type": "result", "subtype": "success", "result": "hi", "total_cost_usd": 0.5})}
        saved = roost.subprocess.run

        def run(cmd, **kw):
            seen.append((cmd, kw))
            return type("R", (), {"returncode": 0, "stderr": "", **out})()
        roost.subprocess.run = run
        try:
            self.assertEqual(roost.claude_print("claude-sonnet-5-5", "sys", "prompt", 4.5), ("hi", 0.5))
            cmd, kw = seen[0]
            self.assertEqual(cmd[:2], ["claude", "-p"])
            for flag, value in (("--tools", ""), ("--max-budget-usd", "4.50"), ("--model", "claude-sonnet-5-5"),
                                ("--setting-sources", ""), ("--system-prompt", "sys")):
                self.assertEqual(cmd[cmd.index(flag) + 1], value)
            self.assertIn("--no-session-persistence", cmd)
            self.assertEqual(kw["input"], "prompt")
            out["stdout"] = json.dumps({"type": "result", "subtype": "error_max_budget_usd", "is_error": True})
            with self.assertRaisesRegex(ValueError, r"\$4.50"):
                roost.claude_print("claude-sonnet-5-5", "sys", "prompt", 4.5)
            out["stdout"] = "not json"
            with self.assertRaises(ValueError):
                roost.claude_print("claude-sonnet-5-5", "sys", "prompt", 4.5)
        finally:
            roost.subprocess.run = saved

    def test_up_to_three_summaries_run_at_once(self):
        ui = object.__new__(roost.UI)
        ui.msg, ui.confirm, ui.offer, ui.sessions = "", None, None, []
        ui.projects = [{"id": "printers", "sessions": [SID]}]
        job = lambda name, state="running": {"state": state, "name": name, "text": "", "step": 1, "steps": 3, "started": 0}
        mine = dict(session("Printer fix"), id=SID)
        saved = roost.prices
        roost.prices = lambda now=None: None
        try:
            ui.summaries = {"a": job("A"), "b": job("B"), "c": job("C", "done")}
            ui.ask_summary(mine)
            self.assertEqual(ui.confirm, ("summary", mine))  # two running: a third may start
            ui.confirm, ui.summaries["c"] = None, job("C")
            ui.ask_summary(mine)
            self.assertEqual((ui.msg, ui.confirm), ("3 summaries are running: wait for one", None))
            ui.summaries = {SID: job("Printer fix")}
            ui.ask_summary(mine)
            self.assertEqual((ui.msg, ui.confirm), ("Its summary is already running", None))
            ui.summaries = {"adopt:/p": job("Printers")}  # an adopt or a question still runs on its own
            ui.ask_summary(mine)
            self.assertEqual((ui.msg, ui.confirm), ("Wait for Printers's run to finish first", None))
            ui.summaries, ui.quit_anyway, ui.tab = {"a": job("A"), "b": job("B")}, False, 0
            ui.helping = ui.choosing = ui.asking = ui.searching = ui.confirm = None
            ui.TABS = [ui.TABS[0]._replace(typing=lambda ui: False)]
            ui.dispatch("ctrl+q", None)
            self.assertEqual(ui.msg, "2 summaries are running and would be lost: ctrl+q again to quit anyway")
            for m in ("3 summaries are running: wait for one", "Its summary is already running"):
                self.assertTrue(roost.REFUSED.match(m), m)
        finally:
            roost.prices = saved

    def test_a_summary_estimated_over_the_cap_runs_with_the_cap_raised(self):
        """A run estimated over the cap would stop partway and lose what it spent, so that run's cap is
        raised to the estimate plus a quarter, in whole dollars, and the box says so."""
        ui = object.__new__(roost.UI)
        ui.summaries, ui.msg, ui.confirm, ui.offer, ui.sessions = {}, "", None, None, []
        ui.projects = [{"id": "printers", "sessions": [SID]}]
        ui.YELLOW = 0
        s = dict(session("Printer fix", kind=None, state="closed"), id=SID)
        ran = []
        saved = roost.summary_estimate, roost.summarize, roost.prices
        roost.prices = lambda now=None: None
        roost.summarize = lambda sid, model, cap, progress: ran.append(cap) or (self.folder / "handoffs" / "h.md", [], 0.4, False)
        ui.refresh = lambda: None
        try:
            for est, cap in ((6.37, 8), (4.0, 5.0), (None, 5.0)):
                roost.summary_estimate = lambda plan, model, table, est=est: est
                ui.ask_summary(s)
                said = " ".join(ui.confirm_text()[1])
                if est == 6.37:
                    self.assertIn("over the $5 cap, so this run is capped at $8", said)
                else:
                    self.assertIn("capped at $5.", said)
                ui.keys_confirm("y")
                for _ in range(100):
                    if ui.summaries[SID]["state"] != "running":
                        break
                    time.sleep(0.01)
                ui.summaries = {}
            self.assertEqual(ran, [8, 5.0, 5.0])
        finally:
            roost.summary_estimate, roost.summarize, roost.prices = saved

    def test_the_key_refuses_working_and_window_sessions_and_stops_background_ones(self):
        ui = object.__new__(roost.UI)
        ui.summaries, ui.msg, ui.confirm, ui.offer, ui.sessions = {}, "", None, None, []
        ui.projects = [{"id": "printers", "sessions": [SID]}]
        mine = lambda **kw: dict(session("Printer fix", **kw), id=SID)
        for s, said in ((mine(state="busy"), "working"), (mine(kind="interactive"), "exit it there first"),
                        (mine(attached=True), "exit it there first")):
            ui.ask_summary(s)
            self.assertIn(said, ui.msg)
            self.assertIsNone(ui.confirm)
        stopped, ran = [], []
        saved = roost.stop_session, roost.summarize, roost.prices
        roost.stop_session, roost.prices = stopped.append, lambda now=None: None
        roost.summarize = lambda sid, model, cap, progress: progress(2) or ran.append((sid, model, cap)) or (self.folder / "handoffs" / "h.md", [], 0.4, False)
        ui.refresh = lambda: None
        try:
            s = dict(mine(), attach="job1")
            ui.ask_summary(s)
            self.assertEqual(ui.confirm, ("summary", s))
            self.assertEqual(ui.offer[:2], ("claude-sonnet-5-5", 5.0))
            ui.keys_confirm("y")
            for _ in range(100):
                if ui.summaries[SID]["state"] != "running":
                    break
                time.sleep(0.01)
            self.assertEqual(stopped, ["job1"])  # stopped before the summary ran
            self.assertEqual(ran, [(SID, "claude-sonnet-5-5", 5.0)])
            self.assertEqual(ui.summaries[SID]["state"], "done")
            self.assertIn("left the README alone: it was edited while the summary ran", ui.summaries[SID]["text"])
        finally:
            roost.stop_session, roost.summarize, roost.prices = saved


class SessionRecaps(unittest.TestCase):
    """#53: a Session with no project leaves a Session recap, which a fresh Session can pick up and
    which can grow into a project."""
    setUp, tearDown, urlopen, transcript = Handoffs.setUp, Handoffs.tearDown, Handoffs.urlopen, Handoffs.transcript

    def unlist(self):
        (self.folder / "README.md").write_text("---\nsummary: Printers\nsessions: []\n---\n")

    def at(self, hour, minute=0):
        return time.mktime((2026, 10, 9, hour, minute, 0, 0, 0, -1))

    def note(self, summary=DRAFT, hour=16, **kw):
        return roost.write_handoff(SID, summary, now=self.at(hour), recap=True, **kw)

    def test_a_wrapped_up_session_starts_fresh_from_its_recap(self):
        """After its recap, a closed Session is wrapped up: it never reopens, and a fresh Session picks up the recap."""
        self.unlist()
        self.note()  # covers it to 11:00
        ui = Projects.ui(self)
        ui.DIM, ui.status = 0, {}
        end = roost.stamp("2026-10-09T11:00:00.000Z")
        s = dict(session("Printer fix", state="closed", kind=None), id=SID, active=end)
        ui.sessions = [s]
        ui.load_projects()
        self.assertEqual(ui.status_cell(s)[0], "✓ wrapped up")
        acts = ui.session_actions(s)
        self.assertEqual(acts[0][0], "Start a fresh Session from its recap")
        self.assertFalse([l for l, _ in acts if l.startswith(("Open", "Restart"))])
        started = []
        ui.start = lambda pid, cwd, name=None: started.append((pid, cwd))
        acts[0][1]()
        self.assertEqual(started, [("printer-fix", "/tmp")])  # the recap's folder (/w) is gone, so where the Session worked
        self.assertTrue(ui.resume_refused(s))
        ui.YELLOW = ui.RED = 0
        self.assertTrue(ui.cost_line(dict(s, tokens=166_000, reply_at=end, ttl=3600))[0].startswith("wrapped up"))
        later = dict(s, active=end + 3600)  # it worked after its recap: that work isn't saved anywhere yet
        self.assertNotEqual(ui.status_cell(later)[0], "✓ wrapped up")
        self.assertTrue([l for l, _ in ui.session_actions(later) if l.startswith("Open")])

    def test_advice_reads_back_and_falls_back_to_keep(self):
        text = ("VERDICT: add-to-project\nPROJECT: printers\nFINISHED: no\nOPEN:\n- PR #59 needs review\n- none\n"
                "WARNINGS:\n- an API token was pasted\nWHY:\nIt wrote the project's README.\n")
        a = roost.parse_advice(text, ["printers"])
        self.assertEqual((a["verdict"], a["project"], a["finished"]), ("add-to-project", "printers", False))
        self.assertEqual((a["open"], a["warnings"], a["why"]), (["PR #59 needs review"], ["an API token was pasted"],
                                                                "It wrote the project's README."))
        self.assertEqual(roost.parse_advice(text, ["other"])["verdict"], "keep")  # a project it made up
        self.assertEqual(roost.parse_advice("VERDICT: shred it\nWHY:\nx")["verdict"], "keep")

    def test_ask_what_to_do_then_act_on_it(self):
        """One model call reads the Session with the facts code checked; its advice's key opens that step."""
        self.unlist()
        self.transcript(TRANSCRIPT)
        ui = Projects.ui(self)
        ui.DIM = ui.GREEN = ui.YELLOW = ui.RED = 0
        ui.status, ui.refresh = {}, lambda: None
        s = dict(session("Printer fix", state="closed", kind=None), id=SID, files=[str(self.folder / "README.md")])
        ui.sessions = [s]
        ui.load_projects()
        prompts = []
        sync = type("Sync", (), {"__init__": lambda self, target, daemon: setattr(self, "run", target),
                                 "start": lambda self: self.run()})
        saved = roost.claude_print, roost.threading.Thread
        roost.claude_print = lambda model, system, prompt, budget: prompts.append(prompt) or (
            "VERDICT: add-to-project\nPROJECT: printers\nFINISHED: yes\nOPEN:\n- none\nWARNINGS:\n- none\nWHY:\nIt fixed the printers.\n", 0.05)
        roost.threading.Thread = sync
        self.addCleanup(lambda: (setattr(roost, "claude_print", saved[0]), setattr(roost.threading, "Thread", saved[1])))
        act = dict(ui.session_actions(s))["Ask what to do with it…"]
        act()
        self.assertEqual(ui.confirm[0], "ask")
        self.assertIn("Ask what to do with Printer fix", ui.confirm_text()[0])
        ui.keys_confirm("y")
        self.assertIn("Projects whose folder it edited: printers", prompts[0])
        self.assertIn("Listed by: nothing", prompts[0])
        ui.report_summaries()
        c = ui.choosing  # the advice is a menu: what it found, then each choice spelled out
        self.assertEqual(c["title"], "What to do with Printer fix")
        self.assertTrue(c["body"][0].startswith("Finished. It fixed the printers."))
        self.assertEqual([l for l, _ in c["options"]],
                         ["Add it to printers, then write its Handoff (shows the cost first)  · advised",
                          "Only add it to printers", "Leave it as it is",
                          "Delete it: its history moves to the Trash (asks again)"])
        self.assertIsNone(ui.keys_choice("enter"))  # the advised one is selected
        self.assertEqual(roost.read_project(self.folder)["sessions"], [SID])  # added, and the Handoff asks next
        self.assertEqual(ui.confirm[0], "summary")

    def test_with_nothing_listing_it_a_handoff_needs_note(self):
        self.unlist()
        with self.assertRaisesRegex(ValueError, "--recap"):
            roost.write_handoff(SID, DRAFT)
        path, kinds = self.note()
        self.assertEqual(path, roost.NOTES / "printer-fix.md")
        text = path.read_text()
        meta = roost.frontmatter(text)
        self.assertEqual((meta["title"], roost.tag_list(meta["tags"]), meta["created"]), ("Printer fix", ["recap"], "2026-10-09"))
        [n] = roost.read_notes()  # on the Notes tab, with every note action
        self.assertEqual((n["name"], n["tags"], n["sessions"]), ("Printer fix", ["recap"], [SID]))
        self.assertEqual(meta["summary"], "Fixed the printers [3]")  # its first point, when none is given
        self.assertEqual(meta["sessions"], [{"name": "Printer fix", "id": SID}])
        self.assertEqual(meta["folder"], "/w")
        self.assertIn(f"<!-- roost handoff 2026-10-09-1600-11111111 session: {SID} from: 2026-10-09T10:00:00.000Z to: 2026-10-09T11:00:00.000Z -->", text)
        self.assertIn("## Facts", text)
        [n] = roost.read_recaps()
        self.assertEqual((n["id"], n["sessions"], n["folder"]), ("printer-fix", [SID], "/w"))
        self.assertEqual(roost.owner([SID]), path)

    def test_secrets_never_reach_a_recaps_summary(self):
        """Given or taken from the first point, a new recap's summary line is redacted too."""
        self.unlist()
        path, _ = self.note(recap_summary="Fixed it with token=s3cr3t-value")
        self.assertIn("Fixed it with", roost.frontmatter(path.read_text())["summary"])
        self.assertNotIn("s3cr3t-value", path.read_text())
        path.unlink()
        path, _ = self.note("### What I did\n- Rotated AKIAABCDEFGHIJKLMNOP\n### Next\n- More")
        self.assertIn("Rotated", roost.frontmatter(path.read_text())["summary"])
        self.assertNotIn("AKIAABCDEFGHIJKLMNOP", path.read_text())

    def test_a_later_handoff_is_added_at_the_end_and_covers_only_what_came_after(self):
        self.unlist()
        path, _ = self.note("### What I did\n- First part\n### Next\n- More", recap_summary="Printer fix")
        first = path.read_text()
        self.transcript(TRANSCRIPT + [{"type": "user", "timestamp": "2026-10-09T12:00:00.000Z", "message": {"content": "again"}}])
        path2, _ = roost.write_handoff(SID, "### What I did\n- Second part", now=self.at(17))
        self.assertEqual(path2, path)
        text = path.read_text()
        self.assertTrue(text.startswith(first.rstrip("\n")))  # nothing above the end changes
        sections = roost.recap_sections(text)
        self.assertEqual([(s[0], s[2]) for s in sections],
                         [("2026-10-09-1600-11111111", "2026-10-09T10:00:00.000Z"), ("2026-10-09-1700-11111111", "2026-10-09T12:00:00.000Z")])
        with self.assertRaisesRegex(ValueError, "never overwrites"):
            roost.write_handoff(SID, "again", now=self.at(17))

    def test_old_recaps_move_into_notes_once(self):
        self.unlist()
        roost.RECAPS.mkdir(parents=True)
        old = roost.RECAPS / "printer-fix.md"
        original = (f"---\nsummary: Fixed it\nfolder: /w\nsessions:\n  - name: Printer fix\n    id: {SID}\n---\n\n"
                    f"# Session recap printer-fix\n\n<!-- roost handoff 2026-10-09-1600-11111111 session: {SID} from:  "
                    f"to: 2026-10-09T11:00:00.000Z -->\n\nDone.\n")
        old.write_text(original)
        (roost.RECAPS / "loose.md").write_text("Restarted two Macs.\n")  # written by hand, no frontmatter
        roost.NOTES.mkdir()
        (roost.NOTES / "printer-fix.md").write_text("my own note\n")
        self.assertEqual(roost.move_recaps(), 2)
        self.assertFalse(roost.RECAPS.exists())
        self.assertEqual((roost.NOTES / "printer-fix.md").read_text(), "my own note\n")  # never replaced
        moved = roost.NOTES / "printer-fix-2.md"
        text = moved.read_text()
        meta = roost.frontmatter(text)
        self.assertEqual((meta["title"], meta["summary"], meta["folder"], roost.tag_list(meta["tags"])),
                         ("Printer fix", "Fixed it", "/w", ["recap"]))
        self.assertIn("Done.", text)
        self.assertEqual(roost.owner([SID]), moved)  # the next wrap-up adds to it
        self.assertEqual(roost.last_covered(moved, [SID]), "2026-10-09T11:00:00.000Z")
        self.assertEqual(sorted(r["id"] for r in roost.read_recaps()), ["loose", "printer-fix-2"])
        self.assertIn("Restarted two Macs.", (roost.NOTES / "loose.md").read_text())
        self.assertEqual(roost.move_recaps(), 0)  # a second run changes nothing
        roost.RECAPS.mkdir()
        old.write_text(original)  # as if the last run stopped after writing the note, before removing this
        self.assertEqual(roost.move_recaps(), 1)
        self.assertFalse(old.exists())
        self.assertFalse((roost.NOTES / "printer-fix-3.md").exists())  # its note was already there

    def test_a_recap_on_the_notes_tab_becomes_a_project_with_its_handoffs(self):
        self.unlist()
        path, _ = self.note()
        path.write_text(path.read_text().replace('folder: "/w"', f'folder: "{self.root}"'))  # a folder that exists
        ui = object.__new__(roost.UI)
        ui.sessions, ui.msg, ui.page, ui.last_click = [], "", 10, (0, -1)
        ui.psel, ui.ptop, ui.prow_y, ui.asking = 0, 0, {}, None
        ui.pquery, ui.searching, ui.term, ui.plugin_ok = "", False, None, True
        ui.folded, ui.expanded, ui.unwrapped = set(), set(), set()
        ui.choosing = ui.confirm = None
        ui.summaries = {}
        ui.load_projects()
        [n] = ui.notes
        started = []
        ui.start = lambda pid, cwd, name=None: started.append(cwd)
        dict(ui.note_actions(n))["Start a Session from it"]()
        self.assertEqual(started, [str(self.root)])  # where its Session worked, not home
        del ui.start
        dict(ui.note_actions(n))["Make it a project…"]()
        typed(ui, "\n")
        typed(ui, "\n")
        self.assertEqual(ui.asking["text"], "Fixed the printers [3]")  # the recap's summary to start from
        typed(ui, "\n")
        self.assertIn("Created", ui.msg)
        self.assertEqual(len(list((self.root / "printer-fix" / "handoffs").iterdir())), 1)
        self.assertEqual(roost.read_recaps(), [])

    def test_notes_never_replace_each_other(self):
        self.unlist()
        roost.NOTES.mkdir(parents=True)
        (roost.NOTES / "printer-fix.md").write_text("someone else's note\n")
        path, _ = self.note()
        self.assertEqual(path.name, "printer-fix-2.md")
        self.assertEqual((roost.NOTES / "printer-fix.md").read_text(), "someone else's note\n")

    def test_pickup_and_starting_from_a_note_list_the_new_session_in_it(self):
        self.unlist()
        path, _ = self.note()
        other = "99999999-2222-3333-4444-555555555555"
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(roost.run_command("project", ["brief", "printer-fix", "--session", other]), 0)
        self.assertIn("Session recap printer-fix", out.getvalue())
        self.assertIn(other, roost.read_recaps()[0]["sessions"])
        saved = roost.plugin_args
        roost.plugin_args = lambda: []
        try:
            started = roost.start_session("printer-fix", "/w", None)
        finally:
            roost.plugin_args = saved
        self.assertEqual(started["start"][-1], "/roost:pickup printer-fix")
        self.assertEqual(len(roost.read_recaps()[0]["sessions"]), 3)
        self.assertEqual(roost.read_recaps()[0]["summary"], "Fixed the printers [3]")  # the rest of the note is untouched

    def test_a_note_becomes_a_project_with_its_handoffs_and_sessions(self):
        self.unlist()
        path, _ = self.note()
        self.transcript(TRANSCRIPT + [{"type": "user", "timestamp": "2026-10-09T12:00:00.000Z", "message": {"content": "again"}}])
        roost.write_handoff(SID, "### What I did\n- Second part", now=self.at(17))
        folder = roost.promote_recap(path, "printer-rollout", "Roll the new driver out to every Mac.")
        self.assertFalse(path.exists())
        handoffs = sorted((folder / "handoffs").iterdir())
        self.assertEqual([h.name for h in handoffs], ["2026-10-09-1600-11111111.md", "2026-10-09-1700-11111111.md"])
        meta = roost.frontmatter(handoffs[1].read_text())
        self.assertEqual((meta["session"], meta["name"], meta["to"]), (SID, "Printer fix", "2026-10-09T12:00:00.000Z"))
        self.assertIn("- Second part", handoffs[1].read_text())
        self.assertEqual(roost.read_project(folder)["sessions"], [SID])
        self.assertEqual(roost.owner([SID]), folder)
        self.assertEqual(roost.last_covered(folder, [SID]), "2026-10-09T12:00:00.000Z")  # the ledger carries on
        self.assertIn("2026-10-09-1700-11111111.md", roost.ledger(folder))
        with self.assertRaisesRegex(ValueError, "already exists"):
            roost.RECAPS.mkdir(exist_ok=True)
            (roost.RECAPS / "x.md").write_text("---\nsummary: x\n---\n\nHand-written.\n")
            roost.promote_recap(roost.RECAPS / "x.md", "printer-rollout", "Again.")
        self.assertTrue((roost.RECAPS / "x.md").exists())  # a failure keeps the note

    def test_a_crafted_marker_writes_nothing_outside_the_project(self):
        roost.RECAPS.mkdir()
        mark = "<!-- roost handoff %s session: s1 from:  to:  -->"
        (roost.RECAPS / "evil.md").write_text("\n".join(["---", "summary: x", "---", "",
                                                         mark % "2026-10-09-1600-ok", "Fine.", "",
                                                         mark % "../../../escaped", "Out.", "", mark % "..", "Dots.", ""]))
        folder = roost.promote_recap(roost.RECAPS / "evil.md", "evil", "Crafted.")
        self.assertEqual([h.name for h in (folder / "handoffs").iterdir()], ["2026-10-09-1600-ok.md"])
        self.assertFalse(list(folder.parent.parent.rglob("escaped.md")))
        self.assertEqual([s[0] for s in roost.recap_sections(mark % "a/b" + "\nx")], [])

    def test_a_hand_written_note_becomes_one_handoff(self):
        roost.RECAPS.mkdir()
        (roost.RECAPS / "vpn.md").write_text("---\nsummary: VPN client\n---\n\nInstalled the VPN client on two Macs.\n")
        folder = roost.promote_recap(roost.RECAPS / "vpn.md", "vpn-rollout", "VPN client everywhere.")
        [h] = (folder / "handoffs").iterdir()
        self.assertTrue(roost.handoff_time(h))
        self.assertIn("Installed the VPN client on two Macs.", h.read_text())

    def test_make_it_a_project_asks_for_an_id_and_summary_then_promotes_it(self):
        self.unlist()
        self.note()
        ui = object.__new__(roost.UI)
        s = dict(session("Printer fix", state="closed", kind=None), id=SID)
        ui.sessions, ui.msg, ui.page, ui.last_click = [s], "", 10, (0, -1)
        ui.psel, ui.ptop, ui.prow_y, ui.asking = 0, 0, {}, None
        ui.pquery, ui.searching, ui.term, ui.plugin_ok = "", False, None, True
        ui.folded, ui.expanded = set(), set()
        ui.unwrapped = set()
        ui.choosing = ui.confirm = None
        ui.summaries = {}
        ui.load_projects()
        self.assertEqual(ui.project_rows()[-1].kind, "new")  # recaps aren't on the Projects tab
        self.assertEqual([(n["id"], n["tags"]) for n in ui.notes], [("printer-fix", ["recap"])])  # it's a note, tagged recap
        self.assertEqual(ui.recap_of(s)["id"], "printer-fix")
        acts = dict(ui.session_actions(s))
        self.assertTrue(any(l.startswith("Read its Session recap") for l in acts))
        self.assertNotIn("New project from this Session…", acts)
        acts["Make it a project… (its recap's Handoffs move in)"]()
        self.assertEqual(ui.asking["text"], "Printer fix")
        typed(ui, "\n")
        self.assertEqual(ui.asking["text"], "printer-fix")
        typed(ui, "\n")
        self.assertEqual(ui.asking["text"], "Fixed the printers [3]")
        typed(ui, "\n")
        self.assertIn("Created", ui.msg)
        self.assertTrue((self.root / "printer-fix" / "handoffs").is_dir())
        self.assertEqual(roost.read_recaps(), [])


if __name__ == "__main__":
    unittest.main()
