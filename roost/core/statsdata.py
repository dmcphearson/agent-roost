"""Spend at list price: every reply in Claude Code's transcripts, and roost's own model calls from
its spend log, by day, kind, Session, and project."""
import calendar, json, os, re, time
from datetime import date, datetime, timedelta

from roost.core import config
from roost.core import pricing


# ---------- spend ----------
# Claude Code's transcripts give every reply's tokens and model; roost prices them from Anthropic's
# pricing page. That's list price, before any discount: a ceiling, not the invoice. roost's own
# claude -p calls save no transcript, so each one's cost goes in the spend log as it finishes.

KINDS = ["Live work", "Wrap-ups", "Recaps and Handoffs", "Ask", "Adopt"]
RANGES = ["Today", "This week", "Month to date", "All time"]
NO_PROJECT = "no project"
WRAP_UP = re.compile(r"<command-name>/(?:roost:)?wrap-up</command-name>")
SKILL_WRAP_UP = ("roost:wrap-up", "wrap-up")


def log_spend(kind, cost, session=None, project=None, model=None, now=None):
    """Add one of roost's own model calls to the spend log. Failures here never stop the call's work."""
    if not cost:
        return
    rec = {"ts": datetime.fromtimestamp(now or time.time()).astimezone().isoformat(timespec="seconds"),
           "kind": kind, "cost": round(float(cost), 6), "session": session, "project": project, "model": model}
    try:
        config.SPEND_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(config.SPEND_LOG, "a") as fh:
            fh.write(json.dumps(rec) + "\n")
    except OSError as e:
        config.log(f"spend log: {e}")


def read_spend_log():
    out = []
    try:
        with open(config.SPEND_LOG) as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                    out.append(rec) if rec.get("kind") in KINDS and rec.get("ts") else None
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def local_day(ts):
    """The person's own calendar day of an ISO timestamp, as YYYY-MM-DD."""
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().date().isoformat()
    except (ValueError, AttributeError):
        return None


def local_hour(ts):
    """The hour (0-23) of an ISO timestamp on the person's own clock, or 0 when it can't be read."""
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().hour
    except (ValueError, AttributeError):
        return 0


def prompt_text(rec):
    """What the person typed in a user record, or None for tool results, skills' own text, and
    Claude Code's notes (command echoes, caveats, compact summaries)."""
    if rec.get("isMeta") or rec.get("isCompactSummary"):
        return None
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, list):
        texts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
        if not texts:
            return None
        content = "\n".join(texts)
    return content if isinstance(content, str) else None


def transcript_spend(path, table):
    """One transcript's priced replies: {"replies": [[message id, day, 1 if a Wrap-up, dollars,
    tokens read from cache, all prompt tokens, hour]], "unpriced": [models]}. A reply is a Wrap-up when it came after /wrap-up (typed,
    or the wrap-up skill called) and before the person's next prompt; every other reply is Live work."""
    replies, wrapping = {}, False
    with open(path, errors="replace") as fh:
        for line in fh:
            if '"user"' not in line and '"assistant"' not in line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            t = rec.get("type")
            if t == "user":
                text = prompt_text(rec)
                if text is None:
                    continue
                if WRAP_UP.search(text):
                    wrapping = True
                elif not text.lstrip().startswith(("<local-command", "<command-", "Caveat:")):
                    wrapping = False
            elif t == "assistant":
                msg = rec.get("message") or {}
                key = msg.get("id") or rec.get("uuid")
                first = replies.get(key)
                replies[key] = (first[0] if first else rec.get("timestamp"), first[1] if first else wrapping, msg)
                for c in msg.get("content") or []:
                    if isinstance(c, dict) and c.get("type") == "tool_use" and c.get("name") == "Skill" \
                            and str((c.get("input") or {}).get("skill", "")) in SKILL_WRAP_UP:
                        wrapping = True
    out = {"replies": [], "unpriced": []}
    for key, (ts, wrap, msg) in replies.items():
        u, model, day = msg.get("usage") or {}, msg.get("model"), local_day(ts)
        if not u or not model or model.startswith("<") or not day:
            continue  # <synthetic> replies are Claude Code's own, not the model's
        cost = pricing.reply_cost(model, u, table)
        if cost is None:
            out["unpriced"] = sorted(set(out["unpriced"]) | {model})
            continue
        read = u.get("cache_read_input_tokens", 0)
        prompt = u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0) + read
        out["replies"].append([key, day, 1 if wrap else 0, round(cost, 6), read, prompt, local_hour(ts)])
    return out


