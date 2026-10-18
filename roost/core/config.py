"""roost's settings and shared constants: versions, paths, the log, and config.json."""
import json, os, re
from datetime import datetime
from pathlib import Path


VERSION = "2026.10.22"
TESTED_CLAUDE = "2.1.291"  # newest Claude Code release roost has been checked against
AGENT_KEYS = {"id", "kind", "name", "pid", "sessionId", "state", "status"}  # fields roost reads
LOG = Path.home() / ".cache" / "roost.log"
REPO = "dmcphearson/agent-roost"
UPDATE_CHECK = Path.home() / ".cache" / "roost-update-check.json"
PROJECTS = Path.home() / ".claude" / "projects"
INDEX = Path.home() / ".cache" / "roost-index-v8.json"  # bump when parse() output changes
JOBS = Path.home() / ".claude" / "jobs"
PINS = JOBS / "pins.json"
HOME = str(Path.home())
TITLE_KEYS = ('"agent-name"', '"custom-title"', '"ai-title"')
VIEWS = ["Running", "Recently closed", "Closed", "Stale", "All"]
RECENT_HOURS = 3
STATE_ORDER = {"waiting": 0, "blocked": 0, "busy": 1, "opening": 1, "watching": 2, "idle": 2, "live": 2, "closed": 3}


def log(message):
    """Append a line to ~/.cache/roost.log; failures here must never break the UI."""
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a") as fh:
            fh.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {message}\n")
    except OSError:
        pass


def version_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3])
ROOST_HOME = Path.home() / ".roost"
RECAPS = ROOST_HOME / "sessions"  # where Session recaps were before they were notes; move_recaps empties it
NOTES = ROOST_HOME / "notes"     # the person's own notes, until one is moved into a project's notes/
DONE = ROOST_HOME / "_done"      # close-out files of closed projects
PLUGIN = ROOST_HOME / "plugin"   # roost's Claude Code plugin (dev-link.sh links it to a checkout)
DEFAULT_PROJECTS = "~/.roost/projects"


def projects_dir():
    return Path(os.path.expanduser(read_config().get("projects_dir") or DEFAULT_PROJECTS))
COMPAT_LOGGED = ROOST_HOME / "compat-logged.json"  # the last Claude Code version logged as newer than roost was tested with
DISMISSED = ROOST_HOME / "dismissed.json"  # the ids of closed Sessions the person dismissed (the Sessions tab lists them last)
ADOPT_PLANS = ROOST_HOME / "adopt"  # plans as written, so a second run reuses them and the person can edit them

TEXTUAL = "textual==8.2.8"  # what roost setup installs in ~/.roost/venv
VENV = ROOST_HOME / "venv"
SPEND_LOG = ROOST_HOME / "spend.jsonl"  # roost's own model calls: when, what kind, what it cost
SPEND_CACHE = Path.home() / ".cache" / "roost-spend-v2.json"  # each transcript's priced replies, by its size and time
APP = ROOST_HOME / "app"  # the installed program files: a folder per version, and current, a link to one
BREW = os.environ.get("ROOST_INSTALL") == "brew"  # Homebrew installed roost: it brings Textual and upgrades roost
BUNDLED_PLUGIN = Path(__file__).resolve().parents[2] / "plugin"  # the plugin that came with this copy of roost
CONFIG = Path.home() / ".config" / "roost" / "config.json"


def read_config():
    try:
        return json.loads(CONFIG.read_text())
    except Exception:
        return {}


def save_config(**changes):
    cfg = {**read_config(), **changes}
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=2))
