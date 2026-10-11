"""roost's public GitHub repo: its latest release and release archives, read with curl (on every Mac),
so no GitHub login is needed."""
import json, subprocess

from roost.core.config import REPO

API = f"https://api.github.com/repos/{REPO}"


def fetch(url):
    """GET url, following redirects. curl retries a failed or rate-limited request three times, waiting
    longer each time and as long as a Retry-After header asks. Raises with curl's message on failure."""
    r = subprocess.run(["curl", "-fsSL", "--retry", "3", "--max-time", "120", "-H", "User-Agent: roost", url],
                       stdin=subprocess.DEVNULL, capture_output=True, timeout=600)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode(errors="replace").strip() or f"curl exited {r.returncode}")
    return r.stdout


def latest_release():
    """Latest release version, e.g. '2026.10.03'."""
    return json.loads(fetch(f"{API}/releases/latest"))["tag_name"].lstrip("v")


def download_tarball(version):
    """The source archive of release v<version>, as bytes (what Homebrew's formula downloads too)."""
    return fetch(f"https://github.com/{REPO}/archive/refs/tags/v{version}.tar.gz")
