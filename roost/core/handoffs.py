"""Handoffs and the ledger: facts from a transcript, redaction, and writing them."""
import json, os, re, subprocess, sys, time, uuid
from datetime import datetime
from pathlib import Path

from roost.core.sessiondata import parse, stamp
from roost.core.projectdata import RECAP_TAG, find_project, handoff_time, link_session, recap_sections, worked
from roost.core.notedata import new_file
from roost.core.pricing import reply_cost
from roost.core.formatting import clip, redact, short, slug
from roost.core.mdfiles import frontmatter, write_lines, yaml_text
from roost.core import config
from roost.core import projectdata
from roost.core import pricing


# ---------- Handoffs and the ledger ----------
# A Handoff is one file in a project's handoffs/, written once by `roost handoff` and never edited:
# facts roost's code reads from the Session's transcript, then the summary the model pipes in.

SHOWN_FACTS = 25        # longer lists in a Handoff end with "and N more"
COMMIT_LINE = re.compile(r"^\[(\S+)(?: \(root-commit\))? ([0-9a-f]{7,40})\] (.+)$", re.M)  # git commit's output
EXIT_CODE = re.compile(r"^(?:Error: )?Exit code (\d+)")
CD = re.compile(r"""^\s*cd\s+("[^"]+"|'[^']+'|[^\s;&|]+)\s*(?:&&|;)|\bgit\s+-C\s+("[^"]+"|'[^']+'|\S+)""")


def session_transcripts(sid):
    """A Session's transcript, then its subagents' transcripts; [] if it has none."""
    main = next(config.PROJECTS.glob(f"*/{sid}.jsonl"), None)
    return [main] + sorted((main.parent / sid / "subagents").glob("*.jsonl")) if main else []


def result_text(content):
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
    return content if isinstance(content, str) else ""


def scratch(path):
    return path.startswith(("/tmp/", "/private/tmp/")) or ("/.claude/jobs/" in path and "/tmp/" in path)


def command_repo(cmd, cwd):
    """The folder a shell command ran git in: its leading cd or git -C, else where the Session was."""
    m = CD.search(cmd)
    d = (m.group(1) or m.group(2)).strip("\"'") if m else cwd
    return os.path.expanduser(d) if d else None


