"""Tasks: one Markdown file each in a project's (or sub-project's) tasks/ folder, with a status, the
Session that has claimed it, and notes that Sessions and you add."""
import argparse, re, sys
from datetime import datetime

from roost.core.mdfiles import frontmatter, write_lines, yaml_text
from roost.core.projectdata import find_project, project_id


# ---------- tasks ----------
# A task's id is <project>/<n> (a sub-project's: <project>/<slug>/<n>), n counting up within its
# tasks/ folder; its file is tasks/<n>-<slug>.md. The frontmatter holds title, status, created,
# updated, and claimed_by while a Session has it; the body is the task, then ## Notes.

TASKS = "tasks"
TASK_STATUSES = ("todo", "in-progress", "in-review", "done", "blocked")
TASK_LIST_ORDER = ("in-progress", "blocked", "in-review", "todo", "done")  # what roost task list shows first
TASK_SLUG_MAX = 40
NOTES_HEADING = "## Notes"


def task_slug(title):
    """The file name's part from a title: lowercase letters, digits, and hyphens."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:TASK_SLUG_MAX].rstrip("-") or "task"


def task_now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def project_folder(pid):
    """A project's or sub-project's folder; a Session recap or a note has no tasks."""
    folder = find_project(pid)
    if not folder.is_dir():
        raise ValueError(f"{pid} is a Session recap, which has no tasks")
    return folder


def task_files(folder):
    """A project's task files by number, as {n: path}."""
    out = {}
    for path in (folder / TASKS).glob("*.md"):
        m = re.match(r"(\d+)-", path.name)
        if m:
            out[int(m[1])] = path
    return out


def find_task(tid):
    """A task's file, from its id. Raises ValueError if there's none."""
    pid, _, n = tid.strip().rpartition("/")
    if not pid or not n.isdigit():
        raise ValueError(f"{tid!r} isn't a task id: they look like <project>/<n>")
    path = task_files(project_folder(pid)).get(int(n))
    if path is None:
        raise ValueError(f"no task {tid} (roost task list {pid} shows them)")
    return path


def task_id(path):
    return f"{project_id(path.parent.parent)}/{int(path.name.split('-', 1)[0])}"


def read_task(path):
    task = frontmatter(path.read_text())
    task["id"], task["path"] = task_id(path), path
    return task


def task_text(fields, body):
    head = "".join(f"{k}: {yaml_text(str(v))}\n" for k, v in fields.items() if v not in (None, ""))
    return f"---\n{head}---\n{body}"


def task_parts(path):
    """A task's frontmatter fields, in their order, and the body after them."""
    text = path.read_text()
    end = text.index("\n---\n", 3) + 5
    return dict(frontmatter(text)), text[end:]


def change_task(path, **fields):
    """Set (or, given None, remove) frontmatter fields, and updated, keeping the body."""
    old, body = task_parts(path)
    old.update(fields, updated=task_now())
    write_lines(path, [task_text(old, body)])


def new_task(pid, title, text=""):
    folder = project_folder(pid)
    title = " ".join(title.split())
    if not title:
        raise ValueError("a task needs a title")
    n = max(task_files(folder), default=0) + 1
    path = folder / TASKS / f"{n}-{task_slug(title)}.md"
    path.parent.mkdir(exist_ok=True)
    now = task_now()
    body = f"\n{text.strip()}\n\n{NOTES_HEADING}\n" if text.strip() else f"\n{NOTES_HEADING}\n"
    write_lines(path, [task_text({"title": title, "status": "todo", "created": now, "updated": now}, body)])
    return path


def claim_task(path, session):
    holder = read_task(path).get("claimed_by")
    if holder and holder != session:
        raise ValueError(f"{task_id(path)} is claimed by Session {holder}")
    change_task(path, claimed_by=session, status="in-progress")


def note_task(path, session, text):
    text = " ".join(text.split())
    if not text:
        raise ValueError("a note needs text")
    fields, body = task_parts(path)
    if NOTES_HEADING not in body.splitlines():
        body = body.rstrip("\n") + f"\n\n{NOTES_HEADING}\n"
    body = body.rstrip("\n") + f"\n- {task_now()} · {session}: {text}\n"
    fields["updated"] = task_now()
    write_lines(path, [task_text(fields, body)])


