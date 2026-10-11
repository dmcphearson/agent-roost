"""The summary from a transcript: the model calls that write a Handoff."""
import json, os, re, subprocess
from pathlib import Path

from roost.core.sessiondata import parse
from roost.core.pricing import reply_cost
from roost.core.statsdata import log_spend
from roost.core.handoffs import command_repo, last_covered, owner, result_text, session_transcripts, write_handoff
from roost.core.formatting import clip, redact, short
from roost.core.mdfiles import readme_lines, write_lines
from roost.core import config


# ---------- the summary from a transcript ----------
# For an Expired Session (later also Goodnight and roost adopt's backfill): roost reads the Session's
# transcript file instead of resuming it, so the cost doesn't depend on its cache. A draft and a
# review pass write the Handoff's summary, a third call rewrites the README body, and nothing is
# written until all three have succeeded.

SUMMARY_MODEL = "claude-sonnet-5-5"  # config: summary_model
SUMMARY_BUDGET = 5.0                 # dollars per run, across its calls; config: summary_budget_usd
ENTRY_CUT = 3000                     # longer tool output keeps its beginning and end
REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
IDENTIFIER = re.compile(r"arn:aws:[\w:/.+=@-]+|https?://[^\s\"'<>)\\\]`]+|\b\d{9,}\b|\b86[0-9a-z]{7}\b|\bPR #\d+\b")
PR_URL = re.compile(r"https://github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)")
CITATION = re.compile(r" ?\[(?:\d+|state)(?:\s*[,–-]\s*(?:\d+|state))*\]")
KEEP_WHOLE = {"AskUserQuestion", "SendMessage", "ExitPlanMode", "TodoWrite"}  # the person's answers live in these

SUMMARY_SYSTEM = """You write Handoffs: the note one Claude Code Session leaves so the next Session can
carry on without the transcript. The next Session reads only your Handoff and will act on it as fact.

What makes a Handoff succeed: every workstream the person started is carried forward, and every
statement is true to the conversation and to the current state.

What makes it fail, and with it the project:
- Ambiguity is the death of success. A vague step, or a guess dressed as fact, sends the next
  Session the wrong way.
- Creating outcomes or inventing goals will cause the project to end as a failure. Never state that
  something was done, decided, or wanted unless the conversation shows it. A suggestion Claude made
  is not the person's decision until they accept it, and a condition they attached stays attached.
- Losing a workstream. Long Sessions drift, and early work is the easiest to drop.

When the conversation is unclear about something, don't resolve it yourself: say plainly that it is
unclear, under Open questions."""

SUMMARY_PROMPT = """Write the Handoff for the Session below. Entries are numbered [n]. After the
conversation come IDENTIFIERS (ids seen in tool output) and CURRENT STATE (checked by roost now,
after the Session ended).

First, silently list every workstream: each distinct goal the person asked for, from the first entry
to the last, including small side tasks. Each one must appear under What I did and, unless finished,
under Next.

Write Markdown with exactly these six headings, in this order, and nothing before the first:

### What I did
The work, as facts, in past tense, grouped under a bold workstream name when there is more than one.
Report the end state: if something was done and then reverted, renamed, or replaced, say what is true now.
### What worked
### What failed and why
Dead ends, errors, and wrong turns, with the reason, so nobody repeats them. "Nothing." if nothing failed.
### Decisions
Decisions the person made or approved, in their terms, each with its reason and any condition they attached.
### Open questions
Each unresolved question and who has to answer it. Start anything the conversation leaves unclear with "Unclear:".
### Next
Concrete next steps in order, per workstream. Mark steps that need the person's approval or face
outward (push, send, merge, delete, upload). If the Session ended mid-task, say exactly where it
stopped. Where CURRENT STATE shows something changed after the Session (a PR merged, a branch gone),
say so and adjust the step. Never list a step the conversation shows was done.

Write "None." under an empty heading. Aim for about 50 lines; go longer rather than drop a workstream.

Grounding rule: end every bullet with the entries it rests on, like [12] or [12, 40], or [state] for
CURRENT STATE. If you can't cite it, leave it out.

Keep exact identifiers (ids, ARNs, SHAs, PR and ticket numbers, paths), using IDENTIFIERS for ones the
conversation cut. Leave out date, model, tokens, cost, files changed, failed commands, commits, and
PRs as a list: roost adds those itself. Never include secrets, tokens, or passwords, even redacted.
"The person" is whoever drove the Session.

=== CONVERSATION ===
"""

