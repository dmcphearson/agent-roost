"""What every test file shares: the roost namespace, scratch paths, and the helpers and fixtures. Demo
sessions and temp paths only; real sessions are never touched."""
import contextlib
import io
import json
import os
import re
import sys
import tarfile
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import roost as package


class Everything:
    """All of roost's modules as one namespace, so a test reads or replaces a name without knowing
    which module defines it. Replacing one sets it in its defining module, where every caller sees it."""
    def __init__(self):
        object.__setattr__(self, "_mods", package.modules())
        where = {}
        for m in self._mods:
            for name in vars(m):
                where.setdefault(name, m)
        object.__setattr__(self, "_where", where)

    def __getattr__(self, name):
        m = self._where.get(name)
        if m is None or name not in vars(m):
            raise AttributeError(name)
        return getattr(m, name)

    def __setattr__(self, name, value):
        for m in [self._where[name]] if name in self._where else self._mods:  # input(): every module's
            setattr(m, name, value)

    def __delattr__(self, name):
        for m in [self._where[name]] if name in self._where else self._mods:
            if name in vars(m):
                delattr(m, name)


roost = Everything()

RUNNING, ALL = roost.VIEWS.index("Running"), roost.VIEWS.index("All")
_SCRATCH = tempfile.TemporaryDirectory()
roost.NOTES, roost.RECAPS = Path(_SCRATCH.name) / "notes", Path(_SCRATCH.name) / "sessions"  # never the person's own
# nor their settings, pins, or installed files: a test that misses a stand-in writes here instead
for _name, _rel in [("CONFIG", "config.json"), ("PINS", "pins.json"), ("UPDATE_CHECK", "update-check.json"),
                    ("DONE", "_done"), ("ADOPT_PLANS", "adopt"), ("APP", "app"), ("SPEND_LOG", "spend.jsonl"),
                    ("SPEND_CACHE", "spend-cache.json"), ("LOG", "roost.log"), ("COMPAT_LOGGED", "compat-logged.json")]:
    setattr(roost, _name, Path(_SCRATCH.name) / _rel)
roost.config.DISMISSED = Path(_SCRATCH.name) / "dismissed.json"  # the dismissed Sessions' ids; never the person's own


def dismissed_file(path):
    """A temp file for config.DISMISSED, for one test; returns a function that reads what it holds."""
    import unittest.mock
    patcher = unittest.mock.patch.object(roost.config, "DISMISSED", path)
    patcher.start()
    return patcher, lambda: json.loads(path.read_text()) if path.exists() else []


def screen_text(app):
    """The screen as Textual draws it, a line of text per row."""
    return ["".join(seg.text for seg in strip).rstrip() for strip in app.screen._compositor.render_strips()]


def session(name, state="idle", kind="background", pinned=False, attached=False, mins=0, tokens=0, cwd="/tmp"):
    sid = f"{abs(hash(name)) % 16**8:08x}-test"
    return {"id": sid, "name": name, "state": state, "kind": kind, "pinned": pinned, "attached": attached,
            "attach": sid[:8] if kind == "background" else None, "last": time.time() - mins * 60,
            "tokens": tokens, "cwd": cwd, "summary": "", "files": []}


def act(ui, keys, rows, label):
    """Open the selected row's actions (enter) and pick the one whose label starts with label."""
    keys("enter", rows)
    opts = ui.choosing["options"]
    ui.choosing["sel"] = next(i for i, (l, _) in enumerate(opts) if l.startswith(label))
    return ui.keys_choice("enter")


def labels(ui, keys, rows):
    """The selected row's actions, as their labels."""
    keys("enter", rows)
    out = [l for l, _ in ui.choosing["options"]]
    ui.choosing = None
    return out


def names(text):
    """Each character of text as the key roost gets: a newline is enter, \\x1b escape, \\t tab."""
    return [roost.UI.CONTROL_KEYS.get(c, c) for c in text]


def typed(ui, text):
    """Type into the prompt at the bottom of the screen; a newline is enter."""
    out = None
    for k in names(text):
        out = ui.keys_ask(k)
    return out


README = """---
category: vendor-reviews
summary: "Two password managers compared: cost and rollout."
status: active
sessions:
  - name: Password Manager Review
    id: aa1b9a71-0000
  - name: Second look
    id: bb2c0000-0000
---

# Title
"""


SID = "11111111-2222-3333-4444-555555555555"
PRICE_PAGE = """# Pricing

## Model pricing

| Model | Base input tokens | 5m cache writes | 1h cache writes | Cache hits and refreshes | Output tokens |
| :---- | :---- | :---- | :---- | :---- | :---- |
| Claude Opus 5.5 | $4 / MTok | $5 / MTok | $8 / MTok | $0.20 / MTok<sup>2</sup> | $20 / MTok |
| Claude Opus 4.1 ([retired](https://x)) | $15 / MTok | $18.75 / MTok | $30 / MTok | $1.50 / MTok | $75 / MTok |
| Claude Haiku 5.5 (for prompts up to 100,000 tokens) | $0.10 / MTok | $0.125 / MTok | $0.20 / MTok | $0.01 / MTok | $0.50 / MTok |
| Claude Haiku 5.5 (for prompts over 100,000 tokens) | $0.50 / MTok | $0.625 / MTok | $1 / MTok | $0.05 / MTok | $2.50 / MTok |

## Feature-specific pricing

### Fast mode pricing

| Model | Input | Output |
| --- | --- | --- |
| Claude Opus 5.5 | $8 / MTok | $40 / MTok |
| Claude Opus 5 / Claude Opus 4.8 | $10 / MTok | $50 / MTok |

### Batch processing

| Model | Batch input | Batch output |
| --- | --- | --- |
| Claude Opus 5.5 | $2 / MTok | $10 / MTok |
"""


