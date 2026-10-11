"""Claude Code Sessions: reading transcripts and jobs, pins, starting, stopping, and sorting them."""
import json, os, re, shutil, subprocess, time
from datetime import datetime
from pathlib import Path

from roost.core.config import (
    AGENT_KEYS,
    HOME,
    RECENT_HOURS,
    STATE_ORDER,
    TESTED_CLAUDE,
    TITLE_KEYS,
    VIEWS,
    version_tuple,
)
from roost.core.formatting import short
from roost.core import config


# ---------- data ----------

def live_sessions():
    try:
        out = subprocess.run(["claude", "agents", "--json"], stdin=subprocess.DEVNULL, capture_output=True,
                             text=True, timeout=15).stdout
        # Background entries with no worker process are leftovers from a retired
        # session; the roster keeps listing them, so drop them here.
        return {a["sessionId"]: a for a in json.loads(out)
                if not (a.get("kind") == "background" and not a.get("pid"))}
    except Exception:
        return {}


def compat_warning(version_out, agents):
    """A short warning when Claude Code looks different from what roost was built against, else ''."""
    m = re.search(r"\d+\.\d+\.\d+", version_out or "")
    if not m:
        return "Couldn't read the Claude Code version"
    missing = sorted(AGENT_KEYS - set().union(*(set(a) for a in agents))) if agents else []
    if missing:
        return f"Claude Code {m.group()} changed its session list (missing {', '.join(missing)}); states may be wrong"
    if version_tuple(m.group()) > version_tuple(TESTED_CLAUDE):
        return f"Claude Code {m.group()} is newer than roost was tested with ({TESTED_CLAUDE})"
    return ""


def check_compat():
    try:
        ver = subprocess.run(["claude", "--version"], stdin=subprocess.DEVNULL, capture_output=True,
                             text=True, timeout=10).stdout
        raw = subprocess.run(["claude", "agents", "--json"], stdin=subprocess.DEVNULL, capture_output=True,
                             text=True, timeout=15).stdout
        warning = compat_warning(ver, json.loads(raw or "[]"))
    except Exception as e:
        warning = f"Couldn't check Claude Code: {e}"
    if warning:
        config.log(f"compat: {warning}")
    # a newer version alone changes nothing roost reads (Claude Code ships almost daily), so it's only logged
    return "" if " is newer than roost was tested with " in warning else warning


def stamp(iso):
    """Transcript timestamps ("2026-10-06T11:30:53.123Z") as epoch seconds."""
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return None


def parse(path):
    s = {"id": path.stem, "ids": [path.stem], "name": None, "ai_title": None, "first": None,
         "summary": "", "tokens": 0, "files": [], "cwd": None, "home": None, "entrypoint": None,
         "started": None, "reply_at": None, "active": None, "ttl": None, "compacted": None}
    ids, files = {path.stem}, {}
    with open(path, errors="replace") as fh:
        for line in fh:
            if '"turn_duration"' in line:
                s["turn_done"] = True
                continue
            if ('"type":"assistant"' not in line and '"cwd"' not in line and '"type":"user"' not in line
                    and '"compact_boundary"' not in line and not any(k in line for k in TITLE_KEYS)):
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("sessionId"):
                ids.add(rec["sessionId"])
            if s["first"] is None and rec.get("type") == "user" and rec.get("uuid"):
                s["first"] = rec["uuid"]
            if s["entrypoint"] is None and rec.get("entrypoint"):
                s["entrypoint"] = rec["entrypoint"]  # cli, claude-desktop, sdk-cli
            if s["started"] is None and rec.get("timestamp"):
                s["started"] = stamp(rec["timestamp"])
            if rec.get("cwd") and not rec.get("isSidechain"):
                s["cwd"] = rec["cwd"]  # where it last worked; shown in the table
                # Where it was started. Resume there: a session that cd'd into a
                # subfolder would otherwise reopen without the launch folder's
                # .claude/settings.local.json (MCP approvals, output style).
                s["home"] = s["home"] or rec["cwd"]
            t = rec.get("type")
            if t in ("user", "assistant") and not rec.get("isSidechain") and not rec.get("isMeta") and rec.get("timestamp"):
                s["active"] = stamp(rec["timestamp"]) or s["active"]  # a message, not just the file being touched
            if t == "agent-name":
                s["name"] = rec.get("agentName") or s["name"]
            elif t == "custom-title":
                s["name"] = rec.get("customTitle") or s["name"]
            elif t == "ai-title":
                s["ai_title"] = rec.get("aiTitle")
            elif t == "system" and rec.get("subtype") == "compact_boundary" and not rec.get("isSidechain"):
                # /compact (or auto-compact): the context is the summary now. Until the next reply,
                # the last reply's usage still says the old size, and its cache no longer matters.
                post = (rec.get("compactMetadata") or {}).get("postTokens")
                if post:
                    s["tokens"], s["compacted"] = post, stamp(rec.get("timestamp")) or True
            elif t == "assistant" and not rec.get("isSidechain"):
                s["turn_done"] = False
                msg = rec.get("message") or {}
                u = msg.get("usage") or {}
                ctx = (u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                       + u.get("cache_creation_input_tokens", 0))
                if ctx:
                    s["tokens"], s["compacted"] = ctx, None
                    s["reply_at"] = stamp(rec.get("timestamp")) or s["reply_at"]
                made = u.get("cache_creation") or {}
                if made.get("ephemeral_1h_input_tokens"):
                    s["ttl"] = 3600
                elif made.get("ephemeral_5m_input_tokens"):
                    s["ttl"] = 300
                for c in msg.get("content") or []:
                    if c.get("type") == "text" and len(c.get("text", "").strip()) > 40:
                        s["summary"] = c["text"].strip()
                    elif c.get("type") == "tool_use" and c.get("name") in ("Edit", "Write", "NotebookEdit"):
                        inp = c.get("input") or {}
                        fp = inp.get("file_path") or inp.get("notebook_path")
                        if fp and not fp.startswith(("/private/tmp", "/tmp")):
                            files[fp] = None
    s["ids"], s["files"] = sorted(ids), list(files)
    s["name"] = s["name"] or s["ai_title"] or s["id"][:8]
    return s


