"""roost adopt: planning and applying a project folder's move into roost's shape."""
import json, os, re, shlex, shutil, subprocess, sys, time
from datetime import datetime
from pathlib import Path

from roost.core.statsdata import log_spend
from roost.core.sessiondata import parse
from roost.core.projectdata import read_project, readme, session_name, set_description, set_status
from roost.core.pricing import reply_cost
from roost.core.handoffs import session_transcripts
from roost.core.summaries import entry_cut, summary_settings
from roost.core.formatting import redact, short
from roost.core.mdfiles import readme_lines, write_lines, yaml_text
from roost.core import config
from roost.core import pricing
from roost.core import summaries


# ---------- roost adopt ----------
# Brings an existing project folder into roost's project layout. One model call per project (no tools: roost
# hands it the folder's files) writes a plan as Markdown; the person reviews it in the terminal and
# can edit it; nothing changes without their yes. Applying moves every replaced or dropped file into
# _archive/<date>/ in the project, so nothing is deleted. The rename (no date prefix) is code's.

ADOPT_FILE_TEXT = 4000     # characters of each small text file the planner sees
ADOPT_TEXT_MAX = 50_000    # text files bigger than this are listed, not shown
ADOPT_TOTAL = 240_000      # characters of file text per plan, at most
ADOPT_OUT = 6000           # tokens a plan comes to, for the estimate
DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-")
TEXT_FILES = {".md", ".txt", ".csv", ".tsv", ".json", ".yaml", ".yml", ".py", ".sh", ".tf", ".hcl", ".html",
              ".js", ".ts", ".toml", ".xml", ".sql", ".ini", ".cfg", ".env.example"}
PLAN_HEADINGS = ["Rename", "Summary", "Status", "Moves", "Archive", "Split out", "First Handoff", "README"]

ADOPT_SYSTEM = """You plan how to bring one project folder into roost's shape. The person reads your
plan and approves it before anything changes, then acts on it as written.

What makes a plan succeed: after it, the README says where the project stands now, every file is
where it belongs, and nothing the person still needs has been moved out of the way.

What makes it fail: inventing facts about the project, archiving a file the person still works from,
or moving a working doc. Ambiguity is the death of success: when you aren't sure a file is a backup
or a deliverable, leave it where it is and say so in the README's Current state."""

ADOPT_PROMPT = """The project folder is below: its name, its README, every file with its size, and the
text of the small text files (long ones cut). Write the plan with exactly these sections, in this
order, and nothing before the first. Write "None." under an empty one.

## Summary
One sentence saying what the project is (keep the README's summary if it still fits).
## Status
One or two lowercase words: keep the README's status if it has one; otherwise active, blocked, or done.
## Moves
Only inputs (spreadsheets, exports, raw data) into data/, and finished deliverables (PDF, HTML, DOCX,
PPTX, XLSX made for someone) into out/. Working docs stay where they are. One line each:
- `old/path` -> `data/name`
## Archive
Only backup copies (a copy, "old", "backup", or "v1" of a file whose newer version is here). Don't
list the First Handoff's file: roost archives it with that step. One line each, with why:
- `path`: why
## Split out
A subfolder that is a separate piece of work with its own finish line, with a new project name
(lowercase, hyphens, no date). One line each: - `sub/` -> `new-name`: why
## First Handoff
If a file is a hand-written handoff, "start here", or session notes, turn it into a Handoff summary.
Its first line names the file, then the summary under these headings: ### What I did, ### What worked,
### What failed and why, ### Decisions (only the person's), ### Open questions, ### Next.
from: `path`
### What I did
…
## README
The new README body, everything after the frontmatter: the title line (# …), then ## Current state,
## Next, ## Key decisions (dated), and ## Files (every file after the moves, at its new path, one line
each saying what it is; leave out README.md, handoffs/, and archived files). Keep what the old README
says that still holds, in its words. Keep any other section the person wrote that still holds. Under
about 100 lines. Use only what the files show.

Never move or archive README.md, handoffs/, or _archive/. Paths are relative to the project folder.

=== PROJECT FOLDER ===
"""