REVIEW_PROMPT = """Below is a draft Handoff, then the conversation it summarizes, IDENTIFIERS, and
CURRENT STATE. Review the draft strictly, bullet by bullet, against its cited entries and the whole
conversation. Find:
1. Claims the cited entries don't support, or that the conversation contradicts.
2. Next steps the conversation shows were already done.
3. Decisions that were only Claude's suggestions, or that dropped a condition the person attached.
4. Workstreams or requests in the conversation that the draft leaves out.
5. Identifiers the draft should carry but doesn't.

Then output the corrected Handoff: the same six headings and citation style, with each problem fixed
(remove, correct, or add), and nothing before the first heading. Change nothing that is right. After
it, add one line: <!-- review: <number> changes: <short list> -->

=== DRAFT ===
%s

=== CONVERSATION ===
%s
"""

README_PROMPT = """Below are a project's README body (everything after its frontmatter), the files in
its folder, and the Handoff summary of the Session that just ended. Update the README body so it
says where the project stands now, not its history: the Handoffs hold the history.

The README is the person's working doc, and it may already be newer than the summary: the Session
often wrote it last. Edit it in place; don't rewrite it.
- Keep every section and item it has, in its words, unless the summary shows that item is done,
  wrong, or replaced. Never drop an open decision, open question, or condition the summary doesn't
  resolve, and never turn a fact the README states back into a question.
- Make sure it has, in this order after the title line (# …): ## Current state, ## Next,
  ## Key decisions (dated, newest kept), and ## Files (every file listed below, one line each saying
  what it is). Rename a section that already does one of these jobs rather than adding a second one.
  Keep the person's other sections where they are.
- Add only what the summary supports. Never invent work, decisions, or files.
- Output only the new body: no frontmatter, no code fence, no comment before or after.

=== README BODY ===
%s

=== FILES ===
%s

=== HANDOFF SUMMARY ===
%s
"""


def summary_settings():
    """(model, dollar cap per run) from roost's config."""
    cfg = config.read_config()
    try:
        cap = float(cfg.get("summary_budget_usd") or SUMMARY_BUDGET)
    except (TypeError, ValueError):
        cap = SUMMARY_BUDGET
    return cfg.get("summary_model") or SUMMARY_MODEL, cap


def entry_cut(text, n=ENTRY_CUT):
    if len(text) <= n:
        return text
    return f"{text[:n * 3 // 4]}\n[... {len(text) - n} chars cut ...]\n{text[-n // 4:]}"


def conversation(path, since=None):
    """A transcript after `since` as numbered entries for the model: nearly the whole conversation,
    without attachments, system reminders, or thinking. Returns (entries, identifiers seen in tool
    output with the entry they came from, folders the Session ran git in or edited files in)."""
    entries, tools, ids, places = [], {}, {}, {}

    def add(text):
        entries.append(f"[{len(entries) + 1}] {text}")
        return len(entries)

    with open(path, errors="replace") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            ts = rec.get("timestamp")
            if since and (not ts or ts <= since):
                continue
            t, att = rec.get("type"), rec.get("attachment") or {}
            if t == "attachment" and att.get("type") == "queued_command" and (att.get("origin") or {}).get("kind") == "human":
                add(f"USER (sent while Claude worked): {entry_cut(REMINDER.sub('', str(att.get('prompt', ''))).strip(), 6000)}")
                continue
            if t not in ("user", "assistant"):
                continue
            content = (rec.get("message") or {}).get("content")
            if isinstance(content, str):
                text = REMINDER.sub("", content).strip()
                if text and not text.startswith("<local-command"):
                    add(f"{'COMPACT SUMMARY' if rec.get('isCompactSummary') else 'USER'}: {entry_cut(text, 8000)}")
                continue
            for c in content or []:
                if not isinstance(c, dict):
                    continue
                kind = c.get("type")
                if kind == "text":
                    text = REMINDER.sub("", c.get("text", "")).strip()
                    if text:
                        add(f"{'USER' if t == 'user' else 'CLAUDE'}: {text}")
                elif kind == "image" and t == "user":
                    add("USER: [pasted an image]")
                elif kind == "tool_use":
                    name, inp = c.get("name"), c.get("input") or {}
                    tools[c.get("id")] = (name, inp)
                    body = json.dumps(inp, ensure_ascii=False)
                    add(f"TOOL CALL {name}: {body if name in KEEP_WHOLE else entry_cut(body, 1500)}")
                    if name in ("Edit", "Write", "NotebookEdit") and (inp.get("file_path") or inp.get("notebook_path")):
                        places[os.path.dirname(inp.get("file_path") or inp.get("notebook_path"))] = None
                    elif name == "Bash" and "git" in str(inp.get("command", "")):
                        d = command_repo(str(inp.get("command", "")), rec.get("cwd"))
                        if d:
                            places[d] = None
                elif kind == "tool_result":
                    name, _ = tools.get(c.get("tool_use_id"), ("?", {}))
                    text = REMINDER.sub("", result_text(c.get("content"))).strip()
                    n = add(f"RESULT {name}{' (ERROR)' if c.get('is_error') else ''}: "
                            f"{text if name in KEEP_WHOLE else entry_cut(text)}")
                    for i in list(dict.fromkeys(IDENTIFIER.findall(text)))[:15]:
                        ids.setdefault(i.rstrip(".,;:"), n)
    return entries, ids, list(places)