def load(max_days):
    """Parse transcripts touched in the last max_days, reusing cached results."""
    try:
        cache = json.loads(config.INDEX.read_text())
    except Exception:
        cache = {}
    cutoff = time.time() - max_days * 86400
    out, fresh, changed = [], {}, False
    for path in config.PROJECTS.glob("*/*.jsonl"):
        if "-private-tmp-" in path.parent.name:
            continue
        st = path.stat()
        if st.st_mtime < cutoff:
            continue
        key = str(path)
        hit = cache.get(key)
        if hit and hit["mtime"] == st.st_mtime and hit["size"] == st.st_size:
            s = hit["s"]
        else:
            s, changed = parse(path), True
        fresh[key] = {"mtime": st.st_mtime, "size": st.st_size, "s": s}
        if s["tokens"]:
            out.append(dict(s, last=st.st_mtime))
    if changed or len(fresh) != len(cache):
        try:
            config.INDEX.parent.mkdir(parents=True, exist_ok=True)
            config.INDEX.write_text(json.dumps(fresh))
        except OSError:
            pass
    return out


def read_pins():
    try:
        return set(json.loads(config.PINS.read_text()))
    except Exception:
        return set()


def set_pin(job_id, pinned):
    """Pin or unpin a background session the way `claude agents` does (Ctrl+T).
    The background service skips pinned sessions when closing idle ones."""
    lock = Path(str(config.PINS) + ".lock")  # same lock Claude Code takes on this file
    for _ in range(50):
        try:
            lock.mkdir()
            break
        except FileExistsError:
            if time.time() - lock.stat().st_mtime > 10:
                lock.rmdir()  # stale lock left by a crashed writer
            time.sleep(0.1)
    else:
        raise RuntimeError("pins file is busy")
    try:
        pins = [p for p in json.loads(config.PINS.read_text())] if config.PINS.exists() else []
        if pinned and job_id not in pins:
            pins.append(job_id)
        elif not pinned and job_id in pins:
            pins.remove(job_id)
            (config.JOBS / job_id / "order").unlink(missing_ok=True)
        tmp = config.PINS.with_suffix(".json.roost-tmp")
        tmp.write_text(json.dumps(pins, indent=2))
        os.replace(tmp, config.PINS)
    finally:
        lock.rmdir()


def launch_dir(s):
    for d in (s.get("home"), s.get("cwd")):
        if d and os.path.isdir(d):
            return d
    return HOME


