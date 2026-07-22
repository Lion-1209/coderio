"""Tests for P0 stability fixes: session crash recovery and config validation."""

import json
from pathlib import Path

import pytest

from coderio.session.store import Session
from coderio.config.loader import _from_dict


class TestSessionCrashRecovery:
    """Session.load must tolerate a trailing corrupted line (crash mid-write)."""

    def test_load_skips_corrupted_trailing_line(self, tmp_path):
        """A partial/garbled last line (from a crash during append) should be
        skipped, not crash the entire session load."""
        path = tmp_path / "test.jsonl"
        path.write_text(
            json.dumps({"type": "meta", "model": "test"})
            + "\n"
            + json.dumps({"role": "user", "content": "hello"})
            + "\n"
            + json.dumps({"role": "assistant", "content": "hi"})
            + "\n"
            + '{"role": "user", "content": "truncated',  # no closing brace
            encoding="utf-8",
        )
        session = Session.load(path)
        assert session.id == "test"
        assert len(session.messages) == 2  # user + assistant, corrupted line skipped

    def test_load_skips_blank_lines(self, tmp_path):
        path = tmp_path / "blanks.jsonl"
        path.write_text(
            json.dumps({"role": "user", "content": "a"})
            + "\n\n\n"
            + json.dumps({"role": "assistant", "content": "b"})
            + "\n",
            encoding="utf-8",
        )
        session = Session.load(path)
        assert len(session.messages) == 2

    def test_load_clean_session_unaffected(self, tmp_path):
        path = tmp_path / "clean.jsonl"
        path.write_text(
            json.dumps({"type": "meta", "model": "m"})
            + "\n"
            + json.dumps({"role": "user", "content": "hi"})
            + "\n",
            encoding="utf-8",
        )
        session = Session.load(path)
        assert session.meta.get("model") == "m"
        assert len(session.messages) == 1


class TestConfigValidation:
    """_from_dict must validate config values and give clear errors."""

    def test_valid_config_loads(self):
        cfg = _from_dict({"model": {"default": "test-model"}})
        assert cfg.model.default == "test-model"

    def test_string_max_output_tokens_raises(self):
        with pytest.raises(ValueError, match="必须是整数"):
            _from_dict({"model": {"max_output_tokens": "16384"}})

    def test_bool_max_tool_rounds_raises(self):
        with pytest.raises(ValueError, match="必须是整数"):
            _from_dict({"tools": {"max_tool_rounds": True}})

    def test_invalid_permission_mode_raises(self):
        with pytest.raises(ValueError, match="permission_mode.*无效"):
            _from_dict({"tools": {"permission_mode": "confim"}})  # typo

    def test_valid_permission_modes_accepted(self):
        for mode in ("confirm", "plan", "auto"):
            cfg = _from_dict({"tools": {"permission_mode": mode}})
            assert cfg.tools.permission_mode == mode

    def test_permission_mode_case_insensitive(self):
        cfg = _from_dict({"tools": {"permission_mode": "AUTO"}})
        assert cfg.tools.permission_mode == "auto"