def reply(ts, mid, usage, content=(), model="claude-opus-5-5"):
    return {"type": "assistant", "timestamp": ts, "sessionId": SID, "cwd": "/w",
            "message": {"id": mid, "model": model, "usage": usage, "content": list(content)}}


def result(ts, tid, text, error=False, stdout=None):
    rec = {"type": "user", "timestamp": ts, "sessionId": SID, "cwd": "/w",
           "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "content": text, "is_error": error}]}}
    if stdout is not None:
        rec["toolUseResult"] = {"stdout": stdout, "stderr": ""}
    return rec


def bash(tid, cmd):
    return {"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": cmd}}


USAGE = {"input_tokens": 10, "output_tokens": 1000, "cache_read_input_tokens": 100_000,
         "cache_creation_input_tokens": 2000, "cache_creation": {"ephemeral_1h_input_tokens": 2000},
         "speed": "standard", "inference_geo": "global"}
TRANSCRIPT = [
    {"type": "custom-title", "customTitle": "Printer fix", "sessionId": SID},
    {"type": "user", "timestamp": "2026-10-09T10:00:00.000Z", "sessionId": SID, "cwd": "/w", "message": {"content": "go"}},
    # one reply split over two records: its usage counts once
    reply("2026-10-09T10:01:00.000Z", "m1", USAGE, [{"type": "text", "text": "ok"}]),
    reply("2026-10-09T10:01:01.000Z", "m1", USAGE, [
        {"type": "tool_use", "id": "e1", "name": "Edit", "input": {"file_path": "/w/printers.md"}},
        {"type": "tool_use", "id": "e2", "name": "Write", "input": {"file_path": "/tmp/scratch.txt"}},
        bash("b1", "make test\nsecond line"), bash("b2", "git commit -m 'Fix printers'")]),
    result("2026-10-09T10:02:00.000Z", "b1", "Exit code 2\nFAILED", error=True),
    result("2026-10-09T10:02:01.000Z", "b2", "", stdout="[main abc1234def] Fix printers\n 1 file changed"),
    {"type": "pr-link", "prUrl": "https://github.com/o/r/pull/7", "timestamp": "2026-10-09T10:03:00.000Z"},
    {"type": "pr-link", "prUrl": "https://github.com/o/r/pull/7", "timestamp": "2026-10-09T10:03:01.000Z"},
    reply("2026-10-09T11:00:00.000Z", "m2", dict(USAGE, speed="fast"), [
        {"type": "tool_use", "id": "e3", "name": "Edit", "input": {"file_path": "/w/later.md"}}]),
]


def release_tarball(version, extra=()):
    """A GitHub-style release tarball: everything under one top folder, the plugin in plugin/."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        def add(name, data=b"", kind=tarfile.REGTYPE, link=""):
            info = tarfile.TarInfo(name); info.type = kind; info.linkname = link; info.size = len(data)
            tar.addfile(info, io.BytesIO(data) if kind == tarfile.REGTYPE else None)
        top = "dmcphearson-agent-roost-abc123"
        add(f"{top}/", kind=tarfile.DIRTYPE)
        add(f"{top}/bin/roost", b"#!/usr/bin/env python3\n")
        add(f"{top}/plugin/.claude-plugin/plugin.json", json.dumps({"name": "roost", "version": version}).encode())
        add(f"{top}/plugin/skills/pickup/SKILL.md", b"---\nname: pickup\n---\n")
        for e in extra:
            add(*e)
    return buf.getvalue()


DRAFT = """### What I did
- Fixed the printers [3]
### What worked
- Restarting them [4, 5]
### What failed and why
- make test failed: missing driver [6]
### Decisions
- None.
### Open questions
- None.
### Next
- Check the second office [state]"""


PLAN_BODY = """## Summary
Roll the new printer driver out to every office Mac.
## Status
active
## Moves
- `export.csv` -> `data/export.csv`
- `report.pdf` -> `out/report.pdf`
## Archive
- `notes-backup.md`: an older copy of notes.md
## Split out
- `badge-printers/` -> `badge-printers`: a separate rollout with its own finish line
## First Handoff
from: `HANDOFF.md`
### What I did
- Packaged the driver
### What worked
- None.
### What failed and why
- Nothing.
### Decisions
- None.
### Open questions
- None.
### Next
- Deploy to the second office
## README
# Printer rollout

## Current state

Driver packaged; first office done.

## Next

Deploy to the second office.

## Key decisions

## Files

- `notes.md`: working notes
"""


import importlib
import locale
FMT = importlib.import_module("roost.core.formatting")  # date_order is patched where it's defined
TIME_TODAY = re.compile(r"^(1[0-2]|[1-9]):[0-5]\d (AM|PM)$")
SHORT_DATE = re.compile(r"\d\d/\d\d/\d\d")


def clock(ts):
    """A time today as the spec writes it: 12-hour, no leading zero, English AM/PM."""
    d = datetime.fromtimestamp(ts)
    return f"{d.hour % 12 or 12}:{d:%M} {'AM' if d.hour < 12 else 'PM'}"


def own_tests(name):
    """A test file's load_tests: only the classes it defines, not the ones it imports for their
    helpers (those run in their own file)."""
    def load_tests(loader, tests, pattern):
        mod = sys.modules[name]
        suite = unittest.TestSuite()
        for value in vars(mod).values():
            if isinstance(value, type) and issubclass(value, unittest.TestCase) and value.__module__ == name:
                suite.addTests(loader.loadTestsFromTestCase(value))
        return suite
    return load_tests
