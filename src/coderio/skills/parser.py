from __future__ import annotations

import re
from pathlib import Path

from coderio.skills.models import Skill

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)


def _parse_frontmatter(fm_text: str) -> dict:
    data = {}
    for line in fm_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        data[key.strip()] = val.strip()
    return data


def parse_skill_file(path: Path, lazy=False, source_layer="user") -> Skill:
    path = Path(path)
    raw = path.read_text(encoding="utf-8", errors="replace")
    m = _FRONTMATTER_RE.match(raw)
    if not m:
        raise ValueError(f"SKILL.md missing frontmatter: {path}")
    fm = _parse_frontmatter(m.group(1))
    if "name" not in fm:
        raise ValueError(f"SKILL.md missing required 'name' field: {path}")
    skill = Skill(
        name=fm["name"],
        description=fm.get("description", ""),
        dir_path=path.parent,
        source_layer=source_layer,
        _loaded=False,
    )
    if not lazy:
        skill.load_body()
    return skill


def read_body(path: Path) -> str:
    raw = Path(path).read_text(encoding="utf-8", errors="replace")
    m = _FRONTMATTER_RE.match(raw)
    if not m:
        return raw
    return m.group(2)
