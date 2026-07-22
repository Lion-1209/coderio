from pathlib import Path

from coderio.skills.store import SkillStore, load_skill_store


def make_skill(d, name, desc="a skill"):
    p = d / name / "SKILL.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        f"---\nname: {name}\ndescription: {desc}\n---\nbody of {name}", encoding="utf-8"
    )


def test_three_layer_priority(tmp_path):
    bundled = tmp_path / "bundled"
    user = tmp_path / "user"
    project = tmp_path / "project"
    make_skill(bundled, "a", "bundled-a")
    make_skill(user, "a", "user-a")
    make_skill(user, "b", "user-b")
    make_skill(project, "a", "project-a")
    make_skill(project, "c", "project-c")
    store = SkillStore()
    store._load_layer(bundled, "bundled")
    store._load_layer(user, "user")
    store._load_layer(project, "project")
    assert store.get("a").source_layer == "project"
    assert set(store.names()) == frozenset({"a", "b", "c"})


def test_load_skill_store_from_dirs(tmp_path):
    user = tmp_path / "user"
    make_skill(user, "x")
    store = load_skill_store(bundled_dir=None, user_dir=user, project_dir=None)
    assert "x" in store.names()


def test_metadata_cached_not_body(tmp_path):
    d = tmp_path / "user"
    make_skill(d, "y")
    store = load_skill_store(bundled_dir=None, user_dir=d, project_dir=None)
    s = store.get("y")
    assert s._loaded is False
    assert s.description == "a skill"
    assert "body of y" in s.body


def test_descriptions_for_prompt(tmp_path):
    user = tmp_path / "user"
    make_skill(user, "p", "does P")
    store = load_skill_store(bundled_dir=None, user_dir=user, project_dir=None)
    descs = store.descriptions_for_prompt()
    assert "p" in descs
