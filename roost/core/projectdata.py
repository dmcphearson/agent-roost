"""Projects: folders with a README, their frontmatter, Sessions, tags, and categories."""
import json, os, re, sys, time
from datetime import datetime
from pathlib import Path

from roost.core.sessiondata import parse, stamp
from roost.core.notedata import is_note, new_file, read_note, set_note_project, tag_list
from roost.core.formatting import clip, plain_text, short
from roost.core.mdfiles import frontmatter, readme_lines, write_lines, yaml_text
from roost.core import config


# ---------- projects ----------
# A project is one folder in the Projects folder; its README's frontmatter holds summary, status,
# and sessions (as `name` and `id` pairs). Roost writes to an existing README in only two
# places, each changing just its own lines: the `category:` line, and the `sessions:` list.

CLOSED = ("closed",)             # status values that put a project behind the closed toggle
BRIEF_HANDOFFS = 3               # how many of the newest Handoffs `roost project brief` prints

# A sub-project is one piece of a project's work, in <project>/sub/<slug>/: the same shape as a
# project, with a `type:` in its frontmatter. Its id is <project>/<slug>. They don't nest.
SUB = "sub"
SUB_TYPES = ("feature", "bugfix", "research", "chore")
SUB_DONE = ("done",) + CLOSED    # status values that hide a sub-project
SLUG = re.compile(r"[a-z0-9][a-z0-9-]*")


def handoff_time(path):
    """When a Handoff was written, from its name: YYYY-MM-DD-HHMM-<session8>.md."""
    try:
        return datetime.strptime(path.name[:15], "%Y-%m-%d-%H%M").timestamp()
    except ValueError:
        return None


def heading(text):
    """A README's first # heading, after its frontmatter: the project's name as people read it."""
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        end = next((i for i, l in enumerate(lines[1:], 1) if l.strip() == "---"), 0)
        lines = lines[end + 1:]
    fence = False
    for line in lines:
        if line.lstrip().startswith(("```", "~~~")):
            fence = not fence
        elif not fence and line.startswith("# ") and line[2:].strip():
            return line[2:].strip()
    return ""


def label_of(p):
    """What a project is called on screen: its README's heading, else its folder's name."""
    return p.get("label") or p["id"]


def read_project(folder, sessions=()):
    try:
        text = (folder / "README.md").read_text(errors="replace")
    except OSError:
        text = ""
    meta = frontmatter(text)
    linked = [x.get("id") for x in meta.get("sessions") or [] if isinstance(x, dict) and x.get("id")]
    times = [t for t in map(handoff_time, sorted((folder / "handoffs").glob("*.md"))) if t]
    times += [s["last"] for s in sessions if set(s.get("ids") or [s["id"]]) & set(linked)]
    covered = {}  # Session id -> where its newest Handoff stopped (its to:)
    for h in (folder / "handoffs").glob("*.md"):
        try:
            hm = frontmatter(h.read_text(errors="replace"))
        except OSError:
            continue
        to = stamp(hm.get("to")) if isinstance(hm.get("to"), str) else None
        if hm.get("session") and to:
            covered[hm["session"]] = max(to, covered.get(hm["session"], 0))
    status = meta.get("status") if isinstance(meta.get("status"), str) else ""
    category = meta.get("category").strip() if isinstance(meta.get("category"), str) else ""
    tags = tag_list(meta.get("tags"))
    kind = meta.get("type").strip() if isinstance(meta.get("type"), str) else ""
    return {"id": folder.name, "type": kind, "label": heading(text) or folder.name, "path": str(folder), "summary": meta.get("summary") if isinstance(meta.get("summary"), str) else "",
            "status": status, "category": category, "tags": tags, "sessions": linked, "last": max(times) if times else None,
            "closed": status.lower() in CLOSED or (config.DONE / f"{folder.name}.md").exists(),
            "readme": (folder / "README.md").exists(), "archives": archives_due(folder),
            "adopted": (folder / "handoffs").is_dir(),  # in roost's shape; roost adopt (or Adopt it) brings it there
            "covered": covered}


def plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def clean_tags(text):
    """Tags typed as "a, b" (or "#a #b"): each lowercase, its spaces made hyphens, each once."""
    out = []
    for t in re.split(r"[,\s]*,[,\s]*|\s+(?=#)", text.strip()):
        t = "-".join(t.strip().lstrip("#").lower().split())
        if t and t not in out:
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", t):
                raise ValueError(f"{t!r} isn't a tag: use lowercase letters, digits, and hyphens, like ai-tools")
            out.append(t)
    return out


