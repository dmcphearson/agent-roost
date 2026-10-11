"""Asking a model what to do with a finished or idle Session."""
import re

from roost.core.projectdata import worked
from roost.core.pricing import reply_cost
from roost.core.handoffs import session_transcripts
from roost.core.summaries import conversation
from roost.core.formatting import redact, short


# ---------- asking what to do with a Session ----------
# One model call reads a Session that isn't running, with the facts code can check (what lists it,
# what it edited, whether it holds secrets, your projects), and says what to do with it. The person
# reads the advice in a box; its key only opens the step it names, which asks again.

ASK_OUT = 900          # tokens the advice comes to, for the estimate
ASK_TEXT = 200_000     # characters of conversation the model sees, at most (the start and the end)
ADVICE = ("delete", "recap", "handoff", "add-to-project", "keep")

ASK_SYSTEM = """You advise a person on what to do with one finished or idle Claude Code Session they
are tidying up. They act on your advice, so it must be right about what the transcript shows.

What succeeds: a clear verdict, the open items the work left (a PR waiting on review, a check that
never ran, a key to revoke), and why, all taken from the transcript and the facts given.

What fails: calling work finished when the transcript shows open items, advising delete when the
Session holds work nothing else has saved, or naming a project the facts don't support. Ambiguity is
the death of success: when you can't tell, say so in WHY and pick the safer verdict (never delete)."""

ASK_PROMPT = """The verdicts:
- delete: nothing worth keeping (a quick question, a dead end, or work fully saved elsewhere)
- recap: worth keeping, but it belongs to no project: write a Session recap
- handoff: a project already lists it, and it has work since its last Handoff: write its Handoff
- add-to-project: it belongs to one of the projects below: add it there, then write its Handoff
- keep: it's still mid-task: leave it as it is

Answer in exactly this shape, nothing before it:
VERDICT: <one verdict>
PROJECT: <a project id from the list, only with add-to-project; otherwise none>
FINISHED: <yes or no>
OPEN:
- <each open item, or none>
WARNINGS:
- <each thing to act on before deleting (a pasted secret, unpushed work), or none>
WHY:
<two or three sentences>

=== FACTS (checked by code) ===
"""


def ask_input(s, projects, recap=None, project=None):
    """What the advisor reads: the facts code can check, then the conversation (its start and its end
    when it's long)."""
    path = (session_transcripts(s["id"]) or [None])[0]
    if path is None:
        raise ValueError(f"there's no transcript for Session {s['id'][:8]}")
    entries, _, _ = conversation(path)
    if not any(e.split("] ", 1)[1].startswith(("USER", "CLAUDE")) for e in entries):
        raise ValueError("its transcript has no conversation")
    text = "\n\n".join(entries)
    if len(text) > ASK_TEXT:
        text = text[:ASK_TEXT // 4] + "\n\n[… the middle is left out …]\n\n" + text[-(ASK_TEXT * 3 // 4):]
    text, kinds = redact(text)  # the advisor never sees a pasted secret, only that there was one
    edited = [p for p in projects if any(f.startswith(p["path"].rstrip("/") + "/") for f in s.get("files") or [])]
    facts = [f"Session: {s['name']} ({s['id'][:8]}), last active {worked(s.get('active') or s['last'])}",
             f"Listed by: {'project ' + project['id'] if project else 'recap ' + recap['id'] if recap else 'nothing (no project or recap lists it)'}",
             f"Files it edited: {', '.join(short(f) for f in (s.get('files') or [])[:20]) or 'none'}",
             f"Projects whose folder it edited: {', '.join(p['id'] for p in edited) or 'none'}",
             f"Secrets in the transcript: {', '.join(sorted(set(kinds))) if kinds else 'none found'}",
             "", "Projects (id: summary):"]
    facts += [f"- {p['id']}: {p.get('summary') or ''}" for p in projects if not p.get("closed")]
    return ASK_PROMPT + "\n".join(facts) + "\n\n=== CONVERSATION ===\n" + text


def ask_estimate(text, model, table):
    n = len(ASK_SYSTEM + text) // 3
    return reply_cost(model, {"cache_creation_input_tokens": n, "cache_creation": {"ephemeral_1h_input_tokens": n},
                              "output_tokens": ASK_OUT, "inference_geo": "us"}, table)


def parse_advice(text, project_ids=()):
    """The advisor's answer as a dict. A verdict or project it isn't allowed falls back to keep."""
    def field(name):
        m = re.search(rf"^{name}:[ \t]*(.*)$", text, re.M | re.I)
        return m.group(1).strip() if m else ""

    def items(name):
        m = re.search(rf"^{name}:[ \t]*\n((?:[ \t]*[-*].*\n?)*)", text, re.M | re.I)
        out = [re.sub(r"^[ \t]*[-*]\s*", "", l).strip() for l in (m.group(1) if m else "").splitlines()]
        return [x for x in out if x and x.lower().rstrip(".") != "none"]
    verdict = field("VERDICT").lower().strip("`*. ")
    project = field("PROJECT").strip("`*. ")
    m = re.search(r"^WHY:[ \t]*\n?(.*)", text, re.M | re.S | re.I)
    advice = {"verdict": verdict if verdict in ADVICE else "keep", "project": None,
              "finished": field("FINISHED").lower().startswith("y"), "open": items("OPEN"),
              "warnings": items("WARNINGS"), "why": " ".join((m.group(1) if m else text).split())}
    if advice["verdict"] == "add-to-project":
        if project in project_ids:
            advice["project"] = project
        else:
            advice["verdict"] = "keep"
            advice["why"] = f"(It named a project that isn't in the list: {project or 'none'}.) " + advice["why"]
    return advice
