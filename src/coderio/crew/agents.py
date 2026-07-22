from __future__ import annotations

from dataclasses import dataclass, field

from coderio.tools import build_default_tools

STAGE_FIELD = {
    "clarify": "clarification",
    "spec": "spec",
    "task": "task_list",
    "execute": "implementation",
    "verify": "verification",
    "commit": "commit_message",
}

STAGE_SKILL = {
    "clarify": "clarifying-questions",
    "spec": "spec-writing",
    "task": "task-breakdown",
    "execute": "executing-plans",
    "verify": "verify-and-fix",
    "commit": "commit-message",
}


@dataclass
class AgentRole:
    stage: str
    name: str
    description: str
    skill_name: str
    skill_body: str
    tool_names: tuple[str, ...]
    reads: list[str]
    writes: str  # the ProjectState/CrewState field name this role writes to (e.g. "clarification")
    human_pause: bool
    max_rounds: int = 15
    tools: list = field(default_factory=list)


def _select_tools(all_tools, names):
    by_name = {t.name: t for t in all_tools}
    return [by_name[n] for n in names]


def build_agent_roles(store) -> list[AgentRole]:
    """Build the 6-role pipeline. Tools resolved from build_default_tools()."""
    all_tools = build_default_tools()

    roles_spec = [
        (
            "clarify",
            "Clarifier",
            "澄清模糊需求，提出关键问题，不要假设答案",
            ("read_file", "list_dir", "glob", "grep"),
            ["request"],
            "clarification",
            True,
        ),
        (
            "spec",
            "SpecWriter",
            "基于澄清结论写一份简洁的设计文档(spec)",
            ("read_file", "list_dir", "glob", "grep", "write_file"),
            ["request", "clarification"],
            "spec",
            True,
        ),
        (
            "task",
            "TaskPlanner",
            "把 spec 拆成可验证、有序的开发任务",
            ("read_file", "list_dir", "glob", "grep", "todo"),
            [*["request", "clarification", "spec"]],
            "task_list",
            False,
        ),
        (
            "execute",
            "Implementer",
            "按任务清单逐个实现，每步验证",
            (
                "read_file",
                "write_file",
                "edit_file",
                "multi_edit",
                "list_dir",
                "bash",
                "glob",
                "grep",
                "todo",
            ),
            ["spec", "task_list"],
            "implementation",
            False,
        ),
        (
            "verify",
            "Verifier",
            "验证实现是否满足 spec，跑测试，报告问题",
            ("read_file", "list_dir", "glob", "grep", "bash"),
            [*["spec", "task_list", "implementation"]],
            "verification",
            False,
        ),
        (
            "commit",
            "Committer",
            "基于改动写规范的提交信息",
            ("bash", "read_file", "list_dir"),
            ["implementation", "verification"],
            "commit_message",
            False,
        ),
    ]

    roles = []
    for stage, name, desc, tool_names, reads, writes, pause in roles_spec:
        skill_name = STAGE_SKILL[stage]
        skill = store.get(skill_name)
        skill_body = skill.body if skill else ""
        roles.append(
            AgentRole(
                stage=stage,
                name=name,
                description=desc,
                skill_name=skill_name,
                skill_body=skill_body,
                tool_names=tool_names,
                reads=reads,
                writes=writes,
                human_pause=pause,
                tools=_select_tools(all_tools, tool_names),
            )
        )
    return roles