def set_tags(path, tags):
    """Set a README's (given its folder) or a note's `tags:` to [a, b], replacing the line and any
    `- a` lines under it, or adding one after the opening ---. Every other byte stays as it was."""
    path, lines, end = readme_lines(path)
    at = next((i for i in range(1, end) if re.match(r"tags\s*:", lines[i])), None)
    new = [f"tags: [{', '.join(tags)}]\n"]
    if at is None:
        lines[1:1] = new
    else:
        stop = at + 1
        while stop < end and re.match(r"\s+- |\s*-\s*$", lines[stop]):
            stop += 1
        lines[at:stop] = new
    write_lines(path, lines)


def read_categories():
    """The categories a project can have, from CATEGORIES.md in the Projects folder: one list item
    each, `- name: what kind of work it is` (the description is optional). Other lines are ignored."""
    try:
        text = (config.projects_dir() / "CATEGORIES.md").read_text()
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        m = re.match(r"\s*[-*]\s+`?([a-z0-9][a-z0-9-]*)`?\s*(?::\s*(.*))?$", line)
        if m and m.group(1) not in [n for n, _ in out]:
            out.append((m.group(1), (m.group(2) or "").strip()))
    return out


def add_category(name):
    """Add a category to the end of CATEGORIES.md, making the file if there's none."""
    path = config.projects_dir() / "CATEGORIES.md"
    try:
        text = path.read_text()
    except FileNotFoundError:
        text = ("# Categories\n\nThe kinds of work a project can be, one per line: `- name: what it covers`. "
                "Roost's Set its category lists these.\n\n")
    path.write_text(text + ("" if text.endswith("\n") else "\n") + f"- {name}\n")


def set_category(folder, category):
    """Set the README's top-level `category:` line, adding one after the opening --- if there is
    none. Every other byte of the README stays as it was. An empty category removes the line."""
    category = category.strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*|", category):
        raise ValueError("a category is lowercase letters, digits, and hyphens, like ai-tools")
    path, lines, end = readme_lines(folder)
    at = next((i for i in range(1, end) if re.match(r"category\s*:", lines[i])), None)
    new = [f"category: {category}\n"] if category else []
    if at is None:
        lines[1:1] = new
    else:
        lines[at:at + 1] = new
    write_lines(path, lines)


def set_status(folder, status):
    """Set the README's top-level `status:` line, adding one after the opening --- if there is
    none. Every other byte of the README stays as it was. Returns the old status ("" if none)."""
    status = " ".join(status.split()).lower()
    if not re.fullmatch(r"[a-z][a-z0-9 -]{0,39}", status):
        raise ValueError("a status is a few lowercase words, like active, blocked, or closed")
    path, lines, end = readme_lines(folder)
    at = next((i for i in range(1, end) if re.match(r"status\s*:", lines[i])), None)
    old = "" if at is None else str(frontmatter("".join(lines[:end + 1])).get("status") or "")
    if at is None:
        lines[1:1] = [f"status: {status}\n"]
    else:
        lines[at] = f"status: {status}\n"
    write_lines(path, lines)
    return old


def sessions_list(lines, end):
    """Where the README's `sessions:` list is: (index of its line or None, its items as
    (first, last) line ranges, end exclusive). Raises ValueError for a form roost won't rewrite."""
    at = next((i for i in range(1, end) if re.match(r"sessions\s*:", lines[i])), None)
    if at is None:
        return None, []
    value = lines[at].split(":", 1)[1].strip()
    if value not in ("", "[]"):
        raise ValueError("its sessions: list isn't in the one-item-per-line form, so roost won't rewrite it")
    items, i = [], at + 1
    while i < end and (lines[i][:1].isspace() or lines[i].startswith("-")):
        if lines[i].strip().startswith("- "):
            items.append([i, i + 1])
        elif lines[i].strip() and items:
            items[-1][1] = i + 1
        i += 1
    return at, [tuple(x) for x in items]