def adopt_name(name):
    """The folder's name in roost's project layout: without its date prefix."""
    return DATE_PREFIX.sub("", name) or name


def adopt_input(folder):
    """What the planner sees of a folder: its name, README, files with sizes, and small text files."""
    files = [p for p in sorted(folder.rglob("*")) if p.is_file() and ".git" not in p.parts
             and not p.name.startswith(".") and "_archive" not in p.relative_to(folder).parts]
    readme = folder / "README.md"
    out = [f"Folder: {folder.name}", "", "README.md:", readme.read_text(errors="replace") if readme.exists() else "(none)", "",
           "Files:"] + [f"- {p.relative_to(folder)} ({p.stat().st_size:,} bytes)" for p in files]
    left = ADOPT_TOTAL
    for p in files:
        if p == readme or p.suffix.lower() not in TEXT_FILES or p.stat().st_size > ADOPT_TEXT_MAX or left <= 0:
            continue
        text = entry_cut(p.read_text(errors="replace"), ADOPT_FILE_TEXT)
        left -= len(text)
        out += ["", f"=== {p.relative_to(folder)} ===", text]
    return "\n".join(out)


def adopt_estimate(text, model, table):
    n = len(ADOPT_SYSTEM + ADOPT_PROMPT + text) // 3
    return reply_cost(model, {"cache_creation_input_tokens": n, "cache_creation": {"ephemeral_1h_input_tokens": n},
                              "output_tokens": ADOPT_OUT, "inference_geo": "us"}, table)


def adopt_plan_text(folder, body):
    """The plan file: the rename (code's), then what the model wrote."""
    return (f"# Adopt plan for {folder.name}\n\nEdit anything below, then save and close. Delete a line to drop that step.\n\n"
            f"## Rename\n\n{adopt_name(folder.name)}\n\n{body.strip()}\n")


def ticked(line, pattern):
    m = re.match(r"^\s*[-*]\s+" + pattern + r"\s*$", line)
    return m.groups() if m else None


def parse_plan(text):
    """A plan file as a dict. The README section runs to the end, so its own ## headings stay in it."""
    m = re.search(r"^## README[ \t]*$", text, re.M)
    head, readme = (text[:m.start()], text[m.end():]) if m else (text, None)
    parts, cur = {}, None
    for line in head.splitlines():
        m = re.match(r"^## (.+?)\s*$", line)
        if m and m.group(1) in PLAN_HEADINGS:
            cur = m.group(1)
            parts[cur] = []
        elif cur:
            parts[cur].append(line)
    body = lambda k: "\n".join(parts.get(k, [])).strip()
    none = lambda t: t.rstrip(".").lower() in ("", "none")
    first = None
    fh = body("First Handoff")
    if not none(fh):
        m = re.match(r"from:\s*`([^`]+)`\s*\n(.*)", fh, re.S)
        first = (m.group(1), m.group(2).strip()) if m else ("", fh)
    plan = {"rename": next((l.strip() for l in body("Rename").splitlines() if l.strip()), ""),
            "summary": " ".join(body("Summary").split()), "status": " ".join(body("Status").split()).lower().rstrip("."),
            "moves": [x for x in (ticked(l, r"`([^`]+)`\s*->\s*`([^`]+)`") for l in parts.get("Moves", [])) if x],
            "archive": [x for x in (ticked(l, r"`([^`]+)`\s*:?\s*(.*)") for l in parts.get("Archive", [])) if x],
            "split": [x for x in (ticked(l, r"`([^`]+?)/?`\s*->\s*`([^`]+)`\s*:?\s*(.*)") for l in parts.get("Split out", [])) if x],
            "first": first, "readme": readme.strip() if readme is not None else None}
    if first:  # the First Handoff's file is archived with that step, so an Archive line for it is dropped
        plan["archive"] = [x for x in plan["archive"] if x[0] != first[0]]
    return plan


def inside(folder, rel):
    """rel as a path in folder, or None if it would leave it."""
    p = (folder / rel).resolve()
    return p if folder.resolve() in p.parents else None


