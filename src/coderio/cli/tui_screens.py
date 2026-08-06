"""TUI picker screens — extracted from tui.py for modularity.

Contains the three modal picker screens used by the TUI:
  - ProfilePickerScreen: /profile (switch saved [[profiles]])
  - ModePickerScreen:    /mode with no argument (switch permission mode)
  - SessionPickerScreen: /resume (resume or delete recent sessions)

All three are ModalScreen subclasses — pure popups with no top-level coupling
to the rest of coderio, so this module can be imported standalone.
"""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, ListItem, ListView, Static


class ProfilePickerScreen(ModalScreen[str | None]):
    """Interactive profile picker (/profile).

    Lists all saved [[profiles]] from config.toml as a ListView — each row shows
    the profile name with its provider/model as a dim subtitle, and the active
    profile is marked ★. ↑↓ navigates, Enter switches (dismisses with the chosen
    profile name), Esc cancels (dismisses None). Mirrors the SessionPickerScreen
    UX so both pickers feel the same.
    """

    CSS = """
    ProfilePickerScreen { align: center middle; }
    #profile-box {
        width: 70%; height: auto; max-height: 70%; border: round $accent;
        background: $surface; padding: 1 2;
    }
    #profile-title { text-align: center; margin-bottom: 1; }
    #profile-list { height: auto; max-height: 16; }
    ProfilePickerScreen ListItem { padding: 0 1; }
    ProfilePickerScreen ListItem > Widget :hover { background: $boost; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "取消", show=True),
    ]

    def __init__(self, profiles: list, active_name: str = "") -> None:
        super().__init__()
        self._profiles = profiles
        self._active_name = active_name

    def compose(self) -> ComposeResult:
        with Vertical(id="profile-box"):
            yield Static(
                "[bold magenta]切换配置[/bold magenta]  ↑↓ 选择 · Enter 切换 · Esc 取消",
                id="profile-title",
            )
            yield ListView(id="profile-list")

    def on_mount(self) -> None:
        lv = self.query_one("#profile-list", ListView)
        for p in self._profiles:
            star = "★ " if p.name == self._active_name else "  "
            lv.append(
                ListItem(
                    Static(f"{star}{p.name}  [dim]{p.provider_id} · {p.model}[/dim]"),
                    name=p.name,
                )
            )
        try:
            lv.index = 0
        except Exception:
            pass
        lv.focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Enter on a row → switch to that profile."""
        self.dismiss(event.item.name)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ModePickerScreen(ModalScreen[str | None]):
    """Visual permission-mode picker (/mode with no argument).

    Lists the three modes (confirm / plan / auto) as a ListView with the
    current mode marked ★. ↑↓ navigates, Enter selects (dismisses with the
    mode name), Esc cancels. Same UX pattern as ProfilePickerScreen.
    """

    CSS = """
    ModePickerScreen { align: center middle; }
    #mode-box {
        width: 60%; height: auto; max-height: 60%; border: round $accent;
        background: $surface; padding: 1 2;
    }
    #mode-title { text-align: center; margin-bottom: 1; }
    #mode-list { height: auto; max-height: 8; }
    ModePickerScreen ListItem { padding: 0 1; }
    ModePickerScreen ListItem > Widget :hover { background: $boost; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "取消", show=True),
    ]

    # mode -> human-readable description shown as a dim subtitle.
    _MODE_INFO = {
        "plan": "只读模式，阻止所有写操作（最安全）",
        "confirm": "每次写操作前确认",
        "auto_edit": "自动编辑文件，bash/网络仍需确认",
        "full": "全自动，不确认任何操作（需信任）",
    }

    def __init__(self, active_mode: str = "") -> None:
        super().__init__()
        self._active_mode = active_mode

    def compose(self) -> ComposeResult:
        with Vertical(id="mode-box"):
            yield Static(
                "[bold magenta]切换权限模式[/bold magenta]  ↑↓ 选择 · Enter 切换 · Esc 取消",
                id="mode-title",
            )
            yield ListView(id="mode-list")

    def on_mount(self) -> None:
        lv = self.query_one("#mode-list", ListView)
        for mode in ("plan", "confirm", "auto_edit", "full"):
            star = "★ " if mode == self._active_mode else "  "
            desc = self._MODE_INFO.get(mode, "")
            lv.append(
                ListItem(
                    Static(f"{star}{mode}  [dim]{desc}[/dim]"),
                    name=mode,
                )
            )
        try:
            lv.index = 0
        except Exception:
            pass
        lv.focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Enter on a row → switch to that mode."""
        self.dismiss(event.item.name)

    def action_cancel(self) -> None:
        self.dismiss(None)