def link_session(folder, sid, name):
    """Add a Session to a project's `sessions:` list, in the /project skill's format, once.
    Returns False if it was already listed. Every other line of the README stays as it was."""
    path, lines, end = readme_lines(folder)
    at, items = sessions_list(lines, end)
    if any(re.match(rf"\s*(- )?id:\s*['\"]?{re.escape(sid)}['\"]?\s*$", lines[i]) for a, b in items for i in range(a, b)):
        return False
    entry = [f"  - name: {yaml_text(name)}\n", f"    id: {sid}\n"]
    if at is None:
        lines[end:end] = ["sessions:\n"] + entry
    elif not items:
        lines[at:at + 1] = ["sessions:\n"] + entry
    else:
        last = items[-1][1]
        lines[last:last] = entry
    write_lines(path, lines)
    return True


def unlink_session(folder, sid):
    """Take a Session off a project's `sessions:` list. Returns False if it wasn't on it."""
    path, lines, end = readme_lines(folder)
    at, items = sessions_list(lines, end)
    hit = next(((a, b) for a, b in items if any(re.search(rf"\bid:\s*['\"]?{re.escape(sid)}['\"]?\s*$", lines[i])
                                                for i in range(a, b))), None)
    if not hit:
        return False
    del lines[hit[0]:hit[1]]
    if len(items) == 1:
        lines[at] = "sessions: []\n"
    write_lines(path, lines)
    return True


def assign_session(sid, name, folder):
    """List a Session in one project, taking it off any other project's list first: a Session belongs
    to at most one project or sub-project. Returns (whether it was added, the ids of projects it was moved from)."""
    moved = []
    for p in read_all() + read_recaps():
        if Path(p["path"]) != Path(folder) and sid in p["sessions"]:
            unlink_session(p["path"], sid)
            moved.append(p["id"])
    return link_session(folder, sid, name), moved


def is_subproject(folder):
    folder = Path(folder)
    return folder.parent.name == SUB and folder.parent.parent.parent == config.projects_dir()


def project_id(folder):
    """A project's id (its folder's name), or a sub-project's (<project>/<slug>)."""
    folder = Path(folder)
    return f"{folder.parent.parent.name}/{folder.name}" if is_subproject(folder) else folder.name


def read_subprojects(folder, sessions=()):
    """A project's sub-projects, each read like a project, plus its slug and parent; closed when done."""
    root = Path(folder) / SUB
    if not root.is_dir():
        return []
    out = []
    for f in sorted(root.iterdir()):
        if f.is_dir() and not f.name.startswith(("_", ".")):
            s = read_project(f, sessions)
            s.update(id=f"{Path(folder).name}/{f.name}", label=f"{Path(folder).name}/{f.name}", slug=f.name,
                     parent=Path(folder).name, closed=s["status"].lower() in SUB_DONE)
            out.append(s)
    return out


def read_all(sessions=()):
    """Every project, each followed by its sub-projects: what a Session can be listed in."""
    out = []
    for p in read_projects(sessions) or []:
        out += [p] + p["subs"]
    return out


def read_projects(sessions=()):
    """Every project in the Projects folder, newest work first; None if the folder is missing.
    Folders starting with _ or . (such as a generated `_By Category`) aren't projects."""
    root = config.projects_dir()
    if not root.is_dir():
        return None
    out = [dict(read_project(p, sessions), subs=read_subprojects(p, sessions)) for p in root.iterdir()
           if p.is_dir() and not p.name.startswith(("_", "."))]
    out.sort(key=lambda p: p["id"], reverse=True)  # dated names: newest first among never-worked
    out.sort(key=lambda p: p["last"] or 0, reverse=True)
    return out


RECAP_TAG = "recap"  # a Session recap is a note with this tag


def is_recap(path):
    """Whether path is a Session recap: a note in ~/.roost/notes tagged recap."""
    path = Path(path)
    try:
        return path.parent == config.NOTES and RECAP_TAG in tag_list(frontmatter(path.read_text(errors="replace")).get("tags"))
    except OSError:
        return False