def git_root(folder):
    d = os.path.expanduser(folder or "")
    while d and d != "/":
        if os.path.exists(os.path.join(d, ".git")):
            return d
        d = os.path.dirname(d)
    return None


def read_only(cmd):
    """A read-only check's output, or why it couldn't run."""
    try:
        r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        return f"(couldn't run: {e})"
    return (r.stdout or r.stderr).strip()


def current_state(places, convo):
    """What's true now in the repos the Session worked in and the PRs it mentioned: read-only
    git status and log, and gh pr view."""
    out = []
    for root in sorted({r for r in map(git_root, places) if r})[:8]:
        status = read_only(["git", "-C", root, "status", "-sb"]).splitlines()[:8]
        recent = read_only(["git", "-C", root, "log", "-3", "--format=%h %cs %s"])
        out.append(f"repo {short(root)}:\n  " + "\n  ".join(status) + "\n  recent commits:\n  " + recent.replace("\n", "\n  "))
    for repo, num in list(dict.fromkeys(PR_URL.findall(convo)))[:10]:
        state = read_only(["gh", "pr", "view", num, "-R", repo, "--json", "state,mergedAt,title,headRefName",
                           "--jq", '"\\(.state) merged=\\(.mergedAt) \\(.headRefName): \\(.title)"'])
        out.append(f"PR {repo}#{num}: {state}")
    return out


def summary_plan(sid):
    """Everything a summary run needs, read without any model call: the project, where the
    Session's last Handoff stopped, and the conversation since. Raises ValueError with a reason."""
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", sid or ""):
        raise ValueError(f"{sid!r} isn't a Session id")
    paths = session_transcripts(sid)
    if not paths:
        raise ValueError(f"there's no transcript for Session {sid[:8]}")
    ids = sorted({sid} | set(parse(paths[0])["ids"]))
    folder = owner(ids)  # None: the run starts a Session recap
    readme = 0
    if folder is not None and folder.is_dir():
        _, lines, end = readme_lines(folder)  # fails now, before anything is spent, if there's no README to update
        readme = len("".join(lines[end + 1:]))
    since = last_covered(folder, ids) if folder else None
    entries, found, places = conversation(paths[0], since)
    if not any(e.split("] ", 1)[1].startswith(("USER", "CLAUDE")) for e in entries):
        raise ValueError("nothing new since its last Handoff" if since else "its transcript has no conversation")
    return {"session": sid, "folder": folder, "since": since, "entries": entries, "ids": found, "places": places,
            "readme": readme, "project": bool(folder and folder.is_dir())}


def summary_input(plan, state):
    convo = "\n\n".join(plan["entries"])
    return (convo + "\n\n=== IDENTIFIERS (first seen in tool output) ===\n"
            + "\n".join(f"{i} [{n}]" for i, n in list(plan["ids"].items())[:200])
            + "\n\n=== CURRENT STATE (checked now) ===\n" + ("\n".join(state) or "Nothing to check."))


SUMMARY_OUT = 3000  # tokens a Handoff draft or its review comes to, for the estimate


