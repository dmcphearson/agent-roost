"""Short text helpers: paths, times, token counts, and clipping."""
import re
from datetime import datetime

from roost.core.config import HOME


SECRETS = [  # what roost redacts from a Handoff before writing it
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)),
    ("anthropic", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}")),
    ("github", re.compile(r"\b(?:ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("aws", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("slack", re.compile(r"\bxox[a-z]-[A-Za-z0-9-]{10,}")),
    ("pk", re.compile(r"\bpk_[A-Za-z0-9_]{16,}")),
    ("okta", re.compile(r"(?<![A-Za-z0-9_-])00[A-Za-z0-9_-]{40}(?![A-Za-z0-9_-])")),  # an Okta API (SSWS) token
]
SECRET_VALUE = re.compile(r"(?i)(?<![a-z])(password|token)=(\"[^\"]*\"|'[^']*'|[^\s&\"'`]+)")


def redact(text):
    """Replace secrets with [REDACTED:<kind>]. Returns (text, the kinds found, one per match)."""
    kinds = []
    for kind, rx in SECRETS:
        text, n = rx.subn(f"[REDACTED:{kind}]", text)
        kinds += [kind] * n

    def value(m):
        if m.group(2).strip("\"'").startswith("[REDACTED:"):
            return m.group(0)
        kinds.append(m.group(1).lower())
        return f"{m.group(1)}=[REDACTED:{m.group(1).lower()}]"
    return SECRET_VALUE.sub(value, text), kinds


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60].strip("-") or "session"


# ---------- formatting ----------

SHOWN = []  # (folder, how it's shown): roost --demo shows its temp folders as the real ones


def short(p):
    if not p:
        return "?"
    for real, shown in SHOWN:
        if p == real or p.startswith(real + "/"):
            return shown + p[len(real):]
    return p.replace(HOME, "~")


def when(ts):
    d = datetime.fromtimestamp(ts)
    days = (datetime.now().date() - d.date()).days
    if days == 0:
        return d.strftime("Today %H:%M")
    if days == 1:
        return d.strftime("Yesterday %H:%M")
    if days < 7:
        return d.strftime("%a %H:%M")
    return d.strftime("%b %d")


def ktok(n):
    return f"{n / 1000:.0f}k"


def worked_short(ts):
    """When, in a few characters: 9:31 pm today, Oct 9 this year, else Oct 2025."""
    d = datetime.fromtimestamp(ts)
    if d.date() == datetime.now().date():
        return f"{d:%I:%M}".lstrip("0") + f" {d:%p}".lower()
    return f"{d:%b} {d.day}" if d.year == datetime.now().year else f"{d:%b %Y}"


def clip(text, width):
    if width <= 0:
        return ""
    return text if len(text) <= width else text[: max(width - 1, 0)] + "…"


def plain_text(text):
    """Summary text without Markdown: no heading marks, bold, or code ticks, on one line."""
    text = re.sub(r"(?m)^\s*#+\s*", "", text or "")
    return " ".join(text.replace("**", "").replace("__", "").replace("`", "").split())
