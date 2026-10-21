"""Installing and updating roost, Textual, and the plugin; roost setup."""
import io, json, os, shlex, shutil, subprocess, sys, tarfile, time
from pathlib import Path

from roost.core.config import DEFAULT_PROJECTS, HOME, REPO, TEXTUAL, version_tuple
from roost.core.plugin import install_plugin, setup_plugin
from roost.core.formatting import short
from roost.core.terminals import TERMINALS, app_installed
from roost.core import config
from roost.core import wren
from roost.core import plugin
from roost.core import github
from roost.core import terminals


# ---------- updates ----------



def update_available():
    """Newer release version if there is one, checked at most once a day; '' otherwise or when offline."""
    try:
        cached = json.loads(config.UPDATE_CHECK.read_text())
    except Exception:
        cached = {}
    latest = cached.get("latest", "")
    if time.time() - cached.get("checked", 0) > 86400:
        try:
            latest = github.latest_release()
            config.UPDATE_CHECK.parent.mkdir(parents=True, exist_ok=True)
            config.UPDATE_CHECK.write_text(json.dumps({"checked": time.time(), "latest": latest}))
        except Exception as e:
            config.log(f"update check: {e}")
    return latest if latest and version_tuple(latest) > version_tuple(config.VERSION) else ""


def extract_app(tarball, dest):
    """Unpack a GitHub release tarball's roost/ package and bin/roost launcher into dest. Only plain
    files and folders inside those are written; anything else in the archive is skipped."""
    with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as tar:
        found = set()
        for m in tar.getmembers():
            parts = Path(m.name).parts
            if len(parts) < 2 or ".." in parts or m.name.startswith("/") or "__pycache__" in parts:
                continue
            if parts[1] != "roost" and parts[1:] != ("bin", "roost"):
                continue
            target = dest.joinpath(*parts[1:])
            if m.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif m.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(tar.extractfile(m).read())
                found.add(parts[1])
        if found != {"roost", "bin"}:
            raise RuntimeError("that release has no roost/ package and bin/roost launcher")
    (dest / "bin" / "roost").chmod(0o755)


def swap_app(folder):
    """Point ~/.roost/app/current at folder with one rename, then drop versions that are neither the
    new one nor the one it replaced."""
    current = config.APP / "current"
    old = Path(os.path.realpath(current)) if current.is_symlink() else None
    tmp = config.APP / ".current-link"
    tmp.unlink(missing_ok=True)
    os.symlink(folder.name, tmp)
    os.replace(tmp, current)
    keep = {Path(os.path.realpath(folder)), old}
    for d in config.APP.iterdir():
        if d.is_dir() and not d.is_symlink() and not d.name.startswith(".") and Path(os.path.realpath(d)) not in keep:
            shutil.rmtree(d, ignore_errors=True)