def summary_estimate(plan, model, table):
    """The run's estimated cost in dollars at list prices, or None without a price for the model."""
    # about 3 characters a token: transcripts are heavy with JSON, paths, and code
    n = len(summary_input(plan, []) + SUMMARY_SYSTEM + SUMMARY_PROMPT) // 3
    calls = [(n, SUMMARY_OUT), (n + SUMMARY_OUT, SUMMARY_OUT)]
    if plan.get("project", True):
        calls.append((plan.get("readme", 0) // 3 + 2 * SUMMARY_OUT, 3 * SUMMARY_OUT))
    # claude -p writes its whole input to the 1-hour cache (2x the input price); assume US-only inference (1.1x)
    costs = [reply_cost(model, {"cache_creation_input_tokens": i, "cache_creation": {"ephemeral_1h_input_tokens": i},
                                "output_tokens": o, "inference_geo": "us"}, table) for i, o in calls]
    return None if None in costs else sum(costs)


def claude_print(model, system, prompt, budget):
    """One headless model call with no tools, settings, or MCP servers, capped at budget dollars.
    Returns (text, cost); raises ValueError with the reason when it fails."""
    cmd = ["claude", "-p", *(["--plugin-dir", str(config.PLUGIN)] if config.PLUGIN.exists() else []), "--model", model,
           "--output-format", "json", "--tools", "", "--no-session-persistence", "--setting-sources", "",
           "--strict-mcp-config", "--max-budget-usd", f"{budget:.2f}", "--system-prompt", system]
    try:
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        raise ValueError("the model call took over 15 minutes") from None
    except OSError as e:
        raise ValueError(f"couldn't run claude: {e}") from None
    try:
        out = json.loads(r.stdout)
    except ValueError:
        raise ValueError(clip(" ".join((r.stderr or r.stdout or f"claude exited {r.returncode}").split()), 200)) from None
    if r.returncode or out.get("is_error") or out.get("subtype") != "success":
        why = out.get("subtype") or ""
        if "budget" in why:
            raise ValueError(f"it hit the ${budget:.2f} left of the run's cap")
        raise ValueError(clip(" ".join(str(out.get("result") or why or f"claude exited {r.returncode}").split()), 200))
    return str(out.get("result") or "").strip(), float(out.get("total_cost_usd") or 0)


def folder_files(folder):
    """The project's files for the README's index: everything but handoffs/, hidden files, and the README."""
    out = []
    for p in sorted(Path(folder).rglob("*")):
        rel = p.relative_to(folder)
        if p.is_file() and rel.parts[0] != "handoffs" and not any(x.startswith(".") for x in rel.parts) \
                and str(rel) != "README.md":
            out.append(str(rel))
    return out[:200]


def summarize(sid, model=None, cap=None, now=None, call=None, readme=True, at=None, progress=None):
    """Write a Session's Handoff and README update from its transcript, without resuming it. Every
    model call comes first, so a failure writes nothing. progress(n) is called as model call n starts. Returns (Handoff path, redacted kinds,
    dollars spent). Raises ValueError with the reason."""
    dflt_model, dflt_cap = summary_settings()
    model, cap, call = model or dflt_model, cap if cap is not None else dflt_cap, call or claude_print
    plan = summary_plan(sid)
    text = summary_input(plan, current_state(plan["places"], "\n".join(plan["entries"])))
    spent, step = 0.0, 0

    def ask(system, prompt):
        nonlocal spent, step
        step += 1
        if progress:
            progress(step)
        if cap - spent < 0.01:
            raise ValueError(f"it used the run's ${cap:g} cap before finishing")
        out, cost = call(model, system, prompt, cap - spent)
        spent += cost
        log_spend("Recaps and Handoffs", cost, session=sid, model=model)
        if not out:
            raise ValueError("the model returned nothing")
        return out

    draft = ask(SUMMARY_SYSTEM, SUMMARY_PROMPT + text)
    reviewed = ask(SUMMARY_SYSTEM, REVIEW_PROMPT % (draft, text))
    summary = CITATION.sub("", re.sub(r"\s*<!-- review:.*?-->\s*", "\n", reviewed, flags=re.S)).strip()
    if "### Next" not in summary:
        raise ValueError("the summary came back without its Next section")
    if not plan["project"] or not readme:  # a Session recap has no README to update; adopt's backfill writes its own
        handoff, kinds = write_handoff(sid, summary, now, recap=True, at=at)
        return handoff, kinds, spent
    path, lines, end = readme_lines(plan["folder"])
    body = ask(SUMMARY_SYSTEM, README_PROMPT % ("".join(lines[end + 1:]).strip() or "(empty)",
                                                "\n".join(folder_files(plan["folder"])) or "(none)", summary))
    body = re.sub(r"\A```\w*\n(.*)\n```\Z", r"\1", body.strip(), flags=re.S).strip()
    if "## Current state" not in body:
        raise ValueError("the README rewrite came back without a Current state section")
    handoff, kinds = write_handoff(sid, summary, now)
    body, more = redact(body)
    path, lines, end = readme_lines(plan["folder"])  # re-read: keep frontmatter written meanwhile
    write_lines(path, lines[:end + 1] + ["\n", body + "\n"])
    return handoff, kinds + more, spent
