"""What this computer can do that differs between macOS and Linux: the clipboard, a file manager,
and where Claude Code keeps managed settings. Read at call time, so tests can set the platform."""
import os, shutil, sys
from pathlib import Path


def mac():
    return sys.platform == "darwin"


def copy_command():
    """The program to pipe text into to put it on the clipboard, or None when there's none and the
    screen sends it to the terminal itself (OSC 52). Inside tmux, tmux passes it on to the local
    clipboard, which also works over SSH."""
    env = os.environ
    if mac():
        return ["pbcopy"]
    if env.get("TMUX") and shutil.which("tmux"):
        return ["tmux", "load-buffer", "-w", "-"]
    if env.get("WAYLAND_DISPLAY") and shutil.which("wl-copy"):
        return ["wl-copy"]
    if env.get("DISPLAY"):
        if shutil.which("xclip"):
            return ["xclip", "-selection", "clipboard"]
        if shutil.which("xsel"):
            return ["xsel", "--clipboard", "--input"]
    return None


def open_command(path, reveal=False):
    """The program that shows path in the file manager (reveal selects it there), or None when
    this computer has no display to show one on."""
    if mac():
        return ["open", "-R", path] if reveal else ["open", path]
    if (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")) and shutil.which("xdg-open"):
        return ["xdg-open", os.path.dirname(path) if reveal else path]  # xdg-open can't select a file
    return None


def file_manager():
    """The file manager's name for menus, or None when open_command() can't show anything."""
    if mac():
        return "Finder"
    return "Files" if open_command("/") else None


def managed_settings():
    """Claude Code's managed settings file, which overrides the user's settings."""
    if mac():
        return Path("/Library/Application Support/ClaudeCode/managed-settings.json")
    return Path("/etc/claude-code/managed-settings.json")