def session_of(path):
    """The Session a transcript belongs to: its own name, or for a subagent's, its Session's folder."""
    return path.parent.parent.name if path.parent.name == "subagents" else path.stem


def gather_spend(table=None, now=None):
    """Every reply's spend, read from the transcripts (each re-read only when it changed since the
    last time) and the spend log. Returns {"rows": [(day, kind, session, project, dollars)],
    "hours": {day: {kind: [dollars in each hour]}}, "tokens": {day: [read, prompt]}, "unpriced": [models], "priced": bool}. project is set only for
    rows whose log entry names one; the rest are matched to projects by their Session."""
    table = table if table is not None else pricing.prices(now)
    if not table:
        return {"rows": [], "hours": {}, "tokens": {}, "unpriced": [], "priced": False}
    stamp = table.get("fetched")
    try:
        cache = json.loads(config.SPEND_CACHE.read_text())
        if cache.get("prices") != stamp:
            cache = {}
    except (OSError, ValueError):
        cache = {}
    files, seen = cache.get("files") or {}, {}
    rows, tokens, unpriced, counted, hours = [], {}, set(), set(), {}

    def by_hour(day, hour, kind, cost):
        hours.setdefault(day, {}).setdefault(kind, [0.0] * 24)[hour] += cost
    for path in sorted(config.PROJECTS.glob("*/*.jsonl")) + sorted(config.PROJECTS.glob("*/*/subagents/*.jsonl")):
        try:
            st = path.stat()
        except OSError:
            continue
        key, mark = str(path), [st.st_mtime, st.st_size]
        got = files.get(key)
        if not got or got.get("mark") != mark:
            try:
                got = dict(transcript_spend(path, table), mark=mark)
            except OSError:
                continue
        seen[key] = got
        sid = session_of(path)
        for mid, day, wrap, cost, read, prompt, hour in got["replies"]:
            if mid in counted:
                continue  # a resumed Session's transcript can repeat replies another one has
            counted.add(mid)
            rows.append((day, KINDS[wrap], sid, None, cost))
            by_hour(day, hour, KINDS[wrap], cost)
            t = tokens.setdefault(day, [0, 0])
            t[0] += read
            t[1] += prompt
        unpriced.update(got["unpriced"])
    for rec in read_spend_log():
        day = local_day(rec["ts"])
        if day:
            rows.append((day, rec["kind"], rec.get("session"), rec.get("project"), float(rec.get("cost") or 0)))
            by_hour(day, local_hour(rec["ts"]), rec["kind"], float(rec.get("cost") or 0))
    try:
        config.SPEND_CACHE.parent.mkdir(parents=True, exist_ok=True)
        config.SPEND_CACHE.write_text(json.dumps({"prices": stamp, "files": seen}))
    except OSError as e:
        config.log(f"spend cache: {e}")
    return {"rows": rows, "hours": hours, "tokens": tokens, "unpriced": sorted(unpriced), "priced": True}


def range_start(rng, today):
    """The first day a range covers, or None for All time."""
    return {"Today": today, "This week": today - timedelta(days=today.weekday()),
            "Month to date": today.replace(day=1)}.get(rng)


