"""roost's Claude Code plugin: installing, registering, and loading it."""
import io, json, os, re, shutil, subprocess, tarfile
from pathlib import Path

from roost.core.formatting import short
from roost.core import config
from roost.core import github


# ---------- the companion plugin ----------
# ~/.roost/plugin is a link to plugin.d/<version>, the plugin folder from that release. It's also the
# `roost` marketplace Claude Code reads in place, so an update writes the new version beside the old
# one and swaps the link with one rename: a Session starting mid-update sees one or the other, never
# neither. dev-link.sh points the link at a checkout instead; roost never replaces that.

PLUGIN_ID = "roost@roost"
POLICY = re.compile(r"polic|strictKnownMarketplaces|not allowed|blocked", re.I)


def plugin_versions():
    """plugin.d, beside the link; its .changed file holds an update's old -> new for the TUI to say once."""
    return config.PLUGIN.with_name("plugin.d")


def claude_folder():
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude"))


def claude_settings():
    """Claude Code's user settings, read only (roost changes them only through `claude plugin`)."""
    try:
        return json.loads((claude_folder() / "settings.json").read_text())
    except (OSError, ValueError):
        return {}


def claude_plugin_version():
    """The version of the copy Claude Code loads: `claude plugin install` copies the plugin into its
    cache, so a new ~/.roost/plugin reaches Sessions only after `claude plugin update`. None when
    Claude Code has no record of it."""
    try:
        entries = json.loads((claude_folder() / "plugins" / "installed_plugins.json").read_text())["plugins"][PLUGIN_ID]
        entry = next((e for e in entries if e.get("scope") == "user"), entries[0])
        return entry.get("version") or None
    except (OSError, ValueError, KeyError, IndexError, TypeError, AttributeError):
        return None


def plugin_registered(settings=None):
    s = claude_settings() if settings is None else settings
    return bool((s.get("enabledPlugins") or {}).get(PLUGIN_ID)) and "roost" in (s.get("extraKnownMarketplaces") or {})


def plugin_version(folder=None):
    """The version in a plugin folder's plugin.json ('' if it has none), or None when there's no plugin there."""
    try:
        return json.loads(Path(folder or config.PLUGIN, ".claude-plugin", "plugin.json").read_text()).get("version") or ""
    except (OSError, ValueError, AttributeError):
        return None


def plugin_dev_link():
    """The checkout ~/.roost/plugin links to, if dev-link.sh made it; else None."""
    if not config.PLUGIN.is_symlink():
        return None
    target = Path(os.path.realpath(config.PLUGIN))
    return None if target.parent == Path(os.path.realpath(plugin_versions())) else target


def plugin_problem():
    """What the TUI says at startup when the plugin isn't right, else ''. It never repairs anything."""
    if plugin_version() is None:
        return "roost plugin missing: run roost update"
    if not plugin_dev_link() and plugin_version() != config.VERSION:
        return f"roost plugin outdated ({plugin_version()}): run roost update"
    if not plugin_registered():
        return "roost plugin isn't enabled in Claude Code: run roost setup"
    cached = claude_plugin_version()
    if not plugin_dev_link() and cached and cached != config.VERSION:
        return f"Claude Code has roost plugin {cached}: run roost update"
    return ""


def update_claude_copy(version):
    """Bring Claude Code's cached copy of the plugin to version: refresh the roost marketplace, then
    update roost@roost. Returns a message; it never raises, since the folder swap already happened."""
    have = claude_plugin_version()
    if not plugin_registered() or plugin_dev_link() or have in (None, version):
        return ""
    for step in (("marketplace", "update", "roost"), ("update", PLUGIN_ID, "--scope", "user")):
        try:
            done, message = claude_plugin(*step)
        except Exception as e:
            done, message = False, str(e)
        if not done:
            return (f"Claude Code still has roost plugin {have} (claude plugin {' '.join(step[:2])} failed: {message}). "
                    f"Run claude plugin update {PLUGIN_ID} to try again")
    return f"Claude Code now has roost plugin {claude_plugin_version()}"


def plugin_args():
    """The --plugin-dir flags a Session needs for roost's plugin: none once Claude Code has it
    installed (roost@roost), else the plugin folder. Raises ValueError when there's neither."""
    if plugin_registered():
        return []
    if plugin_version() is not None:
        return ["--plugin-dir", str(config.PLUGIN)]
    raise ValueError("roost's plugin isn't installed, so /pickup can't run: run roost setup")


def extract_plugin(tarball, dest):
    """Unpack the plugin/ folder of a GitHub release tarball into dest. Only plain files and folders
    inside plugin/ are written; anything else in the archive is skipped."""
    with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as tar:
        found = False
        for m in tar.getmembers():
            parts = Path(m.name).parts
            if len(parts) < 2 or parts[1] != "plugin" or ".." in parts or m.name.startswith("/"):
                continue
            target = dest.joinpath(*parts[2:])
            if m.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif m.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(tar.extractfile(m).read())
                found = True
        if not found:
            raise RuntimeError("that release has no plugin/ folder")


