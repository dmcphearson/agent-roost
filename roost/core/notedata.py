"""Notes: the person's scratchpad of Markdown files, and the editor that opens them."""
import os, re, shlex, shutil, sys
from datetime import datetime
from pathlib import Path

from roost.core.formatting import plain_text, redact, short, slug
from roost.core.mdfiles import frontmatter, readme_lines, unquote, write_lines, yaml_text
from roost.core import config


# ---------- notes ----------
# A note is the person's own Markdown file: an idea, a plan, a scratchpad. It lives in ~/.roost/notes
# until it's moved into a project's notes/ folder. Roost lists notes and opens them in an editor; it
# changes only a note's `project:` line, and the file stays usable without roost.

GUI_EDITORS = ("code", "cursor", "subl", "zed", "mate", "bbedit", "open")  # roost doesn't wait for these


def note_dirs():
    """Where notes are: (project id or None, folder) for ~/.roost/notes, then each project's notes/."""
    out, root = [(None, config.NOTES)], config.projects_dir()
    if root.is_dir():
        out += [(p.name, p / "notes") for p in sorted(root.iterdir())
                if p.is_dir() and not p.name.startswith(("_", ".")) and (p / "notes").is_dir()]
    return out


def note_body(text):
    """A note without its frontmatter."""
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        end = next((i for i, l in enumerate(lines[1:], 1) if l.strip() == "---"), None)
        if end is not None:
            return "\n".join(lines[end + 1:])
    return text


def read_note(path, project=None):
    text = path.read_text(errors="replace")
    meta, body = frontmatter(text), note_body(text)
    text_of = lambda k: meta[k].strip() if isinstance(meta.get(k), str) and meta[k].strip() else None
    title = text_of("title") or next((l.lstrip("#").strip() for l in body.splitlines() if l.strip()), "") or path.stem
    st = path.stat()
    created = text_of("created") or f"{datetime.fromtimestamp(getattr(st, 'st_birthtime', st.st_mtime)):%Y-%m-%d}"
    lines = [l.rstrip() for l in body.splitlines()]
    while lines and (not lines[0].strip() or lines[0].lstrip("#").strip() == title):
        lines.pop(0)  # the pane already shows the title
    ids = [x.get("id") for x in meta.get("sessions") or [] if isinstance(x, dict) and x.get("id")]
    extra = {k: v.strip() for k, v in meta.items()
             if k not in ("title", "created", "project", "sessions", "tags") and isinstance(v, str) and v.strip()}
    return {"id": path.stem, "name": title, "path": str(path), "created": created, "last": st.st_mtime,
            "project": project or text_of("project"), "lines": lines, "sessions": ids, "tags": tag_list(meta.get("tags")),
            "extra": extra, "summary": plain_text(" ".join(l.strip() for l in lines if l.strip())[:500])}


def tag_list(value):
    """A `tags:` value as a list, written either way YAML allows: [a, b] or one `- a` line each."""
    if isinstance(value, str):
        value = value.strip().strip("[]").split(",")
    return [unquote(t) for t in value or [] if isinstance(t, str) and t.strip()]


def read_notes():
    """The notes in ~/.roost/notes, the scratchpad, most recently edited first. A note moved into a
    project leaves it: it's in the project's notes/ folder now."""
    out = []
    for path in config.NOTES.glob("*.md"):
        try:
            out.append(read_note(path))
        except OSError:
            continue
    return sorted(out, key=lambda n: n["last"], reverse=True)


def new_file(folder, name, text):
    """Write text to folder/<name>.md, or <name>-2.md and so on: never replacing a file. Returns its path."""
    folder.mkdir(parents=True, exist_ok=True)
    for n in range(1, 100):
        path = folder / (f"{name}.md" if n == 1 else f"{name}-{n}.md")
        try:
            with open(path, "x") as fh:
                fh.write(text)
            return path
        except FileExistsError:
            continue
    raise ValueError(f"there are already 99 notes named {name}")