def self_update(me=None):
    """Install the latest release: its program files in ~/.roost/app, then its launcher at me (the
    roost command). Returns a process exit code."""
    if config.BREW:  # Homebrew replaces the program; this brings the plugin up to what it brought
        print("roost came from Homebrew: brew upgrade roost installs a new release.")
        return update_plugin(config.VERSION)
    me = Path(me or os.path.abspath(sys.argv[0]))
    if me.is_symlink():
        print(f"{me} links to a development checkout ({me.resolve().parent.parent}); update it with git instead.")
        return 1
    try:
        latest = github.latest_release()
    except Exception as e:
        print(f"Couldn't check for updates: {e}\nroost reads its releases from github.com/{REPO}; check your connection.")
        return 1
    if version_tuple(latest) <= version_tuple(config.VERSION):
        print(f"roost {config.VERSION} is the latest version.")
        return update_textual(me) | update_plugin(config.VERSION)
    folder, tmp = config.APP / latest, config.APP / f".{latest}.download"
    swapped = False  # once ~/.roost/app/current is the new version, even the old launcher runs it
    try:
        config.APP.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(tmp, ignore_errors=True)
        extract_app(github.download_tarball(latest), tmp)
        out = subprocess.run([sys.executable, str(tmp / "bin" / "roost"), "--version"], stdin=subprocess.DEVNULL,
                             capture_output=True, text=True, timeout=30).stdout
        if latest not in out:
            raise RuntimeError(f"the download didn't report version {latest}")
        shutil.rmtree(folder, ignore_errors=True)
        os.replace(tmp, folder)
        swap_app(folder)
        swapped = True
        shutil.copy2(me, me.with_name("roost.previous"))
        launcher = me.with_name(".roost-update")
        shutil.copy2(folder / "bin" / "roost", launcher)
        os.replace(launcher, me)
    except Exception as e:
        shutil.rmtree(tmp, ignore_errors=True)
        if not swapped:
            print(f"Update failed, nothing changed: {e}")
            return 1
        print(f"Update stopped partway: roost runs {latest} from {short(str(folder))}, but the launcher at {me} "
              f"wasn't replaced ({e}). Run roost update again to finish.")
        return update_textual(me) | update_plugin(latest) | 1
    try:
        config.UPDATE_CHECK.unlink()
    except OSError:
        pass
    print(f"Updated roost {config.VERSION} -> {latest}. The old launcher is saved as {me.with_name('roost.previous')}.")
    return update_textual(me) | update_plugin(latest)


def update_textual(me):
    """roost update's Textual step, run by the installed file, which knows the Textual it wants."""
    return subprocess.run([sys.executable, str(me), "setup", "textual"], stdin=subprocess.DEVNULL).returncode


def install_textual():
    """Make ~/.roost/venv with this Python (the system one) and bring Textual in it to TEXTUAL.
    Returns what it did; raises with pip's or venv's message when that fails."""
    py, want = config.VENV / "bin" / "python", TEXTUAL.split("==")[1]
    run = lambda argv, secs: subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=secs)
    if not py.exists():
        r = run([sys.executable, "-m", "venv", str(config.VENV)], 120)
        if r.returncode:
            raise RuntimeError((r.stderr or r.stdout).strip()[-400:] or "python3 -m venv failed")
    have = run([str(py), "-c", "import textual; print(textual.__version__)"], 60).stdout.strip()
    if have == want:
        return f"Textual {want} is in {short(str(config.VENV))}"
    r = run([str(py), "-m", "pip", "install", "--quiet", "--disable-pip-version-check", TEXTUAL], 900)
    if r.returncode:
        raise RuntimeError((r.stderr or r.stdout).strip()[-400:] or "pip install failed")
    return f"Installed Textual {want} in {short(str(config.VENV))}" + (f" (was {have})" if have else "")


def setup_textual():
    """roost setup textual: the screens' one dependency, in its own environment."""
    if config.BREW:
        print("  \033[32m✓\033[0m Textual came with Homebrew's roost")
        return 0
    print("Installing Textual, which draws roost's screens, in ~/.roost/venv…", flush=True)
    try:
        print(f"  \033[32m✓\033[0m {install_textual()}")
        return 0
    except Exception as e:
        print(f"  \033[33m!\033[0m Couldn't install Textual: {e}\n    roost --list still works; rerun roost setup textual to try again.")
        return 1


def update_plugin(version):
    """roost update's plugin step: the folder swap, then Claude Code's own copy through `claude plugin`.
    Claude Code's settings change only in roost setup."""
    try:
        print(install_plugin(version) + ".")
    except Exception as e:
        print(f"Plugin not updated: {e}.")
        return 1
    if not plugin.plugin_registered():
        print("Claude Code doesn't have the roost plugin enabled yet: run roost setup plugin.")
        return 0
    note = plugin.update_claude_copy(version)
    if note:
        print(note + ".")
    return 0