def read_recaps():
    """Session recaps: the notes tagged recap, each holding the Handoffs of Sessions that had no project."""
    out = []
    for path in config.NOTES.glob("*.md"):
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        meta = frontmatter(text)
        if RECAP_TAG not in tag_list(meta.get("tags")):
            continue
        summary = meta.get("summary")
        if not isinstance(summary, str) or not summary:
            body = text.split("---", 2)[-1] if text.startswith("---") else text
            summary = next((l.strip() for l in body.splitlines() if l.strip() and not l.startswith(("#", "<!--"))), "")
        ids = [x.get("id") for x in meta.get("sessions") or [] if isinstance(x, dict) and x.get("id")]
        covered = {}  # Session id -> where its newest Handoff in the recap stopped
        for _, sid, _, to, _ in recap_sections(text):
            if stamp(to):
                covered[sid] = max(stamp(to), covered.get(sid, 0))
        out.append({"id": path.stem, "path": str(path), "summary": summary, "last": path.stat().st_mtime,
                    "sessions": ids, "folder": meta.get("folder") if isinstance(meta.get("folder"), str) else None,
                    "covered": covered})
    return sorted(out, key=lambda n: n["last"], reverse=True)


def recap_as_note(path):
    """An old Session recap's text as a note: title, created, and project: lines added, tagged recap,
    and the rest kept as it was."""
    text = path.read_text(errors="replace")
    st = path.stat()
    meta = frontmatter(text) if text.startswith("---") else {}
    name = next((x["name"] for x in meta.get("sessions") or [] if isinstance(x, dict) and isinstance(x.get("name"), str)), "")
    tags = tag_list(meta.get("tags"))
    head = [f"title: {yaml_text(name.strip() or path.stem.replace('-', ' ').capitalize())}\n",
            f"created: {datetime.fromtimestamp(getattr(st, 'st_birthtime', st.st_mtime)):%Y-%m-%d}\n", "project:\n"]
    tail = [f"tags: [{', '.join(tags + [RECAP_TAG])}]\n", "---\n"]
    if not meta and not text.startswith("---"):
        return "".join(["---\n"] + head + tail + ["\n", text])
    _, lines, end = readme_lines(path)
    if any(re.match(r"tags\s*:\s*$", l) for l in lines[1:end]):
        raise ValueError("its tags: are a list of lines; add recap to them and move it into ~/.roost/notes by hand")
    keep = [l for l in lines[1:end] if not re.match(r"(title|created|project|tags)\s*:", l)]
    return "".join(["---\n"] + head + keep + tail + lines[end + 1:])


def move_recaps():
    """Session recaps from before they were notes: move each from ~/.roost/sessions into ~/.roost/notes,
    once. The old file goes only after its note is written; a run after a crash between the two finds
    the note there and only removes the old file. Returns how many moved."""
    moved = 0
    for path in sorted(config.RECAPS.glob("*.md")):
        try:
            text = recap_as_note(path)
            if not any(n.read_text(errors="replace") == text for n in config.NOTES.glob(f"{path.stem}*.md")):
                new_file(config.NOTES, path.stem, text)
            path.unlink()
            moved += 1
        except (OSError, ValueError) as e:
            config.log(f"move recap {path.name} into notes: {e}")
    try:
        config.RECAPS.rmdir()  # only when nothing else is in it
    except OSError:
        pass
    return moved


def readme(pid, summary, label=None, kind=None):
    return (f"---\nsummary: {json.dumps(summary, ensure_ascii=False)}\n" + (f"type: {kind}\n" if kind else "")
            + "status: active\nsessions: []\n---\n\n"
            f"# {' '.join((label or '').split()) or pid}\n\n## Current state\n\n## Next\n\n## Key decisions\n\n## Files\n")


def rename_project(folder, new):
    """Rename a project: its folder, and what names it by its old name (its notes' `project:` lines,
    a README title that was just the old name, a close-out record, a saved adopt plan). Returns the
    new folder; raises ValueError with a reason, before anything changes."""
    folder, new = Path(folder), new.strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", new):
        raise ValueError("a project's name is its folder's: lowercase letters, digits, and hyphens, like printer-rollout")
    if new == folder.name:
        raise ValueError("that's its name already")
    dst = folder.parent / new
    if dst.exists():
        raise ValueError(f"{new} already exists")
    os.rename(folder, dst)
    for n in sorted((dst / "notes").glob("*.md")):
        try:
            if read_note(n)["project"] == folder.name:
                set_note_project(n, new)
        except (ValueError, OSError):
            pass
    readme_path = dst / "README.md"
    if readme_path.exists():
        lines = readme_path.read_text(errors="replace").splitlines(keepends=True)
        at = next((i for i, l in enumerate(lines) if l.startswith("# ")), None)
        if at is not None and lines[at].strip() == f"# {folder.name}":
            lines[at] = f"# {new}\n"
            readme_path.write_text("".join(lines))
    for d in (config.DONE, config.ADOPT_PLANS):
        if (d / f"{folder.name}.md").exists() and not (d / f"{new}.md").exists():
            os.rename(d / f"{folder.name}.md", d / f"{new}.md")
    return dst


