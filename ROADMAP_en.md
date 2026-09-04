# Roadmap

[中文](ROADMAP.md) | **English**

> This document answers the most common questions from trial users: **is this
> project still maintained, what's next, and what is explicitly out of scope.**
> It is updated alongside releases; shipped changes live in
> [CHANGELOG.md](CHANGELOG.md).

## Maintenance status

coderio is a single-maintainer spare-time project. The cadence is irregular
but ongoing: security and correctness issues (P0/P1) take priority over new
features, and every release's visible changes are recorded in the CHANGELOG.
Issues are read; PRs are welcome — for substantial changes, open an issue
first to align on direction.

## Now — in progress

Fixes over features. The current focus is backing the "reliable" claim with
real evidence:

- **Nightly eval against real providers**. The 1200+ existing tests mock the
  model layer — they cannot prove compatibility with real provider streaming,
  tool-call shapes, or rate-limit behavior. The plan is automated
  verification against at least Zhipu and StepFun, as a regular pre-release
  gate.
- **A steady release cadence**. Fixes and features land in the CHANGELOG's
  Unreleased section per batch and ship once a version's worth accumulates —
  no more letting several versions pile up behind one version number.
- **Real Windows write-sandbox isolation (ACL)**. The current job tier only
  constrains resources, and the write tier is equivalent to job — this is
  the biggest known shortcoming, documented honestly. The plan is true
  file-write isolation via Windows ACLs.
- **Adversarial review as standard procedure**. Since 2026-09-04 every
  release batch ships with an independent adversarial review (assume every
  claim is false) + mutation verification (reintroduce the bug, confirm the
  tests actually go red). The first round's haul is in the CHANGELOG's
  audit batch.

## Next — up next

- **A macOS sandbox decision**: evaluate landing `sandbox-exec`; if the cost
  is too high, explicitly declare macOS as having no OS-level sandbox and
  keep the docs honest (that is already the case today — this makes it a
  formal decision).
- **Structured ToolResult**: bash's exit_code is currently parsed out of the
  result string by regex (`[exit_code: N]` marker) — brittle against
  upstream format drift. The plan is a structured result object.
- **Upstream Textual tracking**: the Screen layers scroll-rendering bug was
  once recorded here; re-tested 2026-09-02 on textual 8.2.8 with two repros —
  NOT reproducible anymore (likely fixed upstream). Kept under watch.
- **Confirmation UX expansion**: file writes already show a diff preview;
  shell-command confirmations are planned to gain risk labeling
  (read / write / network / irreversible).

## Later — vision

- **Sharpening the dual identity**: coderio's differentiation is always two
  things — "the local agent for Chinese Coding Plan users" (direct Zhipu /
  StepFun subscription access, no proxy layer) and "a working, readable
  reference implementation of an agent" (layered monolith, every security
  layer individually readable). The roadmap follows these two threads and
  does not chase other agents' feature checklists.
- **Broader provider ecosystem**: more domestic providers, while keeping the
  direct Anthropic / OpenAI dual-protocol support.

## Non-goals

Explicitly out of scope (saying no is also a commitment):

- **Multi-agent orchestration frameworks**. A 6-agent LangGraph pipeline
  once shipped built-in; it was removed wholesale in 2026-07 — a single
  engine with subagents (read-only research / general-purpose task) covers
  the real use cases.
- **IDE plugins**. The terminal is coderio's form factor.
- **Cloud services / telemetry / accounts**. A local tool: keys stay on your
  machine, no data collection.

## Feedback

Security and correctness issues: open an issue directly. Usage questions:
attach the (sanitized) output of `coderio config` to speed up diagnosis.
