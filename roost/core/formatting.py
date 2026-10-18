"""Short text helpers: paths, times, token counts, and clipping."""
import locale, os, re, subprocess, sys
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


def order_from_format(fmt):
    """"DMY" when a strftime date format puts the day before the month, else "MDY" (the default,
    and what a year-first format like Japan's gets)."""
    day = min((i for i in (fmt.find("%d"), fmt.find("%e")) if i >= 0), default=-1)
    month = fmt.find("%m")
    return "DMY" if 0 <= day < month else "MDY"


_order = None


def date_order():
    """The system locale's day/month order, "MDY" or "DMY": from LC_ALL, LC_TIME, or LANG, else (on a
    Mac whose shell sets none) the Region in System Settings. MDY when none can be read."""
    global _order
    if _order is None:
        name = next((os.environ[v] for v in ("LC_ALL", "LC_TIME", "LANG") if os.environ.get(v)), "")
        if not name and sys.platform == "darwin":
            try:
                region = subprocess.run(["defaults", "read", "-g", "AppleLocale"], stdin=subprocess.DEVNULL,
                                        capture_output=True, text=True, timeout=5).stdout.strip()
                name = f"{region.split('@')[0]}.UTF-8" if region else ""
            except (OSError, subprocess.SubprocessError):
                name = ""
        fmt = ""
        if name:
            old = locale.setlocale(locale.LC_TIME)
            try:
                locale.setlocale(locale.LC_TIME, name)
                fmt = locale.nl_langinfo(locale.D_FMT)
            except (locale.Error, AttributeError, ValueError):
                fmt = ""
            finally:
                locale.setlocale(locale.LC_TIME, old)
        _order = order_from_format(fmt)
    return _order


def short_when(ts, now=None):
    """When, as the lists and cards show it: "2:17 PM" today, else the date alone, "10/14/26" (or
    "14/10/26" where the locale puts the day first)."""
    d = datetime.fromtimestamp(ts)
    today = (now if isinstance(now, datetime) else datetime.fromtimestamp(now) if now else datetime.now()).date()
    if d.date() == today:
        return f"{d.hour % 12 or 12}:{d:%M} {'AM' if d.hour < 12 else 'PM'}"
    return f"{d:%d/%m/%y}" if date_order() == "DMY" else f"{d:%m/%d/%y}"


def short_day(text):
    """A written date that starts YYYY-MM-DD, as the lists and cards show a date: "10/14/26" (or
    "14/10/26"), never a time, since a plain date has none. Anything else comes back as written."""
    try:
        d = datetime.strptime(text[:10], "%Y-%m-%d")
    except (TypeError, ValueError):
        return text
    return f"{d:%d/%m/%y}" if date_order() == "DMY" else f"{d:%m/%d/%y}"


def ktok(n):
    return f"{n / 1000:.0f}k"


def clip(text, width):
    if width <= 0:
        return ""
    return text if len(text) <= width else text[: max(width - 1, 0)] + "…"


def plain_text(text):
    """Summary text without Markdown: no heading marks, bold, or code ticks, on one line."""
    text = re.sub(r"(?m)^\s*#+\s*", "", text or "")
    return " ".join(text.replace("**", "").replace("__", "").replace("`", "").split())