def monthly_limit():
    """The monthly_limit setting in dollars, or None when it isn't set (or isn't a number)."""
    try:
        v = float(config.read_config().get("monthly_limit") or 0)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def streaks(days, today):
    """(current, longest): runs of days in a row with any spend. The current run may end yesterday,
    since today isn't over."""
    have = {date.fromisoformat(d) for d in days}
    longest = run = 0
    for d in sorted(have):
        run = run + 1 if d - timedelta(days=1) in have else 1
        longest = max(longest, run)
    current, d = 0, today if today in have else today - timedelta(days=1)
    while d in have:
        current, d = current + 1, d - timedelta(days=1)
    return current, longest


def spend_view(data, rng, project_of, names, today=None, hour=None):
    """What the Stats tab shows for a range: totals by kind and project, daily spend per kind for
    the chart, the calendar's days, and the facts. project_of maps a Session id to its project;
    names maps it to the Session's name. The chart covers the range's days, or Today's hours up to
    hour (now's, on the person's clock)."""
    today = today or date.today()
    hour = datetime.now().hour if hour is None else hour
    start = range_start(rng, today)
    in_range = lambda day: (start is None or day >= start.isoformat()) and day <= today.isoformat()
    by_kind, by_project, by_day, by_session, all_days = {}, {}, {}, {}, {}
    for day, kind, sid, project, cost in data["rows"]:
        all_days[day] = all_days.get(day, 0) + cost
        if not in_range(day):
            continue
        by_kind[kind] = by_kind.get(kind, 0) + cost
        p = project or project_of.get(sid) or NO_PROJECT
        by_project[p] = by_project.get(p, 0) + cost
        by_day.setdefault(day, {})
        by_day[day][kind] = by_day[day].get(kind, 0) + cost
        if sid:
            by_session[sid] = by_session.get(sid, 0) + cost
    if rng == "Today":  # the chart: Today by the hour, every other range by its days
        got = (data.get("hours") or {}).get(today.isoformat(), {})
        chart_labels = [f"{h:02d}:00" for h in range(hour + 1)]
        chart = {k: list((got.get(k) or [0.0] * 24)[:hour + 1]) for k in KINDS}
    else:
        first = start or (date.fromisoformat(min(all_days)) if all_days else today)
        days = [first + timedelta(days=i) for i in range((today - first).days + 1)]
        chart_labels = [f"{d:%b} {d.day}" for d in days]
        chart = {k: [0.0] * len(days) for k in KINDS}
        index = {d.isoformat(): i for i, d in enumerate(days)}
        for day, kind, sid, project, cost in data["rows"]:
            if day in index:
                chart[kind][index[day]] += cost
    read = prompt = 0
    for day, (r, p) in data["tokens"].items():
        if in_range(day):
            read, prompt = read + r, prompt + p
    month = sum(v for d, v in all_days.items() if d >= today.replace(day=1).isoformat() and d <= today.isoformat())
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    top_day = max(((d, v) for d, v in all_days.items() if in_range(d)), key=lambda x: x[1], default=None)
    top_sid = max(by_session.items(), key=lambda x: x[1], default=None)
    current, longest = streaks([d for d, v in all_days.items() if v > 0.005], today)
    return {
        "range": rng, "start": start, "today": today, "total": sum(by_kind.values()),
        "kinds": [(k, by_kind.get(k, 0)) for k in KINDS],
        "projects": sorted(by_project.items(), key=lambda x: (x[0] == NO_PROJECT, -x[1])),
        "chart_labels": chart_labels, "chart": chart, "calendar": all_days,
        "month": month, "pace": month / today.day * days_in_month, "month_end": today.replace(day=days_in_month),
        "limit": monthly_limit(),
        "top_day": top_day, "top_session": (names.get(top_sid[0]) or top_sid[0][:8], top_sid[1]) if top_sid else None,
        "streak": current, "longest": longest, "cached": read / prompt if prompt else None,
        "unpriced": data.get("unpriced") or [], "priced": data.get("priced", True),
    }