def set_task_status(path, status):
    if status not in TASK_STATUSES:
        raise ValueError(f"{status!r} isn't a task status: {', '.join(TASK_STATUSES)}")
    change_task(path, status=status, **({"claimed_by": None} if status == "todo" else {}))


def read_tasks(folder):
    return [read_task(p) for _, p in sorted(task_files(folder).items())]


# ---------- the roost task command ----------

def stdin_text(words):
    text = " ".join(words or [])
    if not text and not sys.stdin.isatty():
        text = sys.stdin.read()
    return text


def task_command(args):
    try:
        if args.action == "new":
            print(task_id(new_task(args.project, args.title, stdin_text(args.text))))
        elif args.action == "list":
            tasks = [t for t in read_tasks(project_folder(args.project)) if args.all or t.get("status") != "done"]
            for status in TASK_LIST_ORDER:
                group = [t for t in tasks if t.get("status") == status]
                if group:
                    print(status)
                    for t in group:
                        held = f"  (claimed by {t['claimed_by']})" if t.get("claimed_by") else ""
                        print(f"  {t['id']}  {t.get('title', '')}{held}")
            if not tasks:
                print(f"no open tasks in {args.project}")
        elif args.action == "show":
            print(find_task(args.id).read_text(), end="")
        elif args.action == "claim":
            claim_task(find_task(args.id), args.session)
            print(f"{args.id}: claimed by {args.session}, in-progress")
        elif args.action == "note":
            note_task(find_task(args.id), args.session, stdin_text(args.text))
            print(f"{args.id}: note added")
        elif args.action == "status":
            path = find_task(args.id)
            if args.value is None:
                print(read_task(path).get("status") or "(no status)")
            else:
                old = read_task(path).get("status")
                set_task_status(path, args.value)
                print(f"{args.id}: status {old or '(none)'} → {args.value}")
    except ValueError as e:
        print(f"roost task {args.action}: {e}", file=sys.stderr)
        return 1
    return 0


def task_flags(parser):
    sub = parser.add_subparsers(dest="action", metavar="<action>", required=True)
    new = sub.add_parser("new", help="add a task to a project, in todo, and print its id", description="Add a task to a project or sub-project.")
    new.add_argument("project", help="the project's folder name, or a sub-project's <project>/<slug>")
    new.add_argument("title", help="the task's title")
    new.add_argument("text", nargs="*", help="what to do and what counts as done (or pipe it in on stdin)")
    ls = sub.add_parser("list", help="list a project's open tasks by status", description="List a project's open tasks, by status.")
    ls.add_argument("project", help="the project's folder name, or a sub-project's <project>/<slug>")
    ls.add_argument("--all", action="store_true", help="include done tasks")
    show = sub.add_parser("show", help="print a task's file", description="Print a task's file: its frontmatter, text, and notes.")
    show.add_argument("id", help="the task's id, <project>/<n>")
    claim = sub.add_parser("claim", help="claim a task for a Session and set it in-progress",
                           description="Claim a task for a Session; a task another Session holds is refused.")
    claim.add_argument("id", help="the task's id, <project>/<n>")
    claim.add_argument("--session", required=True, metavar="ID", help="the Session claiming it")
    note = sub.add_parser("note", help="add a dated note to a task", description="Add a note to a task, dated and naming its Session.")
    note.add_argument("id", help="the task's id, <project>/<n>")
    note.add_argument("text", nargs="*", help="the note (or pipe it in on stdin)")
    note.add_argument("--session", required=True, metavar="ID", help="the Session writing it")
    st = sub.add_parser("status", help="print a task's status, or set it", description="Print a task's status, or set it. todo releases its claim.")
    st.add_argument("id", help="the task's id, <project>/<n>")
    st.add_argument("value", nargs="?", help=f"the new status: {', '.join(TASK_STATUSES)}")
