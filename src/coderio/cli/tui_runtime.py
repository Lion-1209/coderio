"""Input dispatch + session lifecycle for the Textual TUI.

Extracted from tui.run_tui (S3 decomposition): the closures that route user
input — custom-command expansion, slash dispatch, picker callbacks, and the
deepagents engine call — are runtime WIRING, not UI rendering. A dedicated
module makes them importable and unit-testable without booting Textual, and
gives later features (e.g. checkpoint timeline) a seam to hook session
lifecycle.

Threading contract (unchanged from the closure era): handle_input runs in the
agent worker thread; every touch of the Textual app goes through
tui.call_from_thread / tui._add_text (which marshal to the main thread).
"""

from __future__ import annotations

from typing import Any

from coderio.cli.custom_commands import CustomCommand, try_expand_line
from coderio.cli.tui_onboarding import OnboardingScreen
from coderio.cli.tui_screens import (
    ModePickerScreen,
    ProfilePickerScreen,
    SessionPickerScreen,
)
from coderio.config import load_config


def _switch_active_profile(profile_name: str) -> str:
    """Write the chosen profile name to config.toml as active_profile.

    Read-modify-write so other sections and the profiles array are preserved.
    Returns the name written (empty string if it couldn't be written). Called by
    the /profile picker callback after the user picks a profile.
    """
    import tomllib
    from pathlib import Path

    import tomli_w

    config_path = Path.home() / ".coderio" / "config.toml"
    data: dict = {}
    if config_path.is_file():
        try:
            with open(config_path, "rb") as f:
                data = tomllib.load(f)
        except Exception:
            data = {}
    data["active_profile"] = profile_name
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "wb") as f:
        tomli_w.dump(data, f)
    return profile_name