def create_note(title, today=None, body=""):
    """Make ~/.roost/notes/<slug>.md with its frontmatter, the title as its heading, then body. Returns its path."""
    title = " ".join(title.split())
    if not title:
        raise ValueError("give the note a title")
    name = slug(title) if re.search(r"[a-z0-9]", title.lower()) else "note"
    return new_file(config.NOTES, name, "\n".join([
        "---", f"title: {yaml_text(title)}", f"created: {today or datetime.now():%Y-%m-%d}", "project:",
        "sessions: []", "tags: []", "---", "", f"# {title}", ""] + ([body.strip(), ""] if body.strip() else [""])))


def note_command(args):
    body = " ".join(args.text)
    if not body and not sys.stdin.isatty():
        body = sys.stdin.read()
    body, kinds = redact(body)
    try:
        path = create_note(args.title, body=body)
    except (ValueError, OSError) as e:
        print(f"roost note: {e}", file=sys.stderr)
        return 1
    if kinds:
        print(f"roost note: redacted {len(kinds)} secret{'s' if len(kinds) != 1 else ''}", file=sys.stderr)
    print(f"Saved {short(str(path))}")
    return 0


def note_flags(parser):
    parser.add_argument("title", help="the note's title")
    parser.add_argument("text", nargs="*", help="the note's text (or pipe it in on stdin)")


def set_note_project(path, pid):
    """Set a note's `project:` line, adding one after the opening --- if there is none."""
    path, lines, end = readme_lines(path)
    at = next((i for i in range(1, end) if re.match(r"project\s*:", lines[i])), None)
    line = f"project: {yaml_text(pid)}\n"
    if at is None:
        lines[1:1] = [line]
    else:
        lines[at] = line
    write_lines(path, lines)


def file_note(path, folder):
    """Move a note into a project's notes/ folder and set its `project:`. Returns its new path."""
    path, folder = Path(path), Path(folder)
    dest = folder / "notes" / path.name
    if dest.exists():
        raise ValueError(f"{folder.name} already has a note named {path.name}")
    dest.parent.mkdir(exist_ok=True)
    if path.read_text(errors="replace").startswith("---"):
        set_note_project(path, folder.name)
    shutil.move(str(path), str(dest))
    return dest


def is_note(path):
    """Whether path is a note roost lists, so that /pickup can start from it."""
    path = Path(path).resolve()
    return path.suffix == ".md" and path.is_file() and any(path.parent == d.resolve() for _, d in note_dirs())


def editor():
    """(argv, wait) for opening a note: the editor setting, else $VISUAL, else $EDITOR, else micro.
    wait is the editor_wait setting, else yes unless the editor has its own window. None when none is installed."""
    cfg = config.read_config()
    for cand in (cfg.get("editor"), os.environ.get("VISUAL"), os.environ.get("EDITOR"), "micro"):
        argv = shlex.split(cand) if isinstance(cand, str) else []
        if argv and shutil.which(argv[0]):
            wait = cfg.get("editor_wait")
            return argv, (os.path.basename(argv[0]) not in GUI_EDITORS) if wait is None else bool(wait)
    return None


def at_end(argv, path):
    """The command that opens path in the editor argv with the cursor on its last line, for the editors
    roost knows how to tell; others open it at the top."""
    try:
        n = max(len(Path(path).read_text(errors="replace").splitlines()), 1)
    except OSError:
        n = 1
    name = os.path.basename(argv[0])
    if name == "micro":
        return [*argv, path, f"+{n}"]
    if name in ("vim", "nvim", "vi", "nano", "emacs", "hx", "kak"):
        return [*argv, f"+{n}", path]
    if name in ("code", "cursor"):
        return [*argv, "-g", f"{path}:{n}"]
    if name in ("zed", "subl"):
        return [*argv, f"{path}:{n}"]
    return [*argv, path]
