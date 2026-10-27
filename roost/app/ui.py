"""The screens, composed: the shell plus each tab's module."""

from roost.app.shell import Shell, Tab
from roost.tabs.sessions import SessionsTab, SessionsView
from roost.tabs.projects import ProjectsTab, ProjectsView
from roost.tabs.notes import NotesTab, NotesView
from roost.tabs.stats import StatsTab, StatsPage
from roost.tabs.unfiled import UnfiledTab, UnfiledView


class UI(ProjectsTab, UnfiledTab, SessionsTab, NotesTab, StatsTab, Shell):
    """The screens: what each tab lists and draws, and what its keys do. Drawing goes through a curses
    window size (self.scr); each tab's widgets draw what it works out (see roost.app.screen)."""

    # Each tab: its name, how it draws, how it handles keys, what ? shows, and whether a prompt of its
    # own is taking every key, and the widget that draws it.
    TABS = [Tab("Projects", ProjectsTab.draw_projects, ProjectsTab.keys_projects, ProjectsTab.PROJECTS_HELP,
                ProjectsTab.projects_typing, ProjectsView),
            Tab("Unfiled", UnfiledTab.draw_unfiled, UnfiledTab.keys_unfiled, UnfiledTab.UNFILED_HELP, UnfiledTab.unfiled_typing,
                UnfiledView),
            Tab("Sessions", SessionsTab.draw_sessions, SessionsTab.keys_sessions, SessionsTab.SESSIONS_HELP,
                SessionsTab.sessions_typing, SessionsView),
            Tab("Notes", NotesTab.draw_notes, NotesTab.keys_notes, NotesTab.NOTES_HELP, NotesTab.notes_typing,
                NotesView),
            Tab("Stats", StatsTab.draw_stats, StatsTab.keys_stats, StatsTab.STATS_HELP, StatsTab.stats_typing, StatsPage)]