class TuiRuntime:
    """Routes TUI input lines and owns the mutable runtime holder (``rt``).

    ``rt`` mirrors the original closure state exactly: cfg / model / gate /
    session are swapped IN PLACE by /model, /mode, /resume so subsequent turns
    pick up the new values. ``bind`` is two-phase because the permission gate
    needs the live TUI reference (confirm mode would otherwise deadlock on
    input() against Textual's terminal takeover) while the TUI needs
    handle_input at construction time.
    """

    def __init__(
        self,
        *,
        store,
        active,
        tools,
        creds_path,
        custom_commands: dict[str, CustomCommand],
    ) -> None:
        self.store = store
        self.active = active
        self.tools = tools
        self.creds_path = creds_path
        self.custom_commands = custom_commands
        # Typed as Any on purpose: the runtime duck-types the TUI surface it
        # needs (_add_text / call_from_thread / push_screen / usage / ...),
        # which keeps this module import-cycle-free from tui.py and lets unit
        # tests drive real control flow with stubs instead of a Textual app.
        self.tui: Any = None
        self.rt: dict[str, Any] = {}

    def bind(self, tui, *, cfg, model, gate, session) -> None:
        """Attach the live CoderioTUI and seed the runtime holder."""
        self.tui = tui
        self.rt.update({"cfg": cfg, "model": model, "session": session})
        from coderio.cli.repl import build_gate

        # Rebuild the gate WITH the TUI attached (the build_runtime gate was
        # constructed before any TUI existed).
        self.rt["gate"] = build_gate(cfg, console=None, tui=tui)

    # ------------------------------------------------------------------ input

    def handle_input(self, line: str) -> None:
        # Two-phase construction guard: bind() seeds rt; without it every path
        # would die on KeyError('cfg') deep inside a worker thread. Fail loud
        # and self-explanatory instead (seam-test finding).
        if not self.rt:
            raise RuntimeError("TuiRuntime.bind() must be called before handle_input")
        # Custom commands expand FIRST: "/name args" → template body becomes
        # the user prompt. The expanded text goes STRAIGHT to the engine path
        # below — NEVER back into handle_slash. Re-entry would let a repo file
        # with body "/mode full" flip the permission gate, or "/export <path>"
        # exfiltrate the session (adversarial-review finding); hence `elif`,
        # not a second sequential `if`.
        expanded = try_expand_line(line, self.custom_commands)
        if expanded is not None:
            line = expanded
        elif line.startswith("/"):
            self._handle_slash_line(line)
            return
        self._send_to_engine(line)

    def _handle_slash_line(self, line: str) -> None:
        from pathlib import Path as _P

        from coderio.cli.commands import ReplContext, handle_slash
        from coderio.session.store import Session

        rt = self.rt
        tui = self.tui
        ctx = ReplContext(
            available_skills=self.store.names(),
            active_skills_names={s.name for s in self.active.all()},
            permission_mode=rt["gate"].mode,
            model_name=rt["cfg"].model.default,
            provider_id=rt["cfg"].model.provider_id,
            api_key="",
            base_url=rt["cfg"].model.base_url,
            recent_sessions=Session.list_recent(_P(rt["cfg"].session.save_dir).expanduser()),
            session_save_dir=str(_P(rt["cfg"].session.save_dir).expanduser()),
            session=rt["session"],
            profiles=rt["cfg"].profiles,
            active_profile=rt["cfg"].active_profile,
            usage=tui.usage,
            stream=tui,
            custom_commands=self.custom_commands,
        )
        res = handle_slash(line, ctx)
        # /resume with no arg → open the interactive picker instead of printing.
        # push_screen MUST run on the main thread (it touches the Textual
        # event loop); handle_input runs in the agent's background thread, so
        # dispatch via call_from_thread — same pattern as _add_text.
        if res.message == "__OPEN_PICKER__":
            summaries = Session.summaries(_P(rt["cfg"].session.save_dir).expanduser())

            def _on_picked(sid):
                """Picker dismissed: sid is the chosen id, or None if cancelled."""
                if sid is None:
                    return
                self.load_session(sid)

            tui.call_from_thread(
                tui.push_screen,
                SessionPickerScreen(
                    summaries,
                    save_dir=str(_P(rt["cfg"].session.save_dir).expanduser()),
                    active_session_id=getattr(rt["session"], "id", ""),
                ),
                _on_picked,
            )
            return
        if res.message == "__OPEN_ONBOARDING__":
            # /setup → open the OnboardingScreen to reconfigure provider/model.
            # After it completes, rebuild the runtime with the new config.
            def _on_reconfigured(result):
                if result is None:
                    return
                # Reload config + rebuild model with the new provider/key.
                from pathlib import Path as _Path

                from coderio.llm import build_chat_model as _build

                creds = _Path.home() / ".coderio" / "credentials"
                new_cfg = load_config(search_from=".")
                rt["cfg"] = new_cfg
                rt["model"] = _build(new_cfg, creds_path=creds)
                tui._add_text(
                    f"✅ 已重新配置 → {new_cfg.model.default}（{new_cfg.model.provider_id}）",
                    style="bold green",
                )

            tui.call_from_thread(tui.push_screen, OnboardingScreen(), _on_reconfigured)
            return
        if res.message == "__OPEN_PROFILE_PICKER__":
            # /profile → open the ProfilePickerScreen. After the user picks,
            # write active_profile to config.toml and rebuild the model.
            profiles = rt["cfg"].profiles or []
            active_name = rt["cfg"].active_profile
            if not profiles:
                tui._add_text("[yellow]还没有保存的 profile。用 /setup 添加一个配置。[/yellow]")
                return

            def _on_profile_picked(name):
                if name is None or name == active_name:
                    return  # cancelled or re-picked the same one
                _switch_active_profile(name)
                from coderio.llm import build_chat_model as _build

                new_cfg = load_config(search_from=".")
                rt["cfg"] = new_cfg
                rt["model"] = _build(new_cfg, creds_path=self.creds_path)
                tui._add_text(f"✅ 已切换到配置 → {name}", style="bold green")

            tui.call_from_thread(
                tui.push_screen,
                ProfilePickerScreen(profiles, active_name),
                _on_profile_picked,
            )
            return
        if res.message == "__OPEN_MODE_PICKER__":
            # /mode (no arg) → open the ModePickerScreen. After the user
            # picks, rebuild the gate with the new permission mode.
            current_mode = rt["gate"].mode

            def _on_mode_picked(mode):
                if mode is None or mode == current_mode:
                    return  # cancelled or re-picked the same one
                from dataclasses import replace as _replace

                from coderio.cli.repl import build_gate

                c = _replace(rt["cfg"], tools=_replace(rt["cfg"].tools, permission_mode=mode))
                rt["cfg"] = c
                rt["gate"] = build_gate(c, console=None, tui=tui)
                tui._add_text(f"✅ 已切换到 {mode} 模式", style="bold green")

            tui.call_from_thread(
                tui.push_screen,
                ModePickerScreen(current_mode),
                _on_mode_picked,
            )
            return
        if res.message:
            tui._add_text(res.message)
        if not res.continue_loop:
            tui.call_from_thread(tui.exit)
            return
        # /resume <explicit-id> path: load straight from the result.
        if res.new_session_id:
            self.load_session(res.new_session_id)
            return
        if res.reset_runtime:
            from dataclasses import replace as _replace

            from coderio.cli.repl import build_gate
            from coderio.llm import build_chat_model

            c = rt["cfg"]
            if res.new_permission_mode:
                c = _replace(
                    c,
                    tools=_replace(c.tools, permission_mode=res.new_permission_mode),
                )
                rt["cfg"] = c
                rt["gate"] = build_gate(c, console=None, tui=tui)
            cmd_name = line.strip().split(maxsplit=1)[0]
            if cmd_name == "/clear":
                # /clear: start a fresh session + wipe active skills + clear
                # the history pane. Without this the old session's messages
                # keep being fed to the model (it reads session.messages).
                self.clear_context()
                return
            if cmd_name == "/model":
                parts = line.strip().split(maxsplit=1)
                if len(parts) > 1 and parts[1].strip():
                    c = _replace(c, model=_replace(c.model, default=parts[1].strip()))
                    rt["cfg"] = c
                    rt["model"] = build_chat_model(c, creds_path=self.creds_path)

    # ----------------------------------------------------------------- engine

    def _send_to_engine(self, line: str) -> None:
        from coderio.agent.deep_loop import run_deep_agent
        from coderio.cli.multimodal import build_user_content, extract_images
        from coderio.cli.repl import build_turn_spec

        rt = self.rt
        imgs = extract_images(line)
        if imgs:
            self.tui._add_text(
                f"📎 已附加 {len(imgs)} 张图片: " + ", ".join(p for p, _, _ in imgs),
                style="dim",
            )
        user_content = build_user_content(line)
        # deepagents engine: provides context management, subagents, filesystem.
        # coderio's harness + permission + command review run as middleware.
        # Rebuilt per turn via the SAME factory as headless runs (P2-1):
        # /model and /clear swap rt["model"]/rt["session"] between turns, so a
        # cached spec would run on stale objects.
        spec = build_turn_spec(
            rt["cfg"],
            model=rt["model"],
            gate=rt["gate"],
            skill_store=self.store,
            active_skills=self.active,
            tools=self.tools,
        )
        run_deep_agent(
            user_input=user_content,
            spec=spec,
            session=rt["session"],
            stream=self.tui,
        )

    # ------------------------------------------------------- session lifecycle

    def load_session(self, sid: str) -> None:
        """Swap the active session to a loaded one, clear skills, render history.

        Called after the picker picks a session (or /resume <id> is given). The
        old session's jsonl stays on disk; we just point the runtime at the new
        Session object so subsequent turns continue that conversation.
        """
        from pathlib import Path as _P

        from coderio.session.store import Session

        rt = self.rt
        save_dir = _P(rt["cfg"].session.save_dir).expanduser()
        rt["session"] = Session.load_by_id(save_dir, sid)
        self.active.clear()
        # Render the resumed conversation into the history pane so the user sees
        # context they're continuing, not a blank screen.
        # Count only conversation messages (exclude system-role metadata like
        # phase_timeline / context_summary so the count matches what's displayed).
        convo_msgs = [m for m in rt["session"].messages if m.role != "system"]
        self.tui._add_text(f"↩ 已恢复会话 {sid}（{len(convo_msgs)} 条历史消息）", style="bold green")
        for m in rt["session"].messages:
            if m.role == "user":
                c = m.content
                if isinstance(c, list):
                    c = " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
                self.tui._add_text(f"▸ you {c}", style="bold cyan")
            elif m.role == "assistant":
                self.tui._add_text(f"  {m.content[:200]}", style="blue")

    def clear_context(self) -> None:
        """Start a fresh session + clear active skills + wipe the history pane.

        Backs the /clear command. Without this the old session's messages keep
        being fed to the model (loop.py reads session.messages), so 'context
        cleared' was previously a lie — the model still saw the full history.
        """
        from pathlib import Path as _P

        from coderio.session.store import Session

        rt = self.rt
        save_dir = _P(rt["cfg"].session.save_dir).expanduser()
        rt["session"] = Session.create(
            save_dir,
            {
                "model": rt["cfg"].model.default,
                "provider": rt["cfg"].model.provider,
            },
        )
        self.active.clear()
        # Wipe the visible history pane so the user sees a clean slate (the old
        # session's jsonl is preserved on disk — /resume can still get it back).
        self.tui._clear_history()
        self.tui._add_text("🆕 已开启新会话（历史已清空，可用 /resume 恢复）", style="bold green")