def swap_plugin(folder):
    """Point ~/.roost/plugin at folder (inside plugin.d) with one rename, then drop versions that are
    neither the new one nor the one it replaced."""
    old = Path(os.path.realpath(config.PLUGIN)) if config.PLUGIN.is_symlink() else None
    if config.PLUGIN.exists() and not config.PLUGIN.is_symlink():  # a plain folder from before plugin.d: keep it aside
        os.replace(config.PLUGIN, plugin_versions() / f"{plugin_version() or 'old'}.before-link")
    tmp = config.PLUGIN.with_name(".plugin-link")
    tmp.unlink(missing_ok=True)
    os.symlink(os.path.relpath(folder, config.PLUGIN.parent), tmp)
    os.replace(tmp, config.PLUGIN)
    keep = {Path(os.path.realpath(folder)), old}
    for d in plugin_versions().iterdir():
        if d.is_dir() and not d.name.startswith(".") and Path(os.path.realpath(d)) not in keep:
            shutil.rmtree(d, ignore_errors=True)


def install_plugin(version):
    """Make ~/.roost/plugin the plugin from release v<version>. Returns a message; raises with why not."""
    dev = plugin_dev_link()
    if dev:
        raise RuntimeError(f"{short(str(config.PLUGIN))} links to a development checkout ({short(str(dev))}); update it with git instead")
    before = plugin_version()
    if before == version:
        return f"roost plugin {version} is installed"
    plugin_versions().mkdir(parents=True, exist_ok=True)
    final, tmp = plugin_versions() / version, plugin_versions() / f".{version}.download"
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        if plugin_version(config.BUNDLED_PLUGIN) == version:  # this copy of roost has it (Homebrew's does)
            shutil.copytree(config.BUNDLED_PLUGIN, tmp, ignore=shutil.ignore_patterns("__pycache__", ".DS_Store"))
        else:
            extract_plugin(github.download_tarball(version), tmp)
        if plugin_version(tmp) != version:
            raise RuntimeError(f"the download says version {plugin_version(tmp)}, not {version}")
        shutil.rmtree(final, ignore_errors=True)
        os.replace(tmp, final)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    swap_plugin(final)
    (plugin_versions() / ".changed").write_text(f"{before or 'none'} -> {version}\n")
    return f"Installed roost plugin {version}" + (f" (was {before})" if before else "")


def taken_plugin_change():
    """The bottom-bar note after an update changed the plugin, once: reading it removes it."""
    try:
        (plugin_versions() / ".changed").unlink()
    except OSError:
        return ""
    return "roost plugin updated: restart running Sessions or run /reload-plugins in them"


def claude_plugin(*args):
    """Run `claude plugin <args> --json`; returns (ok, message)."""
    r = subprocess.run(["claude", "plugin", *args, "--json"], stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, timeout=120)
    out = re.sub(r"\x1b\[[0-9;]*m", "", r.stdout.strip() + "\n" + r.stderr.strip()).strip()  # drop color codes
    try:
        res = json.loads(r.stdout.strip().splitlines()[-1])
        return r.returncode == 0 and res.get("outcome") == "ok", res.get("message") or out
    except (ValueError, IndexError, AttributeError):
        return False, out or f"exit {r.returncode}"


def setup_plugin():
    """Install the plugin folder, register it with Claude Code, and confirm it's enabled. Settings
    change only through the claude plugin CLI. Returns an exit code."""
    ok = lambda m: print(f"  \033[32m✓\033[0m {m}")
    bad = lambda m: print(f"  \033[33m!\033[0m {m}")
    dev = plugin_dev_link()
    if dev:
        ok(f"roost plugin linked to {short(str(dev))}")
    else:
        try:
            ok(install_plugin(config.VERSION))
        except Exception as e:
            bad(f"Couldn't install the roost plugin: {e}")
            return 1
    for step in (("marketplace", "add", str(config.PLUGIN)), ("install", PLUGIN_ID, "--scope", "user")):
        try:
            done, message = claude_plugin(*step)
        except Exception as e:
            done, message = False, str(e)
        if not done:
            if POLICY.search(message):
                bad(f"The roost plugin is blocked by org policy, so roost's skills won't load ({message})")
            else:
                bad(f"claude plugin {step[0]} failed: {message}")
            return 1
    if not plugin_registered():
        bad(f"Claude Code's settings don't list {PLUGIN_ID} as enabled; something may have rewritten them. Run roost setup plugin again.")
        return 1
    ok(f"{PLUGIN_ID} is enabled in Claude Code")
    return 0
