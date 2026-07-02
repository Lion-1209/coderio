from __future__ import annotations

from typing import Callable

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from coderio.agent.loop import _BoundModelCache, _execute_turn
from coderio.agent.skill_tool import ActivateSkillTool
from coderio.agent.stream import NullStream
from coderio.crew.agents import AgentRole, build_agent_roles
from coderio.crew.state import (
    CrewState,
    ProjectState,
    crew_state_to_project_state,
)
from coderio.session import Message
from coderio.tools import to_langchain_tools
from coderio.tools.base import to_langchain_tool
from coderio.tools.permission import PermissionGate

PauseCallback = Callable[[str, str], str]


class _ActiveSkillsStub:
    """Minimal stand-in so ActivateSkillTool can construct (crew roles have skills
    baked into prompts, not via runtime activation)."""

    def all(self):
        return []

    def is_active(self, name) -> bool:
        return False

    def activate(self, skill):
        pass


class CrewOrchestrator:
    """Six-agent sequential pipeline, implemented as a LangGraph StateGraph.

    Public interface (unchanged from the pre-LangGraph version):
        CrewOrchestrator(model, store, gate, stream, auto_mode, on_pause,
                         max_rounds, max_fix_loops)
        .run(request) -> ProjectState

    Internally the pipeline is a declarative state graph: clarify → spec →
    task → execute → verify → commit, with conditional back-edges (verify→execute
    on failure) and HITL interrupts (clarify/spec). Each node runs one role via
    the shared _execute_turn seam (harness=None — crew has its own verify→fix loop).
    """

    def __init__(
        self,
        model,
        store,
        gate: PermissionGate,
        stream=None,
        auto_mode: bool = False,
        on_pause: PauseCallback | None = None,
        max_rounds: int = 15,
        max_fix_loops: int = 2,
    ):
        self.model = model
        self.store = store
        self.gate = gate
        self.stream = stream or NullStream()
        self.auto_mode = auto_mode
        self.on_pause = on_pause
        self.roles = build_agent_roles(store)
        self.max_rounds = max_rounds
        self.max_fix_loops = max_fix_loops
        self._by_stage = {r.stage: r for r in self.roles}
        self._graph = self._build_graph()

    # ------------------------------------------------------------ public entry
    def run(self, request: str) -> ProjectState:
        """Run the full pipeline. Drives the LangGraph, handling HITL interrupts.

        Returns a ProjectState (the public contract). The graph uses CrewState
        internally; we convert at the boundary.
        """
        cfg = {"configurable": {"thread_id": f"crew-{id(request)}"}}
        state: CrewState | Command = {
            "request": request,
            "current_stage": "clarification",
            "fix_attempts": 0,
        }
        while True:
            result = self._graph.invoke(state, config=cfg)
            # LangGraph signals a pause via the __interrupt__ key in the result.
            interrupts = result.get("__interrupt__") if isinstance(result, dict) else None
            if not interrupts:
                break
            # A pause node fired interrupt(); ask the user and resume.
            intr = interrupts[0]
            payload = intr.value if hasattr(intr, "value") else intr
            stage = payload.get("stage", "") if isinstance(payload, dict) else ""
            output = payload.get("output", "") if isinstance(payload, dict) else str(payload)
            answer = self.on_pause(stage, output) if self.on_pause else ""
            state = Command(resume=answer)
        return crew_state_to_project_state(result)

    # ------------------------------------------------------------ graph build
    def _build_graph(self):
        """Compile the 6-role pipeline as a StateGraph."""
        g = StateGraph(CrewState)

        # One node per role. Each runs the role and returns its field update.
        for role in self.roles:
            g.add_node(role.stage, self._make_role_node(role))

        # HITL pause nodes (no-op in auto mode; interrupt() otherwise).
        g.add_node("clarify_pause", self._make_pause_node("clarify"))
        g.add_node("spec_pause", self._make_pause_node("spec"))

        # Linear spine.
        g.add_edge(START, "clarify")
        g.add_edge("clarify", "clarify_pause")
        g.add_edge("clarify_pause", "spec")
        g.add_edge("spec", "spec_pause")
        g.add_edge("spec_pause", "task")
        g.add_edge("task", "execute")
        g.add_edge("execute", "verify")
        # verify → conditional: back to execute (fix loop) or forward to commit.
        g.add_conditional_edges(
            "verify",
            self.verify_router,
            {"execute": "execute", "commit": "commit"},
        )
        g.add_edge("commit", END)

        return g.compile(checkpointer=MemorySaver())

    # ------------------------------------------------------------ node factories
    def _make_role_node(self, role: AgentRole):
        """Build a graph node that runs one role and returns its state update.

        Converts the LangGraph dict state to ProjectState (for attribute access
        in _build_prompt), runs the role, and writes the result to role.writes.
        Special handling for execute (fix_attempts increment) and verify
        (fix_feedback on failure).
        """
        def node(state: CrewState) -> dict:
            self._render_start(role)
            ps = crew_state_to_project_state(state)
            system_prompt = self._build_prompt(role, ps)
            result = self._run_role(role, system_prompt)
            self._render_done(role, result)
            update: dict = {role.writes: result, "current_stage": role.stage}
            if role.stage == "execute" and state.get("fix_feedback"):
                # A fix attempt was made in response to prior verifier feedback.
                update["fix_attempts"] = state.get("fix_attempts", 0) + 1
            if role.stage == "verify" and not self._verification_passed(result):
                # Feed the verifier's findings back to the implementer.
                update["fix_feedback"] = result
            return update

        return node

    def _make_pause_node(self, stage: str):
        """Build a HITL pause node. In auto mode it is a no-op; otherwise it
        interrupts and waits for the user's response (resumed via Command)."""
        field = "user_clarification_answer" if stage == "clarify" else "user_spec_approval"
        output_field = "clarification" if stage == "clarify" else "spec"

        def node(state: CrewState) -> dict:
            if self.auto_mode:
                return {}
            answer = interrupt({
                "stage": stage,
                "output": state.get(output_field, ""),
            })
            return {field: answer}

        return node

    # ------------------------------------------------------------ routing
    def verify_router(self, state: CrewState) -> str:
        """Conditional edge after verify: loop back to execute on failure
        (under the fix budget), otherwise proceed to commit."""
        if self._verification_passed(state.get("verification", "")):
            return "commit"
        if state.get("fix_attempts", 0) < self.max_fix_loops:
            return "execute"
        return "commit"  # budget exhausted — proceed even if still failing

    # ------------------------------------------------------------ helpers (preserved)
    def _verification_passed(self, verification: str) -> bool:
        """Heuristic: only loop when there's a CLEAR failure signal. Explicit PASS
        or ambiguous output both count as passed (avoid spurious fix loops)."""
        v = verification.lower()
        fail_signals = ["fail", "未通过", "不通过", "failed", "❌", "不满足"]
        has_fail = any(s in v for s in fail_signals)
        return not has_fail

    def _build_prompt(self, role: AgentRole, state) -> str:
        """Build a role's system prompt from the shared state.

        Accepts either a ProjectState (attribute access) or a CrewState dict
        (LangGraph) — converts the latter so callers (graph nodes and tests) can
        pass either.
        """
        if not isinstance(state, ProjectState):
            state = crew_state_to_project_state(state)
        parts = [
            f"你是 coderio crew 的 {role.name}。",
            f"你的职责：{role.description}",
            "",
            "## 遵循的 playbook",
            role.skill_body or f"({role.skill_name} skill 未加载)",
            "",
            "## 项目上下文",
            f"原始需求: {state.request}",
        ]
        for field_name in role.reads:
            val = getattr(state, field_name, "")
            if val:
                parts.append(f"\n{field_name}:\n{val}")
        if role.stage == "execute" and state.fix_feedback:
            parts.append(
                f"\n## 上次验证发现的问题（请修复）\n{state.fix_feedback}\n请针对上述问题修复实现，然后重新验证。"
            )
        if role.stage == "clarify" and state.user_clarification_answer:
            parts.append(f"\n用户对澄清的回应:\n{state.user_clarification_answer}")
        if role.stage == "spec" and state.user_spec_approval:
            parts.append(f"\n用户对 spec 的确认/修改:\n{state.user_spec_approval}")
        parts.append("")
        parts.append(f"## 你的产出\n把你的最终回复作为产出（编排器会存入 {role.writes} 字段）。")
        if role.stage == "clarify":
            parts.append("提出澄清问题；不要假设答案。")
        return "\n".join(parts)

    def _run_role(self, role: AgentRole, system_prompt: str) -> str:
        convo = [Message.user(self._task_instruction(role))]
        bound_cache = _BoundModelCache(self.model)
        activate = ActivateSkillTool(self.store, _ActiveSkillsStub())
        langchain_tools = to_langchain_tools(role.tools) + [to_langchain_tool(activate, activate.args_schema)]
        skill_index = {t.name: t for t in role.tools}
        skill_index[activate.name] = activate
        return _execute_turn(
            self.model,
            bound_cache,
            langchain_tools,
            system_prompt,
            convo,
            skill_index,
            self.gate,
            self.stream,
            role.max_rounds or self.max_rounds,
            harness=None,
        )

    def _task_instruction(self, role: AgentRole) -> str:
        if role.stage == "clarify":
            return "分析需求，提出需要澄清的关键问题。"
        return f"完成你作为 {role.name} 的职责。"

    def _pause(self, role: AgentRole, output: str) -> str:
        if self.on_pause is None:
            return ""
        return self.on_pause(role.stage, output)

    def _render_start(self, role: AgentRole):
        idx = list(self._by_stage).index(role.stage) + 1
        total = len(self.roles)
        self.stream.on_tool_end(
            "__stage__",
            f"▶ 阶段 {idx}/{total}: {role.name}",
        )

    def _render_done(self, role: AgentRole, result: str):
        preview = result[:120] + "..." if len(result) > 120 else result
        self.stream.on_tool_end(
            "__stage__",
            f"✓ {role.name} 产出:\n{preview}",
        )