def create_project(pid, summary, label=None):
    """Make a project folder with a README in roost's project layout, its heading the label (else the id). Returns its path; raises ValueError
    with a reason the person can act on."""
    pid, summary = pid.strip(), " ".join(summary.split())
    root = config.projects_dir()
    if not pid or pid.startswith(("_", ".")) or "/" in pid or pid != pid.split()[0]:
        raise ValueError("a project id is one word, like 2026-10-printer-rollout, not starting with _ or .")
    if not summary:
        raise ValueError("give it a one-sentence summary")
    if not root.is_dir():
        raise ValueError(f"the Projects folder {short(str(root))} doesn't exist: run roost setup")
    folder = root / pid
    if folder.exists():
        raise ValueError(f"{pid} already exists")
    folder.mkdir()
    (folder / "handoffs").mkdir()
    (folder / "README.md").write_text(readme(pid, summary, label))
    return folder


def create_subproject(pid, kind, summary):
    """Make a sub-project <project>/<slug>: sub/<slug>/ in the project, with a README of the given type
    and handoffs/. Returns its path; raises ValueError with a reason, before anything is made."""
    parent, _, slug = pid.strip().partition("/")
    summary = " ".join(summary.split())
    if kind not in SUB_TYPES:
        raise ValueError(f"a sub-project's --type is one of {', '.join(SUB_TYPES)}")
    if "/" in slug:
        raise ValueError("sub-projects don't nest: an id is <project>/<slug>")
    if not SLUG.fullmatch(slug):
        raise ValueError("a sub-project's slug is lowercase letters, digits, and hyphens, like badge-printers")
    root = config.projects_dir()
    if not parent or parent.startswith(("_", ".")) or not (root / parent).is_dir():
        raise ValueError(f"there's no project {parent!r} (roost project list shows them)")
    if not summary:
        raise ValueError("give it a one-sentence summary")
    folder = root / parent / SUB / slug
    if folder.exists():
        raise ValueError(f"{parent}/{slug} already exists")
    (folder / "handoffs").mkdir(parents=True)
    (folder / "README.md").write_text(readme(slug, summary, kind=kind))
    return folder


def worked(ts):
    """'Oct 7, 4:12 pm'"""
    d = datetime.fromtimestamp(ts)
    return f"{d:%b} {d.day}, {d.hour % 12 or 12}:{d:%M} {'am' if d.hour < 12 else 'pm'}"


def project_new(args):
    try:
        if "/" in args.id:
            if not args.type:
                raise ValueError(f"a sub-project needs --type: {', '.join(SUB_TYPES)}")
            folder = create_subproject(args.id, args.type, args.summary)
        elif args.type:
            raise ValueError("--type is for a sub-project (<project>/<slug>); a project has none")
        else:
            folder = create_project(args.id, args.summary)
    except ValueError as e:
        print(f"roost project new: {e}", file=sys.stderr)
        return 1
    print(f"Created {short(str(folder))}")
    return 0


def find_project(pid):
    """A project's folder, or failing that a Session recap's file, or a note given its path.
    Raises ValueError if none exists."""
    pid = pid.strip().rstrip("/")
    if pid.endswith(".md") and is_note(os.path.expanduser(pid)):  # a note, given its path
        return Path(os.path.expanduser(pid))
    if pid.count("/") == 1:
        parent, slug = pid.split("/")
        folder = config.projects_dir() / parent / SUB / slug
        if parent and not parent.startswith(("_", ".")) and SLUG.fullmatch(slug) and folder.is_dir():
            return folder
    if pid and "/" not in pid and not pid.startswith(("_", ".")):
        if (config.projects_dir() / pid).is_dir():
            return config.projects_dir() / pid
        if is_recap(config.NOTES / f"{pid}.md"):
            return config.NOTES / f"{pid}.md"
    raise ValueError(f"no project or Session recap named {pid!r} (roost project list shows them)")