class SessionPickerScreen(ModalScreen[str | None]):
    """Interactive session picker (Claude-Code-style /resume).

    Shows recent sessions as a scrollable list — each row has the first user
    message (so the user recognizes the conversation by what they asked, not by
    an opaque id), the message count, and the time. ↑↓ navigates, Enter resumes,
    Esc cancels. Typing filters the list by the summary text. Dismisses with the
    chosen session id (string) or None (cancelled).
    """

    CSS = """
    SessionPickerScreen {
        align: center middle;
    }
    #picker-box {
        width: 80%; height: 70%; border: thick $accent; background: $surface;
        padding: 1 2;
    }
    #picker-title { text-align: center; color: $accent; margin-bottom: 1; }
    #picker-filter {
        dock: bottom; margin-top: 1; border: round $accent 50%;
    }
    #picker-filter:focus { border: round $accent; }
    #picker-list { height: 1fr; border: none; }
    .picker-row { padding: 0 1; }
    .picker-row:first-child { margin-top: 0; }
    .picker-summary { color: $text; }
    .picker-meta { color: $text-muted; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "取消", show=True),
        Binding("delete", "delete_selected", "删除", show=True),
    ]

    def __init__(self, summaries: list[dict], save_dir: str = "", active_session_id: str = "") -> None:
        super().__init__()
        self._all = summaries  # full list; filtered view derived on typing
        self._filter = ""
        self._delete_confirm_sid: str | None = None  # pending delete confirmation
        # The directory where session files actually live. Must match the
        # source that populated `summaries` — hardcoding ~/.coderio/sessions
        # would delete the wrong files when a custom save_dir is configured.
        self._save_dir = save_dir
        self._active_session_id = active_session_id  # cannot be deleted

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-box"):
            yield Static(
                "[bold]恢复会话[/bold]  ↑↓ 选择 · Enter 恢复 · Del 删除(按2次确认) · Esc 取消 · 输入过滤",
                id="picker-title",
            )
            yield ListView(id="picker-list")
            yield Input(placeholder="输入关键字过滤（首条消息 / 时间）", id="picker-filter")

    def on_mount(self) -> None:
        self._populate()
        # Focus the ListView (not the filter input) so ↑↓ navigation works
        # immediately. The filter input still receives keystrokes via on_key
        # forwarding when the user starts typing.
        lv = self.query_one("#picker-list", ListView)
        try:
            lv.index = 0
        except Exception:
            pass
        lv.focus()

    def on_key(self, event) -> None:
        """Forward printable keys to the filter input; let ListView handle nav."""
        # Cancel pending delete on any key other than Del.
        if event.key != "delete" and self._delete_confirm_sid is not None:
            self._delete_confirm_sid = None
        inp = self.query_one("#picker-filter", Input)
        if event.key in ("up", "down", "enter", "escape", "delete", "pageup", "pagedown"):
            return  # ListView / bindings handle these natively when it has focus
        # Printable character → route to filter input for live search
        if event.character and len(event.character) == 1 and event.character.isprintable():
            inp.focus()
            inp.value += event.character
            event.prevent_default()
        elif event.key == "backspace":
            inp.focus()
            if inp.value:
                inp.value = inp.value[:-1]
            event.prevent_default()

    def _populate(self) -> None:
        """Rebuild the list from the (filtered) summaries."""
        lv = self.query_one("#picker-list", ListView)
        lv.clear()
        f = self._filter.lower()
        for s in self._all:
            label = s["first_user"] or "(空会话)"
            if f and f not in label.lower() and f not in s["mtime"].lower() and f not in s["id"].lower():
                continue
            row = Static(
                f"[{s['mtime']}] {label}\n"
                f"  [dim]{s['message_count']} 条消息 · {s.get('model') or '?'} · {s['id']}[/dim]",
                classes="picker-row",
            )
            lv.append(ListItem(row, name=s["id"]))

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "picker-filter":
            self._filter = event.value
            self._populate()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Enter on a row → resume that session."""
        self.dismiss(event.item.name)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_delete_selected(self) -> None:
        """Delete the currently highlighted session (jsonl + sqlite).

        Two-step confirmation: first Del selects the item for deletion (shows
        a confirmation prompt), second Del on the SAME item executes. Moving
        the cursor or pressing any other key cancels the pending delete.
        """
        lv = self.query_one("#picker-list", ListView)
        if not lv.children or lv.index is None:
            return
        item = lv.children[lv.index]
        sid = item.name or ""
        if not sid:
            return
        # Protect the currently active session — deleting it would corrupt
        # the running conversation (the Session object still holds a file
        # handle, and the next append would recreate a file without history).
        if sid == self._active_session_id:
            self.app.bell()
            return
            return

        # Two-step confirmation: if there's a pending delete for THIS sid,
        # execute it. Otherwise, mark it pending and wait for second Del.
        if self._delete_confirm_sid == sid:
            self._delete_confirm_sid = None  # clear before executing
            self._do_delete(lv, sid)
        else:
            # First Del — mark pending. User must press Del again to confirm.
            self._delete_confirm_sid = sid
            self.app.bell()

    def _do_delete(self, lv: ListView, sid: str) -> None:
        """Actually delete the session files and update the list."""
        # Use the actual save_dir (from config), not a hardcoded path.

        # Use the actual save_dir (from config), not a hardcoded path.
        if self._save_dir:
            sessions_dir = Path(self._save_dir).expanduser()
        else:
            sessions_dir = Path.home() / ".coderio" / "sessions"
        deleted_any = False
        delete_failed = False
        for suffix in (".jsonl", ".sqlite", ".sqlite-wal", ".sqlite-shm"):
            p = sessions_dir / f"{sid}{suffix}"
            if p.exists():
                try:
                    p.unlink()
                    deleted_any = True
                except Exception:
                    delete_failed = True
        # Only remove from UI if deletion succeeded. If it failed, keep the
        # entry so the user knows it's still on disk (no false success).
        if deleted_any and not delete_failed:
            self._all = [s for s in self._all if s["id"] != sid]
            self._populate()
            if not self._all:
                self.dismiss(None)  # no sessions left → close picker
            else:
                try:
                    lv.index = 0
                except Exception:
                    pass
        elif delete_failed:
            # Show a brief error notice without closing the picker.
            self.app.bell()