def revive_pinned(s, name=None, pin=True):
    """Bring a closed session back as a background session and (by default) pin it.
    Resuming with --bg makes no model call; it waits idle for a prompt."""
    cwd = launch_dir(s)
    short_id = s["id"][:8]
    # A leftover job entry with no process makes Claude start an empty copy
    # under a new id instead of waking this session. `claude rm` clears the
    # entry and keeps the transcript.
    if (config.JOBS / short_id).exists():
        subprocess.run(["claude", "rm", short_id], stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
    r = subprocess.run(["claude", "--bg", "--resume", s["id"], "-n", name or s["name"]], stdin=subprocess.DEVNULL, cwd=cwd,
                       capture_output=True, text=True, timeout=60)
    # Claude prints "backgrounded · <id>" for a fresh start, "woke session <id>" when reusing one
    out = re.sub(r"\x1b\[[0-9;]*m", "", r.stdout + r.stderr)  # drop color codes
    m = re.search(r"(?:backgrounded\W+|woke session |attach )([0-9a-f]{8})", out)
    if not m:
        raise RuntimeError((r.stderr or r.stdout).strip().splitlines()[-1] if (r.stderr or r.stdout).strip() else "no id returned")
    if m.group(1) != short_id:
        # Claude made a copy rather than resuming: undo it rather than pin an empty session
        subprocess.run(["claude", "stop", m.group(1)], stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
        subprocess.run(["claude", "rm", m.group(1)], stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
        raise RuntimeError("Claude started a copy instead of this session, so it was undone")
    if pin:
        set_pin(short_id, True)
    return short_id


def transcript_path(s):
    return next(config.PROJECTS.glob(f"*/{s['id']}.jsonl"), None)


def rename_session(s, name):
    """Give a session a new name, the way Claude Code stores it.
    Closed: append a title record to its transcript (what /rename writes).
    Background: restart it under the new name (no model call), keeping its pin.
    Open in a window: only that window can rename it."""
    name = " ".join(name.split())
    if not name:
        raise RuntimeError("name can't be empty")
    if s["kind"] == "interactive":
        raise RuntimeError("it's open in a window: use /rename there")
    if s["state"] == "closed":
        path = transcript_path(s)
        if not path:
            raise RuntimeError("transcript not found")
        sid = next((i for i in s["ids"] if i.startswith(s["id"])), s["id"])
        with open(path, "a") as fh:
            fh.write(json.dumps({"type": "custom-title", "customTitle": name, "sessionId": sid}) + "\n")
        return
    if s["state"] == "busy":
        raise RuntimeError("it's working; rename it once it's idle")
    if s.get("attached"):
        raise RuntimeError("it's open in a window: use /rename there, or close the window first")
    pinned = bool(s.get("pinned"))
    stop_session(s["attach"])
    revive_pinned(s, name=name, pin=pinned)


def stop_session(job_id):
    """Stop a background session (same as `claude stop`) and drop its pin."""
    r = subprocess.run(["claude", "stop", job_id], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip() or "claude stop failed")
    if job_id in read_pins():
        set_pin(job_id, False)
    # Clear the finished entry (transcript is kept) so a later restart reuses this session
    subprocess.run(["claude", "rm", job_id], stdin=subprocess.DEVNULL, capture_output=True, timeout=30)


def delete_session(s):
    """Move a closed session's Claude Code data to the macOS Trash: its transcript,
    the folder beside it (subagent logs, tool output), file-edit history, and
    session env. Work folders are never touched."""
    claude_dir = Path.home() / ".claude"
    sid = next((i for i in s["ids"] if i.startswith(s["id"])), s["id"])
    targets = [p for p in config.PROJECTS.glob(f"*/{s['id']}.jsonl")]
    targets += [p for p in config.PROJECTS.glob(f"*/{s['id']}") if p.is_dir()]
    targets += [claude_dir / d / sid for d in ("file-history", "session-env") if (claude_dir / d / sid).exists()]
    if not targets:
        raise RuntimeError("nothing found to delete")
    short_id = s["id"][:8]
    if (config.JOBS / short_id).exists():
        subprocess.run(["claude", "rm", short_id], stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
    if short_id in read_pins():
        set_pin(short_id, False)
    trash = Path.home() / ".Trash" / f"claude-session-{s['id'][:8]}-{int(time.time())}"
    trash.mkdir(parents=True)
    for p in targets:
        shutil.move(str(p), str(trash / f"{p.parent.name}__{p.name}"))
    return trash


def attach_live(sessions, live):
    # A backgrounded session gets a new id with no transcript of its own, so
    # fall back to matching it by name against the newest transcript.
    # Exact id matches are claimed first so two transcripts sharing a name
    # can't trade places between refreshes.
    sessions.sort(key=lambda s: -s["last"])
    match = {}
    for s in sessions:
        sid = next((i for i in s["ids"] if i in live), None)
        if sid and sid not in match.values():
            match[s["id"]] = sid
    for s in sessions:
        if s["id"] not in match:
            sid = next((i for i, a in live.items() if i not in match.values()
                        and a.get("name") == s["name"]), None)
            if sid:
                match[s["id"]] = sid
    pins = read_pins()
    for s in sessions:
        agent = live.get(match.get(s["id"]))
        if agent:
            n = agent.get("name")
            # A revived unnamed session is labelled with its short id; keep ours
            if n and not re.fullmatch(r"[0-9a-f]{8}", n):
                s["name"] = n
        s["state"] = (agent.get("status") or agent.get("state") or "live") if agent else "closed"
        # Claude reports a session as busy while it watches an Artifact for
        # comments, even when its last turn finished long ago.
        if s["state"] == "busy" and s.get("turn_done") and time.time() - s["last"] > 120:
            s["state"] = "watching"
        if not agent and time.time() - s.get("opening", 0) < 20:
            s["state"] = "opening"
        s["kind"] = agent.get("kind") if agent else None
        s["attach"] = agent.get("id") if agent and agent.get("kind") == "background" else None
        s["pinned"] = bool(s["attach"]) and s["attach"] in pins
    # /bg can start a copy that holds only messages sent after the handoff; once
    # it's closed and reopened the earlier history is gone from it. Flag a running
    # session when a closed one with the same name carries far more context.
    closed_best = {}
    for s in sessions:
        if s["state"] == "closed" and s["tokens"] > closed_best.get(s["name"], {"tokens": 0})["tokens"]:
            closed_best[s["name"]] = s
    for s in sessions:
        best = closed_best.get(s["name"])
        s["partial"] = best["id"][:8] if (s["state"] != "closed" and best
                                          and s["tokens"] < best["tokens"] * 0.6) else None
    return sessions


def drop_copies(sessions):
    """Hide closed transcripts that are older copies of another one. Copies made
    by /bg or by a restart keep the original message ids, so the first user
    message identifies the conversation."""
    newest = {}
    for s in sessions:
        if s.get("first") and (s["first"] not in newest or s["last"] > newest[s["first"]]["last"]):
            newest[s["first"]] = s
    return [s for s in sessions if s["state"] != "closed" or not s.get("first")
            or newest[s["first"]] is s]


def regather(old):
    """Reload everything (transcripts are cached, so this is cheap) while keeping
    state that only lives in memory, like a window that's still opening."""
    opening = {s["id"]: s["opening"] for s in old if s.get("opening")}
    sessions = load(90)
    for s in sessions:
        if s["id"] in opening:
            s["opening"] = opening[s["id"]]
    return drop_copies(attach_live(sessions, live_sessions()))


# Settings files that can set retention, highest priority first (managed settings override the user's)
RETENTION_SETTINGS = [Path("/Library/Application Support/ClaudeCode/managed-settings.json"),
                      Path.home() / ".claude" / "settings.json"]


def retention():
    """(cleanupPeriodDays, desktopSessionCleanupPeriodDays) as Claude Code applies them.
    Transcripts are deleted that many days after they were last written; the default is 30.
    Sessions started or last continued in Claude Desktop are kept at any age unless the
    desktop setting is given."""
    days = desktop = None
    for path in RETENTION_SETTINGS:
        try:
            cfg = json.loads(path.read_text())
        except Exception:
            continue
        if days is None and isinstance(cfg.get("cleanupPeriodDays"), (int, float)):
            days = cfg["cleanupPeriodDays"]
        if desktop is None and isinstance(cfg.get("desktopSessionCleanupPeriodDays"), (int, float)):
            desktop = cfg["desktopSessionCleanupPeriodDays"]
    return days or 30, desktop


def days_left(s, rules, now=None):
    """Days until Claude Code deletes a closed session's transcript; None for running sessions
    (the docs don't say how running ones are treated) and float('inf') when it's never deleted."""
    if s["state"] != "closed":
        return None
    days, desktop = rules
    if s.get("entrypoint") == "claude-desktop":
        if desktop is None:
            return float("inf")
        days = desktop
    return days - ((now or time.time()) - s["last"]) / 86400


def gather():
    return drop_copies(attach_live(load(90), live_sessions()))


# ---------- cache and staleness ----------
# Claude Code keeps a session's context in the prompt cache after each request. While it's warm,
# the next message reads it back at about 0.1x the input price; once it expires, the next message
# writes the whole context again: 2x on the 1-hour cache, 1.25x on the 5-minute one.
CACHE_WARN = 10 * 60      # seconds before going cold that a session is flagged
STALE_TOKENS = 150_000    # big enough that letting it sit and resuming it later is costly
STALE_DAYS = 2


def cache_left(s, now=None):
    """Seconds until the session's cache goes cold (0 or less: already cold), or None if it has no context."""
    if not s.get("tokens"):
        return None
    if s.get("state") == "busy":
        return s.get("ttl") or 3600  # every request refreshes it
    # A transcript without cache details predates them; assume the 1-hour cache Claude Code uses on subscriptions
    return (s.get("reply_at") or active(s)) + (s.get("ttl") or 3600) - (now or time.time())


def next_cost(s, now=None):
    """Price multiplier on the context for the next message: about 0.1 warm, 2 or 1.25 cold."""
    left = cache_left(s, now)
    if left is None:
        return None
    if left > 0:
        return 0.1
    return 1.25 if s.get("ttl") == 300 else 2.0


def stale(s, now=None):
    """Big, idle, and cold: its context should be wrapped up into files, not kept to resume."""
    now = now or time.time()
    return (s.get("tokens", 0) >= STALE_TOKENS and s.get("state") != "busy"
            and now - active(s) >= STALE_DAYS * 86400)


def active(s):
    """When someone last sent or got a message; older index entries only have the file time."""
    return s.get("active") or s["last"]


def ago(s, now=None):
    if s.get("state") == "busy":
        return "now"
    secs = (now or time.time()) - active(s)
    return "just now" if secs < 60 else f"{span(secs)} ago"


def span(secs):
    secs = int(secs)
    if secs < 3600:
        return f"{max(secs // 60, 1)}m"
    if secs < 86400:
        return f"{secs // 3600}h"
    return f"{secs // 86400}d"


def in_view(s, view, keep=()):
    """keep: Sessions that count as running while roost writes their Handoff or recap (it stopped
    them first), so they stay where the person started it."""
    label = VIEWS[view]
    running = s["state"] not in ("closed",) or s.get("id") in keep
    if label == "Running":
        return running
    if label == "Recently closed":
        return not running and s["last"] >= time.time() - RECENT_HOURS * 3600
    if label == "Closed":
        return not running
    if label == "Stale":
        return stale(s)
    return True


def matches(s, query):
    if not query:
        return True
    hay = " ".join([s["name"], s["summary"], short(s["cwd"]), *s["files"]]).lower()
    return all(q in hay for q in query.lower().split())


SECTIONS = ["Open", "Pinned", "Background", "Closed"]


def group(s):
    """Section index: open in a window, then pinned, then other background, then closed."""
    if s["kind"] == "interactive" or s["state"] == "opening" or s.get("attached"):
        return 0
    if s["kind"] == "background":
        return 1 if s.get("pinned") else 2
    return 3


# Sortable columns: header -> (sort key, newest/largest first by default)
SORTS = {
    "NAME": (lambda s: s["name"].lower(), False),
    "STATUS": (lambda s: STATE_ORDER.get(s["state"], 9), False),
    "LAST ACTIVE": (lambda s: active(s), True),
    "CONTEXT": (lambda s: s["tokens"], True),
    "PROJECT": (lambda s: (not s.get("proj"), s.get("proj") or ""), False),
}


def visible(sessions, view, query, sort=("LAST ACTIVE", True), keep=()):
    """Rows for a view: grouped into sections, then ordered by the chosen column inside each."""
    col, desc = sort
    rows = [s for s in sessions if in_view(s, view, keep) and matches(s, query)]
    rows.sort(key=lambda s: -s["last"])  # tiebreak: most recent first
    rows.sort(key=SORTS[col][0], reverse=desc)
    rows.sort(key=group)  # stable, so each section keeps the column order
    return rows