def brief(target):
    """What a Session reads to catch up: the README, then the newest Handoffs, newest first.
    For a Session recap, the recap."""
    if target.is_file() and not is_recap(target):
        note = read_note(target)
        return (f"# Note: {note['name']}\nFile: {target}" + (f"\nProject: {note['project']}" if note["project"] else "")
                + f"\n\n{target.read_text(errors='replace').rstrip()}\n")
    if target.is_file():
        return f"# Session recap {target.stem}\nFile: {target}\n\n{target.read_text(errors='replace').rstrip()}\n"
    if is_subproject(target):
        return subproject_brief(target)
    return "\n".join(project_lines(target, f"# Project {target.name}")) + "\n"


def subproject_brief(target):
    """A sub-project's brief: its parent's README first, as the wider context (not its Handoffs, which
    are the parent's history), then the sub-project like a project, then the parent's other open
    sub-projects, so the Session knows what else is in flight."""
    parent = target.parent.parent
    readme = parent / "README.md"
    kind = read_project(target)["type"]
    out = [f"# Wider context: project {parent.name}", f"Folder: {parent}", "",
           f"This sub-project is part of {parent.name}. Keep its goals in view, and keep this Session's work "
           "inside the sub-project.", "",
           readme.read_text(errors="replace").rstrip() if readme.exists() else "(This project has no README.md.)", ""]
    out += project_lines(target, f"# Sub-project {project_id(target)}" + (f" ({kind})" if kind else ""))
    others = [s for s in read_subprojects(parent) if Path(s["path"]) != target and not s["closed"]]
    if others:
        out += ["", f"# Other open sub-projects of {parent.name} (not this Session's work)", ""]
        out += [f"- {s['slug']} ({s['type'] or 'no type'}, {s['status'] or 'no status'}): {plain_text(s['summary'])}"
                for s in others]
    return "\n".join(out) + "\n"


def project_lines(target, title):
    """A project's (or sub-project's) README, then its newest Handoffs, newest first, and its notes."""
    readme = target / "README.md"
    out = [title, f"Folder: {target}", "",
           readme.read_text(errors="replace").rstrip() if readme.exists() else "(This project has no README.md.)"]
    handoffs = sorted(p for p in (target / "handoffs").glob("*.md") if handoff_time(p))
    newest = handoffs[::-1][:BRIEF_HANDOFFS]
    out += ["", f"# Newest Handoffs ({len(newest)} of {len(handoffs)}, newest first)" if handoffs else "# No Handoffs yet"]
    for p in newest:
        out += ["", f"## handoffs/{p.name}", "", p.read_text(errors="replace").rstrip()]
    if len(handoffs) > len(newest):
        out += ["", f"{len(handoffs) - len(newest)} older Handoffs are in {target / 'handoffs'}."]
    notes = sorted((target / "notes").glob("*.md"))
    if notes:
        out += ["", f"# Notes ({len(notes)}, in {target / 'notes'}; open one only when the work needs it)", ""]
        out += [f"- notes/{p.name}: {read_note(p)['name']}" for p in notes]
    return out


def session_name(sid):
    """The name roost shows for a Session, read from its transcript; its short id if there's none yet."""
    path = next(config.PROJECTS.glob(f"*/{sid}.jsonl"), None)
    return parse(path)["name"] if path else sid[:8]


def project_brief(args):
    try:
        target = find_project(args.id)
        text = brief(target)
    except (ValueError, OSError) as e:
        print(f"roost project brief: {e}", file=sys.stderr)
        return 1
    print(text, end="")
    if args.session:
        try:
            added, moved = assign_session(args.session, session_name(args.session), target)
        except (ValueError, OSError) as e:
            print(f"\nCouldn't record this Session in {project_id(target) if target.is_dir() else target.stem}: {e}", file=sys.stderr)
            return 1
        print(f"\nThis Session {'is now listed' if added else 'was already listed'} in "
              f"{project_id(target) if target.is_dir() else target.stem}'s sessions:"
              + (f" (moved from {', '.join(moved)})" if moved else "") + ".")
    return 0