def plan_problems(folder, plan, taken=()):
    """Why a plan can't be applied as it stands; [] when it can."""
    out, used = [], set()
    root = folder.parent

    def claim(rel, what):
        p = inside(folder, rel)
        if p is None:
            out.append(f"{what} `{rel}` is outside the project folder")
        elif rel in used:
            out.append(f"`{rel}` is in more than one step")
        used.add(rel)
        return p
    name = plan["rename"]
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name or ""):
        out.append(f"Rename: {name!r} isn't a folder name (lowercase letters, digits, and hyphens)")
    elif name != folder.name and ((root / name).exists() or name in taken):
        out.append(f"Rename: {name} already exists (or another plan takes it): give it another name")
    if not plan["summary"]:
        out.append("Summary is empty")
    if plan["status"] and not re.fullmatch(r"[a-z][a-z0-9-]*( [a-z][a-z0-9-]*)?", plan["status"]):
        out.append(f"Status {plan['status']!r} isn't one or two lowercase words")
    if not plan["readme"] or "## Current state" not in plan["readme"]:
        out.append("README has no ## Current state section")
    dests = set()
    for a, b in plan["moves"]:
        src, dst = claim(a, "Move"), inside(folder, b)
        if src is not None and not src.is_file():
            out.append(f"Move: `{a}` isn't a file in the folder")
        if dst is None or (dst.exists() and dst != src):
            out.append(f"Move: `{b}` already exists or is outside the folder")
        elif Path(b).parts and Path(b).parts[0] in ("handoffs", "_archive"):  # the apply writes there first
            out.append(f"Move: `{b}` is in {Path(b).parts[0]}/, which only roost writes to")
        elif dst in dests:  # the second rename would replace the first file
            out.append(f"Move: `{b}` is the destination of more than one Move")
        dests.add(dst)
    for rel, _ in plan["archive"]:
        p = claim(rel, "Archive")
        if p is not None and not p.exists():
            out.append(f"Archive: `{rel}` doesn't exist")
        if Path(rel).parts and Path(rel).parts[0] in ("README.md", "handoffs", "_archive"):
            out.append(f"Archive: `{rel}` stays (README.md, handoffs/, and _archive/ are never archived)")
    for rel, new, _ in plan["split"]:
        p = claim(rel, "Split")
        if p is not None and not p.is_dir():
            out.append(f"Split: `{rel}/` isn't a subfolder")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", new) or (root / new).exists() or new in taken:
            out.append(f"Split: {new} isn't a free project name")
    if plan["first"]:
        rel, summary = plan["first"]
        p = claim(rel, "First Handoff") if rel else None
        if p is None or not p.is_file():
            out.append(f"First Handoff: `{rel}` isn't a file in the folder")
        if "### Next" not in summary:
            out.append("First Handoff has no ### Next section")
    return out


