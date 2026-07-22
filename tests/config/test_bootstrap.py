from pathlib import Path

from coderio.config.bootstrap import ensure_user_dirs, STRUCTURE


def test_creates_skeleton(tmp_path):
    ensure_user_dirs(user_dir=tmp_path)
    for rel in STRUCTURE:
        assert (tmp_path / rel).is_dir(), f"missing {rel}"
    cfg = tmp_path / ".coderio" / "config.toml"
    assert cfg.is_file()
    text = cfg.read_text(encoding="utf-8")
    assert "[model]" in text
    assert "permission_mode" in text


def test_idempotent(tmp_path):
    ensure_user_dirs(user_dir=tmp_path)
    cfg = tmp_path / ".coderio" / "config.toml"
    cfg.write_text('# custom\n[model]\ndefault = "x"\n', encoding="utf-8")
    ensure_user_dirs(user_dir=tmp_path)
    assert cfg.read_text(encoding="utf-8").startswith("# custom")