def setup(only=None):
    """Interactive first-run setup: checks prerequisites, picks a terminal and the Projects folder,
    and installs the plugin. only="plugin" does just the plugin step."""
    if only == "plugin":
        print("\n\033[1mroost plugin\033[0m\n")
        return setup_plugin()
    if only == "textual":
        return setup_textual()
    ok = lambda m: print(f"  \033[32m✓\033[0m {m}")
    bad = lambda m: print(f"  \033[33m!\033[0m {m}")
    print()
    wren.print_wren()
    print("\033[1mAgent Roost setup\033[0m\n")

    claude = shutil.which("claude")
    if not claude:
        bad("Claude Code (claude) isn't on your PATH. Install it first: https://code.claude.com")
        return 1
    ver = subprocess.run(["claude", "--version"], stdin=subprocess.DEVNULL, capture_output=True, text=True).stdout.strip()
    ok(f"Claude Code {ver}")
    try:
        json.loads(subprocess.run(["claude", "agents", "--json"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15).stdout)
        ok("claude agents --json works")
    except Exception:
        bad("claude agents --json didn't return data. Update Claude Code (claude update) and rerun setup.")
        return 1
    if setup_textual():
        return 1
    here = str(Path(sys.argv[0]).resolve().parent)
    if shutil.which("roost") is None:
        bad(f"roost isn't on your PATH. Add this to ~/.zshrc:  export PATH=\"{here}:$PATH\"")
    else:
        ok(f"roost is on your PATH ({shutil.which('roost')})")

    installed = [k for k, v in TERMINALS.items() if app_installed(v["app"])]
    detected = terminals.detect_terminal()
    print("\nWhen you press enter on a session, where should it open?\n")
    options = installed + ["same"]
    for i, k in enumerate(options, 1):
        label = "This terminal (replaces roost, no new windows)" if k == "same" else f"New {TERMINALS[k]['label']} window"
        print(f"  {i}. {label}{'  (detected)' if k == detected else ''}")
    default = options.index(detected) + 1 if detected in options else 1
    try:
        raw = input(f"\nChoose 1-{len(options)} [{default}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print(); return 1
    choice = options[int(raw) - 1] if raw.isdigit() and 1 <= int(raw) <= len(options) else options[default - 1]

    if choice != "same":
        print(f"\nOpening a test {TERMINALS[choice]['label']} window. If macOS asks to let your terminal")
        print(f"control {TERMINALS[choice]['app']}, click Allow.")
        line = "echo 'Agent Roost can open windows here. Closing…'; sleep 2"
        res = terminals.osa(TERMINALS[choice]["new"], HOME, line + "; exit", os.environ.get("SHELL", "/bin/zsh") + " -lc " + shlex.quote(line))
        if res is None:
            bad("The test window didn't open. Allow it in System Settings → Privacy & Security → Automation, then rerun roost setup.")
            return 1
        ok("Test window opened")

    current = config.read_config().get("projects_dir") or DEFAULT_PROJECTS
    print("\nWhere do your projects live? One folder per project; roost never moves what's there.")
    try:
        raw = input(f"Projects folder [{current}]: ").strip() or current
    except (EOFError, KeyboardInterrupt):
        print(); return 1
    folder = Path(os.path.expanduser(raw))
    if not folder.is_dir():
        try:
            make = input(f"{short(str(folder))} doesn't exist. Create it? [Y/n]: ").strip().lower() in ("", "y", "yes")
        except (EOFError, KeyboardInterrupt):
            print(); return 1
        if make:
            folder.mkdir(parents=True, exist_ok=True)
            ok(f"Created {short(str(folder))}")
        else:
            bad(f"{short(str(folder))} doesn't exist yet; the Projects tab will say so until it does")
    else:
        ok(f"Projects folder {short(str(folder))}")

    config.save_config(terminal=choice, projects_dir=short(str(folder)))
    ok(f"Saved to {short(str(config.CONFIG))}")

    print("\nInstalling roost's Claude Code plugin (its skills, like /roost:pickup and /roost:wrap-up).")
    if setup_plugin():
        print("\nThe rest is set up. Fix the plugin, then run \033[1mroost setup plugin\033[0m.\n")
        return 1
    print("\nDone. Run \033[1mroost\033[0m to open your projects and sessions.\n")
    return 0