def apply_plan(folder, plan, today=None):
    """Carry out a checked plan. Every replaced or dropped file goes to _archive/<today>/, or
    <today>-2/ and on when an adopt already ran that day. Returns the project's folder after the rename."""
    problems = plan_problems(folder, plan)
    if problems:
        raise ValueError("; ".join(problems))
    today = day = today or datetime.now().strftime("%Y-%m-%d")
    n = 1
    while (folder / "_archive" / today).exists():
        n += 1
        today = f"{day}-{n}"
    archive = folder / "_archive" / today

    def put_away(rel):
        dst = archive / rel
        if dst.exists():
            raise ValueError(f"_archive/{today}/{rel} already exists")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(folder / rel), str(dst))
    (folder / "handoffs").mkdir(exist_ok=True)
    if plan["first"]:
        rel, summary = plan["first"]
        src = folder / rel
        stem = f"{datetime.fromtimestamp(src.stat().st_mtime):%Y-%m-%d-%H%M}-adopted"
        text, _ = redact("\n".join(["---", "session: ", f"name: {yaml_text(rel)}", "from: ", "to: ", "---", "",
                                    f"# Handoff from {rel}", "", "## Facts", "",
                                    f"- **Written by** roost adopt from `{rel}`, a hand-written handoff (now in `_archive/{today}/`)",
                                    "", "## Summary", "", summary, ""]))
        with open(folder / "handoffs" / f"{stem}.md", "x") as fh:
            fh.write(text)
        put_away(rel)
    for rel, _ in plan["archive"]:
        put_away(rel)
    for a, b in plan["moves"]:
        (folder / b).parent.mkdir(parents=True, exist_ok=True)
        os.rename(folder / a, folder / b)
    for rel, new, why in plan["split"]:
        dst = folder.parent / new
        shutil.move(str(folder / rel.rstrip("/")), str(dst))
        if not (dst / "README.md").exists():
            (dst / "README.md").write_text(readme(new, why or f"Split out of {folder.name}."))
        (dst / "handoffs").mkdir(exist_ok=True)  # a project of its own now, in roost's project layout
    readme_path = folder / "README.md"
    if readme_path.exists():
        archive.mkdir(parents=True, exist_ok=True)
        shutil.copy2(readme_path, archive / "README.md")
    try:
        path, lines, end = readme_lines(folder)
    except ValueError:  # no README, or one without frontmatter: start one, keeping nothing of the old (it's archived)
        readme_path.write_text("---\ndescription: \"\"\nsessions: []\n---\n")
        path, lines, end = readme_lines(folder)
    body, _ = redact(plan["readme"])
    write_lines(path, lines[:end + 1] + ["\n", body.strip() + "\n"])
    set_description(folder, plan["summary"])
    if plan["status"]:
        set_status(folder, plan["status"])
    new = folder.parent / plan["rename"]
    if new != folder:
        os.rename(folder, new)
    return new


def backfill(folder, model, cap, say=print, sids=None):
    """Handoffs from the transcripts of the Sessions the project lists (or just sids), oldest first,
    dated when each Session last worked. A failure writes nothing for that Session and moves on."""
    sids = read_project(folder)["sessions"] if sids is None else sids
    order = []
    for sid in sids:
        paths = session_transcripts(sid)
        if paths:
            order.append((parse(paths[0]).get("active") or paths[0].stat().st_mtime, sid))
    spent = 0.0
    for last, sid in sorted(order):
        try:
            path, _, cost, _ = summaries.summarize(sid, model, cap, readme=False, at=last)
            spent += cost
            say(f"  Handoff from {session_name(sid)}: {short(str(path))} (${cost:.2f})")
        except ValueError as e:
            say(f"  {session_name(sid)}: skipped, {e}")
    return spent


def trash(path):
    """Move a file or folder to the macOS Trash. Returns where it went."""
    dst = Path.home() / ".Trash" / f"{Path(path).parent.parent.name}-{Path(path).parent.name}-{Path(path).name}-{int(time.time())}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(dst))
    return dst


def ask(prompt):
    try:
        return input(prompt).strip().lower()
    except EOFError:
        return "q"


