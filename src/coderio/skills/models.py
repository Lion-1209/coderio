from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Skill:
    name: str
    description: str
    dir_path: Path
    source_layer: str = ""
    _body: str = ""
    _loaded: bool = False
    # Lazily-loaded executable tools carried by this skill (from tools.py).
    # None = not yet attempted; [] = attempted but none found.
    _tools: list | None = field(default=None, repr=False)

    @property
    def body(self):
        if not self._loaded:
            self.load_body()
        return self._body

    def load_body(self):
        from coderio.skills.parser import read_body

        self._body = read_body(self.dir_path / "SKILL.md")
        self._loaded = True

    def load_tools(self) -> list:
        """Import executable tools from this skill's tools.py, if present.

        Convention: a skill directory may contain a ``tools.py`` whose module-
        level ``TOOLS`` attribute is a list of coderio Tool instances. When the
        skill is activated, these tools are added to the agent's tool set (and
        removed on deactivation). A skill without tools.py simply returns [].

        The import is cached on the instance (_tools) so repeated calls don't
        re-import. Returns a fresh list copy so callers can mutate safely.
        """
        if self._tools is not None:
            return list(self._tools)

        tools_py = self.dir_path / "tools.py"
        loaded: list = []
        if tools_py.is_file():
            try:
                # Dynamic import by file path — module name is namespaced to
                # avoid collisions across skills.
                mod_name = f"coderio_skill_{self.name.replace('-', '_')}_tools"
                spec = importlib.util.spec_from_file_location(mod_name, tools_py)
                if spec is not None and spec.loader is not None:
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    carried = getattr(module, "TOOLS", None)
                    if isinstance(carried, list):
                        loaded = carried
            except Exception:
                # A broken tools.py must NOT crash the agent — silently carry
                # no tools. The skill's prompt body still loads normally.
                loaded = []

        self._tools = loaded
        return list(loaded)