def quiet_commits(repo, start, end):
    """Commits made in a repo while a command ran, for `git commit -q`, which prints nothing:
    as ("in <repo>", short sha, subject)."""
    try:
        r = subprocess.run(["git", "-C", repo, "log", "--all", "--reverse", "--format=%h%x09%s",
                            f"--since=@{int(start) - 1}", f"--until=@{int(end) + 1}"],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    where = f"in {Path(repo).name}"
    return [(where,) + tuple(l.split("\t", 1)) for l in r.stdout.splitlines() if "\t" in l] if r.returncode == 0 else []


def facts(sid, since=None, table=None):
    """What a Session did after `since` (an ISO timestamp; None for all of it), read from its
    transcript and its subagents'. The same transcripts, since, and prices give the same facts."""
    paths = session_transcripts(sid)
    if not paths:
        raise ValueError(f"there's no transcript for Session {sid}")
    f = {"session": sid, "name": parse(paths[0])["name"], "from": None, "to": None, "models": {},
         "cost": 0.0, "unpriced": [], "files": {}, "failed": [], "commits": [], "prs": {}}
    tokens = {"input": 0, "cache write": 0, "cache read": 0, "output": 0}
    for path in paths:
        replies, commands = {}, {}
        with open(path, errors="replace") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                ts = rec.get("timestamp")
                if not ts or (since and ts <= since):
                    continue
                t = rec.get("type")
                if t in ("user", "assistant"):
                    f["from"] = min(f["from"] or ts, ts)
                    f["to"] = max(f["to"] or ts, ts)
                if t == "pr-link" and rec.get("prUrl"):
                    f["prs"][rec["prUrl"]] = None
                elif t == "assistant":
                    msg = rec.get("message") or {}
                    replies[msg.get("id") or rec.get("uuid")] = msg  # one reply can span several records
                    for c in msg.get("content") or []:
                        if not isinstance(c, dict) or c.get("type") != "tool_use":
                            continue
                        inp = c.get("input") or {}
                        if c.get("name") in ("Edit", "Write", "NotebookEdit"):
                            fp = inp.get("file_path") or inp.get("notebook_path")
                            if fp and not scratch(fp):
                                f["files"][short(fp)] = None
                        elif c.get("name") == "Bash":
                            commands[c.get("id")] = (inp.get("command") or "", ts, rec.get("cwd"))
                elif t == "user":
                    for c in (rec.get("message") or {}).get("content") or []:
                        if not isinstance(c, dict) or c.get("type") != "tool_result" or c.get("tool_use_id") not in commands:
                            continue
                        (cmd, began, cwd), text = commands[c["tool_use_id"]], result_text(c.get("content"))
                        code = EXIT_CODE.match(text)
                        if c.get("is_error") and code:
                            first = cmd.strip().splitlines()[0] if cmd.strip() else ""
                            f["failed"].append((clip(first, 100), int(code.group(1))))
                        elif not c.get("is_error") and "git commit" in cmd:
                            out = (rec.get("toolUseResult") or {}).get("stdout") if isinstance(rec.get("toolUseResult"), dict) else None
                            made = [(f"on {b}", sha, msg) for b, sha, msg in COMMIT_LINE.findall(out or text)]
                            if not made and command_repo(cmd, cwd) and stamp(began) and stamp(ts):
                                made = quiet_commits(command_repo(cmd, cwd), stamp(began), stamp(ts))
                            f["commits"] += [m for m in made if m not in f["commits"]]
        for msg in replies.values():
            u, model = msg.get("usage") or {}, msg.get("model")
            if not u or not model or model.startswith("<"):
                continue  # <synthetic> replies are Claude Code's own, not the model's
            f["models"][model] = f["models"].get(model, 0) + 1
            made = u.get("cache_creation_input_tokens", 0)
            for k, v in (("input", u.get("input_tokens", 0)), ("cache write", made),
                         ("cache read", u.get("cache_read_input_tokens", 0)), ("output", u.get("output_tokens", 0))):
                tokens[k] += v
            cost = reply_cost(model, u, table)
            if cost is None:
                f["unpriced"] = sorted(set(f["unpriced"]) | {model})
            else:
                f["cost"] += cost
    f["tokens"], f["files"], f["prs"] = tokens, list(f["files"]), list(f["prs"])
    return f


def tok(n):
    return f"{n:,}" if n < 1000 else f"{n / 1000:.0f}k" if n < 1_000_000 else f"{n / 1e6:.1f}M"


def iso_time(iso):
    return worked(stamp(iso)) if stamp(iso) else iso


def listed(title, items, fmt):
    if not items:
        return [f"- **{title}:** none"]
    out = [f"- **{title} ({len(items)}):**"] + [f"  - {fmt(x)}" for x in items[:SHOWN_FACTS]]
    return out + ([f"  - and {len(items) - SHOWN_FACTS} more"] if len(items) > SHOWN_FACTS else [])


def facts_lines(f, table):
    when = f"{iso_time(f['from'])} – {iso_time(f['to'])}" if f["from"] else "no activity since its last Handoff"
    models = ", ".join(f"{m} ({n} repl{'y' if n == 1 else 'ies'})" for m, n in sorted(f["models"].items())) or "none"
    t = f["tokens"]
    if not f["models"]:
        cost = "none"
    elif not table:
        cost = "unknown: roost couldn't fetch Anthropic's prices"
    else:
        cost = (f"about ${f['cost']:.2f} at Anthropic's list prices of {worked(table['fetched'])}, before any discounts"
                + (f" (not counting {', '.join(f['unpriced'])}: no price listed)" if f["unpriced"] else ""))
    return (["## Facts", "",
             f"- **Session:** {f['name']} (`{f['session']}`)",
             f"- **When:** {when}",
             f"- **Model:** {models}",
             f"- **Tokens:** {tok(t['input'])} input · {tok(t['cache write'])} cache write · "
             f"{tok(t['cache read'])} cache read · {tok(t['output'])} output",
             f"- **Cost:** {cost}"]
            + listed("Files changed", f["files"], lambda p: f"`{p}`")
            + listed("Failed commands", f["failed"], lambda x: f"`{x[0]}` (exit {x[1]})")
            + listed("Commits", f["commits"], lambda x: f"`{x[1][:7]}` {x[0]}: {x[2]}")
            + listed("PRs", f["prs"], lambda u: u))


def owner(ids):
    """Where these Session ids leave Handoffs: the folder of the project that lists them, the file of
    the Session recap that does, or None. Raises ValueError when more than one lists them."""
    hits = [p for p in projectdata.read_all() + projectdata.read_recaps() if set(ids) & set(p["sessions"])]
    if len(hits) > 1:
        raise ValueError(f"more than one project or recap lists this Session ({', '.join(p['id'] for p in hits)}): "
                         "take it off all but one")
    return Path(hits[0]["path"]) if hits else None


# A Session recap holds its Handoffs one after another, each after a marker line that carries what a
# Handoff file's frontmatter would. Nothing above the end of the file is ever changed; turning the
# recap into a project splits the sections into Handoff files.


def first_point(summary):
    """A recap's one-line summary when none is given: the first point under What I did."""
    part = summary.split("### What I did", 1)[-1]
    line = next((l for l in part.splitlines() if l.strip().lstrip("-* ").strip() and not l.startswith("#")), "")
    return clip(" ".join(line.strip().lstrip("-* ").replace("**", "").split()), 200)


def last_covered(folder, ids):
    """Where this Session's newest Handoff in the project or recap stopped (its `to:`), or None."""
    ends = []
    if folder.is_file():
        ends = [to for _, sid, _, to, _ in recap_sections(folder.read_text(errors="replace")) if sid in ids and to]
        return max(ends) if ends else None
    for p in (folder / "handoffs").glob("*.md"):
        try:
            meta = frontmatter(p.read_text(errors="replace"))
        except OSError:
            continue
        if meta.get("session") in ids and isinstance(meta.get("to"), str) and meta["to"]:
            ends.append(meta["to"])
    return max(ends) if ends else None


def write_handoff(sid, summary, now=None, recap=False, recap_summary=None, at=None, roll_up=False):
    """Write a Handoff for a Session into the project or Session recap that lists it: the facts since
    its last Handoff, then the summary, with secrets redacted. With recap, a Session nothing lists gets
    a new Session recap: a note in ~/.roost/notes/, named after it and tagged recap. `at` dates the Handoff's file name (default now).
    With roll_up, a done sub-project's Session writes its roll-up into the parent project instead.
    Returns (path, redacted kinds). Raises ValueError with a reason, and never overwrites a Handoff."""
    summary = summary.strip()
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", sid or ""):
        raise ValueError(f"{sid!r} isn't a Session id (the full id, like {uuid.UUID(int=0)})")
    if not summary:
        raise ValueError("no summary on stdin: pipe the summary in (/wrap-up does this)")
    paths = session_transcripts(sid)
    if not paths:
        raise ValueError(f"there's no transcript for Session {sid}")
    info = parse(paths[0])
    ids = sorted({sid} | set(info["ids"]))
    folder = owner(ids)
    if folder is None and not recap:
        raise ValueError(f"no project lists Session {ids[0][:8]}, and no Session recap does, so it has nowhere to leave a "
                         "Handoff. /pickup <project> in the Session lists it (or roost project brief <project> --session <id>); "
                         "--recap writes a Session recap instead")
    if roll_up:
        return write_roll_up(folder, sid, summary, info, at or now)
    table = pricing.prices(now)
    f = facts(sid, last_covered(folder, ids) if folder else None, table)
    stem = f"{datetime.fromtimestamp(at or now or time.time()):%Y-%m-%d-%H%M}-{sid[:8]}"
    body = [f"# Handoff from {f['name']}", ""] + facts_lines(f, table) + ["", "## Summary", "", summary, ""]
    if folder is None or folder.is_file():
        return write_recap_handoff(folder, sid, f, stem, "\n".join(body), info, recap_summary or first_point(summary))
    text = "\n".join(["---", f"session: {sid}", f"name: {yaml_text(f['name'])}", f"from: {f['from'] or ''}",
                      f"to: {f['to'] or ''}", "---", ""] + body)
    text, kinds = redact(text)
    return new_handoff(folder, f"{stem}.md", text), kinds


def write_roll_up(folder, sid, summary, info, at=None):
    """A finished sub-project's roll-up: one Handoff in its parent project, so the parent learns what
    the sub-project did without carrying its history."""
    if folder is None or folder.is_file() or folder.parent.name != "sub":
        raise ValueError("--roll-up is for a sub-project's Session: this Session isn't listed in a sub-project")
    sub = projectdata.project_id(folder)
    if projectdata.read_project(folder)["status"] != "done":
        raise ValueError(f"{sub} isn't done: set it with roost project status {sub} done first")
    stem = f"{datetime.fromtimestamp(at or time.time()):%Y-%m-%d-%H%M}-{sid[:8]}-rollup-{folder.name}"
    text, kinds = redact("\n".join(["---", f"session: {sid}", f"name: {yaml_text(info['name'])}", f"rollup: {sub}", "---", "",
                                    f"# Roll-up of {sub}", "", "## Summary", "", summary, ""]))
    return new_handoff(folder.parent.parent, f"{stem}.md", text), kinds


def new_handoff(folder, name, text):
    """Write a Handoff file into folder's handoffs/, never over an existing one."""
    handoffs = folder / "handoffs"
    handoffs.mkdir(exist_ok=True)
    path = handoffs / name
    tmp = handoffs / f".{path.name}.roost-tmp"
    tmp.write_text(text)
    try:
        os.link(tmp, path)  # fails if the Handoff exists, so one is never overwritten
    except FileExistsError:
        raise ValueError(f"{short(str(path))} already exists, and roost never overwrites a Handoff: "
                         "try again in a minute") from None
    finally:
        tmp.unlink()
    return path


def write_recap_handoff(path, sid, f, stem, body, info, summary):
    """Add a Handoff to the end of a Session recap, or start a new recap with it when path is None."""
    section, kinds = redact(f"<!-- roost handoff {stem} session: {sid} from: {f['from'] or ''} to: {f['to'] or ''} -->\n\n{body}")
    if path is not None:
        text = path.read_text()
        if f"<!-- roost handoff {stem} " in text:
            raise ValueError(f"{short(str(path))} already has a Handoff {stem}, and roost never overwrites one: "
                             "try again in a minute")
        write_lines(path, [text.rstrip("\n") + "\n\n", section])
        return path, kinds
    title = " ".join(f["name"].split()) or sid[:8]
    summary, more = redact(summary or title)
    head = ["---", f"title: {yaml_text(title)}", f"created: {stem[:10]}", "project:", f"summary: {yaml_text(summary)}"] + (
        [f"folder: {yaml_text(info['home'] or info['cwd'])}"] if info.get("home") or info.get("cwd") else [])
    text = "\n".join(head + ["sessions:", f"  - name: {yaml_text(f['name'])}", f"    id: {sid}", f"tags: [{RECAP_TAG}]",
                             "---", "", f"# {title}", "", section])
    return new_file(config.NOTES, slug(title), text), kinds + more  # never replaces another note


def promote_recap(path, pid, summary, label=None):
    """Turn a Session recap into a project: a new project folder whose Handoffs are the recap's, listing
    the recap's Sessions. The recap is removed only after everything else is written."""
    path = Path(path)
    text = path.read_text(errors="replace")
    meta, sections = frontmatter(text), recap_sections(text)
    if not sections:  # a recap written by hand: it becomes one Handoff
        body = text.split("\n---", 1)[1].split("\n", 1)[-1].strip() if text.startswith("---") else text.strip()
        sid = next((x.get("id") for x in meta.get("sessions") or [] if isinstance(x, dict) and x.get("id")), "")
        sections = [(f"{datetime.fromtimestamp(path.stat().st_mtime):%Y-%m-%d-%H%M}-note", sid, "", "", body)]
    folder = projectdata.create_project(pid, summary, label)
    for stem, sid, frm, to, body in sections:
        name = (re.search(r"^# Handoff from (.+)$", body, re.M) or re.search(r"(.+)", path.stem)).group(1)
        (folder / "handoffs" / f"{stem}.md").write_text("\n".join(
            ["---", f"session: {sid}", f"name: {yaml_text(name)}", f"from: {frm}", f"to: {to}", "---", "", body, ""]))
    for x in meta.get("sessions") or []:
        if isinstance(x, dict) and x.get("id"):
            link_session(folder, x["id"], x.get("name") or x["id"][:8])
    path.unlink()
    return folder


def handoff_command(args):
    if sys.stdin.isatty():
        print("roost handoff: pipe the summary in on stdin, e.g. roost handoff --session <id> < summary.md",
              file=sys.stderr)
        return 1
    try:
        path, kinds = write_handoff(args.session, sys.stdin.read(), recap=args.recap, recap_summary=args.summary,
                                    roll_up=args.roll_up)
    except (ValueError, OSError) as e:
        print(f"roost handoff: {e}", file=sys.stderr)
        return 1
    if kinds:
        counts = ", ".join(f"{n} {k}" for k, n in sorted({k: kinds.count(k) for k in kinds}.items()))
        print(f"roost handoff: redacted {len(kinds)} secret{'s' if len(kinds) != 1 else ''} ({counts}); "
              "check the Handoff and rotate anything real", file=sys.stderr)
    print(f"Wrote {short(str(path))}")
    return 0


def handoff_flags(parser):
    parser.add_argument("--session", required=True, metavar="ID",
                        help="the Session's full id (in a Session: ${CLAUDE_SESSION_ID}); a project or Session recap must list it")
    parser.add_argument("--recap", "--note", dest="recap", action="store_true",
                        help="if nothing lists the Session, start a Session recap for it: a note in ~/.roost/notes/, tagged recap")
    parser.add_argument("--summary", metavar="TEXT", help="a new recap's one-sentence summary (default: its first point)")
    parser.add_argument("--roll-up", action="store_true",
                        help="a done sub-project's Session: write its roll-up into the parent project's handoffs/")


def ledger(folder):
    """Every Handoff's facts, oldest first: the project's timeline."""
    handoffs = sorted(p for p in (folder / "handoffs").glob("*.md") if handoff_time(p))
    if not handoffs:
        return f"# Ledger of {folder.name}\n\nNo Handoffs yet.\n"
    out = [f"# Ledger of {folder.name}: {len(handoffs)} Handoff{'s' if len(handoffs) != 1 else ''}, oldest first"]
    for p in handoffs:
        text = p.read_text(errors="replace")
        m = re.search(r"^## Facts\n(.*?)(?=^## |\Z)", text, re.S | re.M)
        roll_up = frontmatter(text).get("rollup")
        line = f"Roll-up of {roll_up}" if roll_up else "(No Facts section: not written by roost handoff.)"
        out += ["", f"## handoffs/{p.name}", "", m.group(1).strip() if m else line]
    return "\n".join(out) + "\n"


def ledger_command(args):
    try:
        target = find_project(args.project)
        if target.is_file():
            raise ValueError(f"{target.stem} is a Session recap or a note, which has no ledger")
        print(ledger(target), end="")
    except (ValueError, OSError) as e:
        print(f"roost ledger: {e}", file=sys.stderr)
        return 1
    return 0


def ledger_flags(parser):
    parser.add_argument("project", help="the project's folder name")