def adopt_command(args):
    if not sys.stdin.isatty():
        print("roost adopt: run it in a terminal; it asks before it spends anything and before it changes a folder",
              file=sys.stderr)
        return 1
    root = config.projects_dir()
    if not root.is_dir():
        print(f"roost adopt: the Projects folder {short(str(root))} doesn't exist: run roost setup", file=sys.stderr)
        return 1
    if args.folders:
        folders = [root / f.rstrip("/") for f in args.folders]
        missing = [f.name for f in folders if not f.is_dir()]
        if missing:
            print(f"roost adopt: no project folder named {', '.join(missing)} in {short(str(root))}", file=sys.stderr)
            return 1
    else:  # every folder not in roost's project layout yet (it has no handoffs/)
        folders = [f for f in sorted(root.iterdir()) if f.is_dir() and not f.name.startswith(("_", "."))
                   and not (f / "handoffs").is_dir()]
    if not folders:
        print("Every project is in roost's project layout already (each has handoffs/). Name a folder to adopt it again.")
        return 0
    model, cap = summary_settings()
    table = pricing.prices()
    config.ADOPT_PLANS.mkdir(parents=True, exist_ok=True)
    rows, total = [], 0.0
    for f in folders:
        saved = config.ADOPT_PLANS / f"{f.name}.md"
        text = None if saved.exists() and not args.fresh else adopt_input(f)
        cost = 0.0 if text is None else adopt_estimate(text, model, table)
        fill = 0.0
        if args.backfill:
            for sid in read_project(f)["sessions"]:
                try:
                    fill += summaries.summary_estimate(dict(summaries.summary_plan(sid), project=False), model, table) or 0
                except ValueError:
                    pass
        known = cost is not None
        total += (cost or 0) + fill
        rows.append((f, text, cost, fill))
        print(f"  {f.name:<48} plan {'saved' if text is None else f'${cost:.2f}' if known else 'cost unknown'}"
              + (f"   backfill ${fill:.2f}" if args.backfill else ""))
    print(f"\nAbout ${total:.2f} in all with {model}, each call capped at ${cap:g}. Nothing changes without your yes on each plan.")
    if ask(f"Plan {len(folders)} project{'s' if len(folders) != 1 else ''}? [y/N] ") != "y":
        print("Nothing ran.")
        return 0
    claimed = {}  # folder -> the names its plan takes, once shown: a skipped plan keeps them for the run
    for i, (f, text, _, _) in enumerate(rows, 1):
        saved = config.ADOPT_PLANS / f"{f.name}.md"
        if text is not None:
            print(f"\n[{i}/{len(rows)}] Planning {f.name}…", flush=True)
            try:
                body, cost = summaries.claude_print(model, ADOPT_SYSTEM, ADOPT_PROMPT + text, cap)
                log_spend("Adopt", cost, project=f.name, model=model)
            except ValueError as e:
                print(f"  Couldn't plan it, nothing changed: {e}")
                continue
            saved.write_text(adopt_plan_text(f, body))
            print(f"  Plan written (${cost:.2f}): {short(str(saved))}")
        while True:
            plan = parse_plan(saved.read_text(errors="replace"))
            print("\n" + saved.read_text(errors="replace").rstrip())
            claimed[f.name] = {n for n in [plan["rename"], *(new for _, new, _ in plan["split"])] if n and n != f.name}
            problems = plan_problems(f, plan, set().union(*(v for k, v in claimed.items() if k != f.name)))
            for p in problems:
                print(f"  ⚠ {p}")
            choice = ask(f"\n{f.name}: a apply · e edit · s skip · q stop here: ")
            if choice == "e":
                editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
                with open("/dev/tty") as tty:  # the editor needs the terminal
                    subprocess.run([*shlex.split(editor), str(saved)], stdin=tty)
                continue
            if choice == "a" and problems:
                print("  Fix the problems above first (e edits the plan).")
                continue
            if choice == "a":
                try:
                    new = apply_plan(f, plan)
                except (ValueError, OSError) as e:
                    print(f"  Couldn't apply it: {e}. Check {short(str(f))}: steps before the failure are done, "
                          "and anything they replaced is in its _archive/.")
                    break
                saved.unlink()
                print(f"  Applied: {short(str(new))}" + (f" (renamed from {f.name})" if new != f else "")
                      + f". Replaced files are in {short(str(new / '_archive'))}.")
                if args.backfill:
                    backfill(new, model, cap)
            elif choice == "q":
                print("Stopped. Plans not yet applied are kept and reused next time.")
                return 0
            else:
                print("  Skipped; the plan is kept for next time.")
            break
    if (root / "INDEX.md").exists():
        print(f"\n{short(str(root))} has an INDEX.md: rebuild anything generated from it after renames.")
    return 0


def adopt_flags(parser):
    parser.add_argument("folders", nargs="*", metavar="folder",
                        help="project folders to adopt (default: every one without handoffs/)")
    parser.add_argument("--backfill", action="store_true",
                        help="also write Handoffs from the transcripts of the Sessions each project lists (costs more)")
    parser.add_argument("--fresh", action="store_true", help="plan again even where a saved plan exists")
