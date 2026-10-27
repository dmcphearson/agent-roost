"""roost: where your Claude sessions come home to roost.

core/ reads and changes Sessions, projects, notes, and Handoffs; app/ is the screens' shell and the
Textual front end; tabs/ has one module per tab. bin/roost starts roost.cli."""
import importlib

# every module, lowest first: a module imports names only from the ones before it
MODULES = ["core.config", "core.wren", "core.github", "core.formatting", "core.mdfiles", "core.pricing", "core.sessiondata",
           "core.notedata", "core.projectdata", "core.statsdata", "core.plugin", "core.handoffs", "core.summaries", "core.advice",
           "core.adopt", "core.terminals", "core.install", "app.shell", "app.widgets", "tabs.sessions", "tabs.projects",
           "tabs.notes", "tabs.stats", "tabs.unfiled", "app.ui", "app.screen", "demo", "cli"]


def modules():
    return [importlib.import_module(f"roost.{m}") for m in MODULES]


def owner(name):
    """The module that defines name: the lowest one that has it, since imports only go downward."""
    return next((m for m in modules() if name in vars(m)), None)


def patch(**names):
    """Replace module-level names where roost defines them (roost --demo's stand-ins)."""
    for name, value in names.items():
        m = owner(name)
        if m is None:
            raise AttributeError(f"roost has no {name}")
        setattr(m, name, value)
