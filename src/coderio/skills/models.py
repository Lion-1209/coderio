from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class Skill:
    name: str
    description: str
    dir_path: Path
    source_layer: str = ""
    _body: str = ""
    _loaded: bool = False

    @property
    def body(self):
        if not self._loaded:
            self.load_body()
        return self._body

    def load_body(self):
        from coderio.skills.parser import read_body

        self._body = read_body(self.dir_path / "SKILL.md")
        self._loaded = True