def project_list(args):
    projects = read_projects()
    if projects is None:
        print(f"roost project list: the Projects folder {short(str(config.projects_dir()))} doesn't exist: run roost setup",
              file=sys.stderr)
        return 1
    shown = [(p, [s for s in read_subprojects(p["path"]) if args.all or not s["closed"]])
             for p in projects if args.all or not p["closed"]]
    width = max([len(p["id"]) for p, _ in shown] + [len(s["id"]) + 2 for _, subs in shown for s in subs] + [10])
    for p, subs in shown:
        for x, name, summary in [(p, p["id"], p["summary"])] + [(s, f"  {s['id']}", f"{s['type']}: {s['summary']}") for s in subs]:
            last = worked(x["last"]) if x["last"] else "never"
            print(f"{name.ljust(width)}  {(x['status'] or '—')[:10]:<10}  {last:<16}  {clip(plain_text(summary), 90)}")
    recaps = read_recaps()
    if recaps:
        print("\nSession recaps:")
        for n in recaps:
            print(f"{n['id'].ljust(width)}  {'recap':<10}  {worked(n['last']):<16}  {clip(plain_text(n['summary']), 90)}")
    if not shown and not recaps:
        print("No projects yet. Create one: roost project new <id> --summary \"…\"")
    return 0


def project_status(args):
    try:
        folder = find_project(args.id)
        if not folder.is_dir():
            raise ValueError(f"{args.id} is a Session recap, which has no status")
        if args.value is None:
            print(read_project(folder)["status"] or "(no status)")
            return 0
        old = set_status(folder, args.value)
    except ValueError as e:
        print(f"roost project status: {e}", file=sys.stderr)
        return 1
    print(f"{project_id(folder)}: status {old or '(none)'} → {read_project(folder)['status']}")
    return 0


def project_command(args):
    return {"new": project_new, "brief": project_brief, "list": project_list, "status": project_status}[args.action](args)


def project_flags(parser):
    sub = parser.add_subparsers(dest="action", metavar="<action>", required=True)
    new = sub.add_parser("new", help="create a project folder and README", description="Create a project folder and README.")
    new.add_argument("id", help="the folder name, e.g. printer-rollout; <project>/<slug> makes a sub-project")
    new.add_argument("--type", help=f"a sub-project's type: {', '.join(SUB_TYPES)}")
    new.add_argument("--summary", required=True, help="one sentence saying what the project is")
    b = sub.add_parser("brief", help="print what a Session needs to catch up (/pickup runs this)",
                       description="Print a project's README and its newest Handoffs, or a Session recap.")
    b.add_argument("id", help="the project's folder name, a sub-project's <project>/<slug>, or a Session recap's name")
    b.add_argument("--session", metavar="ID", help="also list this Session in the project's sessions: "
                   "(taking it off any other project's list)")
    ls = sub.add_parser("list", help="list projects and Session recaps, most recently worked first",
                        description="List open projects and Session recaps, most recently worked first.")
    ls.add_argument("--all", action="store_true", help="include closed projects")
    st = sub.add_parser("status", help="print a project's status, or set it (/wrap-up asks first)",
                        description="Print a project's status, or set it. Only the README's status: line changes.")
    st.add_argument("id", help="the project's folder name, or a sub-project's <project>/<slug>")
    st.add_argument("value", nargs="?", help=f"the new status: active, blocked, {CLOSED[0]} (closed hides it), or a few lowercase words")
RECAP_MARK = re.compile(r"^<!-- roost handoff ([\w.-]+) session: (\S+) from: (\S*) to: (\S*) -->$", re.M)


def recap_sections(text):
    """A recap's Handoffs as (file stem, session, from, to, body), oldest first. The stem names a
    file in handoffs/, so a marker whose stem has .. in it isn't one."""
    marks = [m for m in RECAP_MARK.finditer(text) if ".." not in m.group(1)]
    return [(m.group(1), m.group(2), m.group(3), m.group(4),
             text[m.end():marks[i + 1].start() if i + 1 < len(marks) else len(text)].strip())
            for i, m in enumerate(marks)]
ADOPT_ARCHIVE_DAYS = 5     # config: adopt_archive_days; after this roost suggests clearing an _archive/<date>/


def archives_due(folder, now=None):
    """The project's _archive/<date>/ folders (and <date>-2/ from a second adopt that day) older
    than adopt_archive_days, as (name, days old)."""
    try:
        days = float(config.read_config().get("adopt_archive_days") or ADOPT_ARCHIVE_DAYS)
    except (TypeError, ValueError):
        days = ADOPT_ARCHIVE_DAYS
    out = []
    for d in sorted((Path(folder) / "_archive").glob("*")):
        m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})(-\d+)?", d.name)
        try:
            age = ((now or time.time()) - datetime.strptime(m[1], "%Y-%m-%d").timestamp()) / 86400
        except (TypeError, ValueError):
            continue
        if d.is_dir() and age >= days:
            out.append((d.name, int(age)))
    return out
