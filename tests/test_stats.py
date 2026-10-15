from support import *  # noqa: F401,F403 (the shared namespace, helpers, and fixtures)
from support import _SCRATCH, own_tests  # noqa: F401

load_tests = own_tests(__name__)


class Stats(unittest.TestCase):
    TABLE = {"fetched": "t1", "standard": {"claude-sonnet-5-5": [(None, (3, 3.75, 6, 0.3, 15))],
                                           "claude-haiku-4-5": [(None, (1, 1.25, 2, 0.1, 5))]}}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        import unittest.mock
        root = Path(self.tmp.name)
        patcher = unittest.mock.patch.multiple(roost, PROJECTS=root / "projects", SPEND_LOG=root / "spend.jsonl",
                                               SPEND_CACHE=root / "cache.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        (root / "projects" / "-x").mkdir(parents=True)

    def reply(self, mid, model="claude-sonnet-5-5", ts="2026-10-10T15:00:00Z", out=1000):
        return {"type": "assistant", "timestamp": ts, "message": {"id": mid, "model": model, "content": [],
                "usage": {"input_tokens": 0, "output_tokens": out, "cache_read_input_tokens": 0}}}

    def user(self, text):
        return {"type": "user", "message": {"role": "user", "content": text}}

    def transcript(self, name, recs):
        path = Path(self.tmp.name) / "projects" / "-x" / f"{name}.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in recs))
        return path

    def test_replies_after_wrap_up_count_as_wrap_ups_until_the_next_prompt(self):
        path = self.transcript("s1", [self.user("fix it"), self.reply("a"),
                                      self.user("<command-name>/roost:wrap-up</command-name>"), self.reply("b"),
                                      self.user("thanks, next thing"), self.reply("c")])
        got = roost.transcript_spend(path, self.TABLE)
        self.assertEqual([(r[0], r[2]) for r in got["replies"]], [("a", 0), ("b", 1), ("c", 0)])
        self.assertAlmostEqual(got["replies"][0][3], 0.015)

    def test_a_bedrock_model_id_is_priced_like_the_anthropic_one(self):
        u = {"input_tokens": 0, "output_tokens": 1000}
        self.assertEqual(roost.reply_cost("us.anthropic.claude-haiku-4-5-20251001-v1:0", u, self.TABLE),
                         roost.reply_cost("claude-haiku-4-5", u, self.TABLE))

    def test_gather_counts_a_repeated_reply_once_and_adds_the_spend_log(self):
        self.transcript("s1", [self.reply("a"), self.reply("b")])
        self.transcript("s2", [self.reply("a")])  # a resumed Session repeats it
        roost.log_spend("Ask", 0.5, session="s1", now=1760108400)
        roost.log_spend("Ask", 0)  # nothing spent, nothing logged
        got = roost.gather_spend(self.TABLE)
        self.assertEqual(sorted(r[1] for r in got["rows"]), ["Ask", "Live work", "Live work"])
        self.assertAlmostEqual(sum(r[4] for r in got["rows"]), 0.53)
        self.assertEqual(roost.gather_spend(self.TABLE)["rows"], got["rows"])  # read again from the cache
        hourly = sum(sum(v) for kinds in got["hours"].values() for v in kinds.values())
        self.assertAlmostEqual(hourly, 0.53)  # the same spend, hour by hour, for the Today chart

    def test_the_view_totals_a_range_and_paces_the_month(self):
        from datetime import date
        rows = [("2026-10-01", "Live work", "s1", None, 10.0), ("2026-10-10", "Wrap-ups", "s1", None, 2.0),
                ("2026-10-10", "Adopt", None, "printer", 1.0), ("2026-09-30", "Live work", "s2", None, 5.0)]
        data = {"rows": rows, "tokens": {"2026-10-10": [80, 100]}, "unpriced": [], "priced": True}
        with unittest.mock.patch.object(roost, "monthly_limit", lambda: None):
            v = roost.spend_view(data, "Month to date", {"s1": "printer"}, {"s1": "Printer fix"}, date(2026, 10, 10))
            week = roost.spend_view(data, "This week", {}, {}, date(2026, 10, 10))
        self.assertEqual(v["total"], 13.0)
        self.assertEqual(v["projects"], [("printer", 13.0)])
        self.assertAlmostEqual(v["pace"], 13.0 / 10 * 31)
        self.assertEqual(v["top_session"], ("Printer fix", 12.0))
        self.assertEqual(v["cached"], 0.8)
        self.assertEqual(week["total"], 3.0)  # Monday Oct 5 on
        self.assertEqual(week["chart_labels"], ["Oct 5", "Oct 6", "Oct 7", "Oct 8", "Oct 9", "Oct 10"])  # its own days
        self.assertEqual(v["chart_labels"][0], "Oct 1")
        self.assertEqual(v["chart"]["Live work"][0], 10.0)
        everything = roost.spend_view(data, "All time", {}, {}, date(2026, 10, 10))
        self.assertEqual(everything["chart_labels"][0], "Sep 30")

    def test_today_is_charted_by_the_hour_up_to_now(self):
        from datetime import date
        data = {"rows": [("2026-10-10", "Live work", "s1", None, 3.0)], "tokens": {}, "unpriced": [], "priced": True,
                "hours": {"2026-10-10": {"Live work": [0.0] * 9 + [1.0, 2.0] + [0.0] * 13}}}
        with unittest.mock.patch.object(roost, "monthly_limit", lambda: None):
            v = roost.spend_view(data, "Today", {}, {}, date(2026, 10, 10), hour=14)
        self.assertEqual(v["chart_labels"], [f"{h:02d}:00" for h in range(15)])
        self.assertEqual(v["chart"]["Live work"][9:11], [1.0, 2.0])
        self.assertEqual(v["chart"]["Wrap-ups"], [0.0] * 15)

    def test_streaks_end_yesterday_while_today_is_young(self):
        from datetime import date
        days = ["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-08", "2026-10-09"]
        self.assertEqual(roost.streaks(days, date(2026, 10, 10)), (2, 3))


# ---------- Projects rows: the NAME first (dates, a two-line SESSIONS cell, and the column widths) ----------


if __name__ == "__main__":
    unittest.main()
