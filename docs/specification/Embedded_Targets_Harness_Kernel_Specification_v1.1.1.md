# Embedded Targets Agentic AI Harness Kernel and Multi-Agent Orchestrator

**Consolidated implementation specification for Claude Code, OpenAI Codex, or another software-engineering agent**

- **Document version:** 1.1.1
- **Supersedes:** 1.1
- **Status:** MVP implementation specification (amended)
- **Primary users:** Embedded Targets Development and Quality Engineering teams
- **Distribution model:** Versioned GitLab repository, cloned or installed into product repositories
- **Reference implementation:** Python CLI and library
- **Initial delivery:** Harness kernel with a governed single-agent loop (Milestone A), then sequential, role-isolated multi-agent orchestration (Milestone B), then CI authority and a first code-generation slice (Milestone C)

---

## 0. Revision summary

### 1.1 → 1.1.1

Version 1.1.1 is an amendment. It changes no state, transition, verdict rule or enforcement rule. It adds a read-only run monitor and fixes the audit event vocabulary the monitor and other readers depend on.

| ID | Change | Sections affected |
|---|---|---|
| C-11 | Read-only run monitor added to MVP scope. §5.2 now excludes only web interfaces that can change state, approve or override. | 5.1, 5.2, 7, 9.22, 28.18, 30, 33 |
| C-12 | Audit record completed: `sequence`, hash-chain fields and `details` are required; `event_type` values come from a fixed vocabulary; transition reasons are separated from §27 reason codes; fixed paths for the audit log and orchestration state file. | 9.19, 16, 28.18, 30 |

### 1.0 → 1.1

Version 1.1 keeps the 1.0 architecture and state machine. It reduces MVP scope, adds one real Embedded Targets slice, states honestly which controls are enforced versus detected, makes GitLab CI the authority for verdicts, and closes gaps found in review.

| ID | Change | Sections affected |
|---|---|---|
| C-01 | MVP scope reduced. Reproduction, diagnosis, package revocation, sensitivity quarantine, nested workflows and attempt comparison deferred to v1.2. | 5.1, 5.5, 8, 9.9, 9.15, 9.17, 9.20, 27, 28 |
| C-02 | Enforcement tiers (`ENFORCED`, `DETECTED`, `ADVISORY`) declared per host and per control. "Freeze writes" redefined accordingly. Kernel-owned state is hash-chained to detect tampering. | 3.11, 8, 9.5, 9.11, 9.14, 11.8, 17, 18, 21, 27, 28 |
| C-03 | Reference domain slice: a MATLAB session port, a real L1 `matlab.unittest` gate and a real L3 code-generation and compile gate on one reference target. | 5.4, 7, 11.9, 12, 20, 30, 36 |
| C-04 | GitLab CI issues authoritative verdicts by re-executing gates. Local verdicts are advisory. Class D and E approvals must be verifiable against GitLab identities. | 3.12, 6, 9.13, 22.5, 25, 26, 32 |
| C-05 | Artefact classes: model files need a model-diff provider or human review; derived artefacts (caches, generated code, build output) are not scope violations but must not be committed. | 9.1, 9.21, 14.7, 18.1, 21, 22.1 |
| C-06 | Workflow profiles: `full` and `lite`. The lite profile replaces the Planner and QE agents with kernel-generated plans and kernel-run gates, with the same state machine and evaluator. | 1, 5.1, 13.2, 13.5 |
| C-07 | Contract intake: an optional Intake persona drafts contracts; a human approves and locks them. Every acceptance criterion declares a verification method. A minimum feature-contract schema is added. | 4.6, 8, 9.4.1, 9.6, 24.5, 26 |
| C-08 | QE independence: optional `TEST_DESIGN` state where QE writes acceptance tests before Development; test-weakening detection; human sign-off at L5 and above. | 13, 14.5, 15, 22.1, 24.3 |
| C-09 | Gap fixes: rework ownership and review guard; verdict names unified as `PASSED`/`FAILED`/`BLOCKED`/`INCONCLUSIVE`; bootstrapping rule for the first HK features; host projection locations; editorial fixes. | 8, 9.6, 13.2, 14.10, 22, 24.4, 30 |
| C-10 | Implementation sequence regrouped into three milestones so a usable governed loop with a real MATLAB gate ships first. | 30, 34, 35, 36 |

---

## 1. Implementation directive

Treat this document as the authoritative implementation contract for the MVP.

Build a local, agent-neutral harness kernel that can be used from a GitLab-hosted product repository with Claude Code or Codex. The kernel must govern one bounded engineering feature at a time, activate versioned supporting packages, control agent personas and role handoffs, mediate tools, run verification gates, preserve evidence, and issue a deterministic verdict.

The MVP must implement **sequential multi-agent orchestration**:

```text
Planner -> [QE test design] -> Development -> QE -> Deterministic Evaluator
```

It must also implement a **lite profile** for small, low-risk changes, in which the kernel generates the plan and runs the gates itself:

```text
Kernel plan -> Development -> Kernel-run gates -> Deterministic Evaluator
```

Both profiles use the same state machine, evidence schema and evaluator (§13.5).

The kernel owns workflow state and role transitions. Agents may request transitions and submit artefacts, but they must never update orchestration state directly.

Do not build an unconstrained autonomous coding assistant. Do not build concurrent modifying agents. Do not fabricate MATLAB, Simulink, code-generation, build, virtual-target, or hardware results. Do not claim a control is enforced when it is only detected (§3.11).

---

## 2. Product vision

> The Embedded Targets Agentic AI Harness is a governed engineering runtime in which one immutable feature contract drives one bounded implementation. Role-isolated agents operate through approved skills, knowledge, tools, workflows, templates, and verification gates. The kernel controls state, permissions and handoffs, while reproducible evidence determines completion.

The kernel must eventually support engineering flows such as:

```text
Intent
  -> target-aware plan
  -> model or source change
  -> simulation
  -> code generation
  -> build
  -> target deployment
  -> observation and measurement
  -> verification
  -> verdict
```

The MVP does not need to implement all Embedded Targets integrations. It must establish the reliable substrate on which those integrations can be added as plug-in packages.

---

## 3. Design principles

### 3.1 Kernel, not expert

The kernel is not the Embedded Targets expert. It assembles, constrains and observes expert capabilities supplied by packages.

The kernel owns:

- Repository and environment discovery
- Configuration and policy precedence
- Feature contracts
- Session and workflow state
- Agent persona registry
- Agent-instance ownership leases
- Package resolution and activation
- Context assembly
- Scope enforcement
- Tool mediation
- Workflow execution
- Verification-gate execution
- Evidence collection
- Deterministic evaluation
- Audit and restartable handoff

The kernel does not own:

- AURIX, RH850, RA, C2000 or STM32 facts
- Peripheral-specific procedures
- MATLAB or Simulink domain expertise
- Vendor SDK commands
- Motor-control algorithms
- Target-framework implementation details
- Hardware-specific verification logic
- Knowledge-catalog storage technology
- Product source, models or tests

### 3.2 Progressive instructions

Instructions must be loaded progressively:

```text
Global operating contract
  -> current persona
  -> active feature
  -> selected workflow
  -> target family or project context
  -> required skills and knowledge
  -> required tools and gates
  -> diagnostic content only when needed
```

The root instruction file must be a router, not a large undifferentiated prompt.

### 3.3 Persisted state

All authoritative state must be persisted on disk in validated machine-readable files. A new process or agent session must resume without depending on prior chat history.

### 3.4 Verification before completion

An agent statement is not evidence. Completion requires executed gates and preserved evidence at the level required by the feature contract.

### 3.5 One bounded feature

Only one feature may be active in an MVP workflow. The objective, acceptance criteria, allowed paths, protected paths, verification level and required gates must be locked before implementation.

### 3.6 Agent isolation

Only one modifying agent owns the workflow at a time. Planner, Development and QE run in separate sessions with distinct permissions and context. In the lite profile, Development is the only agent session. The evaluator is read-only.

### 3.7 Artefact-based handoff

Agents do not hand work to each other through informal chat. The outgoing agent submits structured artefacts. The kernel validates actual repository state, creates the authoritative handoff, changes workflow state and starts the receiving persona with bounded context.

### 3.8 Deterministic control plane

Use deterministic code for:

- State transitions
- Scope decisions
- Package resolution
- Permission decisions
- Approval decisions
- Gate aggregation
- Evidence completeness
- Pass eligibility

An LLM may propose plans, implementation, tests, diagnoses and explanations. It must not decide whether mandatory gates passed.

### 3.9 Plug-and-play extension

Skills, tools, knowledge providers, workflows, gates, policies, templates and agent adapters must be versioned packages behind stable interfaces.

### 3.10 Local and CI parity

The same feature contract, package lock, gate definitions and evidence schemas must work locally and in GitLab CI. CI may apply stricter policy, but it must not implement a different semantic workflow.

### 3.11 Honest enforcement

The kernel runs beside the agent host, not between the agent and the filesystem. Claude Code and Codex edit files and run commands through their own native tools. The kernel can only prevent an action when the host offers a pre-execution control (hooks, permission rules, sandbox or approval configuration) or the operating environment does (read-only mounts, containers, file permissions).

Every control must therefore be classified per host:

| Tier | Meaning | Example |
|---|---|---|
| `ENFORCED` | The action is prevented before it executes. | Host pre-tool hook calls `harness hook pre-tool` and the broker denies a write outside scope. |
| `DETECTED` | The action can occur, but the kernel detects it at a checkpoint, handoff or evaluation and rejects the result. | Git diff at `DEVELOPMENT_REVIEW` finds a protected-path change. |
| `ADVISORY` | The control exists only in instructions. | "Do not weaken tests" in the persona file, with no hook or diff rule. |

The kernel must report the effective tier of every control in `harness doctor`, in each handoff and in the evidence manifest. Policy may require a minimum tier for a risk class (§25). No control may be documented or reported as `ENFORCED` unless a test proves the host blocks the action.

### 3.12 CI is the authority

A verdict issued on a developer machine is advisory. The authoritative verdict for a candidate commit is issued in GitLab CI (or a registered hardware-lab runner) by re-executing the required gates against the committed contract and package lock (§22.5, §32). Local evidence may be attached to a merge request but cannot by itself satisfy a mandatory gate.

---

## 4. Personas and users

### 4.1 Developer

Needs to:

- Initialise or resume a bounded feature
- Use approved AI assistance
- Discover relevant packages
- Modify authorised source or models
- Run local checks
- Create a Development handoff
- Preserve work across sessions

### 4.2 QE engineer

Needs to:

- Receive an immutable candidate handoff
- Derive tests from acceptance criteria
- Run independent gates
- Exercise negative and boundary cases
- Reproduce failures
- Collect model, generated-code, virtual-target or hardware evidence
- Submit structured QE results

### 4.3 Target or driver author

Needs to:

- Create or extend hardware definitions
- Generate manifests, services, drivers, tests and documentation
- Add target variants or boards
- Validate toolchain and deployment integration

These domain operations are supplied by packages; the kernel only governs them.

### 4.4 Package contributor

Needs to:

- Scaffold a package
- Declare metadata, compatibility, permissions and entry points
- Run package validation and contract tests
- Publish a versioned package

### 4.5 Harness maintainer

Needs to:

- Evolve kernel APIs and schemas
- Maintain policies
- Certify package compatibility
- Publish releases
- Withdraw unsafe packages (MVP: remove from locks and approved sources; formal revocation is deferred to v1.2)
- Diagnose orchestration and compatibility failures

### 4.6 Feature owner

The human accountable for a feature. Needs to:

- Draft a feature contract, optionally with the Intake persona (§24.5)
- Make every acceptance criterion verifiable by a gate or mark it for human review
- Approve and lock the contract
- Approve criteria marked for human review and L5/L6 sign-off
- Decide on escalation from the lite to the full profile

---

## 5. MVP scope

### 5.1 In scope

- Python package and CLI
- Git repository discovery and inspection
- Layered configuration
- JSON Schema validation
- One locked feature contract, with human approval before lock
- Optional Intake persona for drafting contracts
- Persisted session and orchestration state, hash-chained to the audit log
- `full` and `lite` workflow profiles (§13.5)
- Persona registry
- Claude Code and Codex instruction projections
- Host enforcement tiers, including a Claude Code pre-tool hook (§3.11)
- Sequential Planner, QE test-design, Development and QE sessions
- Read-only deterministic Evaluator
- Exclusive agent ownership lease
- Structured handoff protocol
- Artefact classification, including model and derived paths (§9.21)
- Package manifest validation
- Pinned local package activation (local path or Git commit SHA)
- Tool broker for approved local commands and session-scoped tools
- Sequential workflow engine
- Verification gate runner
- Evidence bundle and hashing
- Audit log
- Rework loop with limits
- Block, resume and crash recovery
- GitLab CI-compatible commands, with CI as verdict authority
- Reference domain slice (§5.4)
- Synthetic tests for all kernel behaviour
- Read-only run monitor (§9.22)

### 5.2 Explicitly out of scope

- Concurrent modifying agents
- Free-form agent-to-agent chat
- Dynamic delegation trees
- Background agents
- Parallel workflow execution
- Remote orchestration service
- Web user interface that can change state, approve, override or launch agents (the read-only run monitor in §9.22 is in scope)
- Shared hardware scheduling
- Knowledge graph implementation
- Expertise Catalog implementation
- Simulink Atlas implementation
- Automatic merge request creation
- Autonomous merging
- Autonomous release approval
- Automatic installation of unapproved software
- Autonomous golden-baseline updates
- Autonomous production deployment
- MATLAB, Simulink or vendor integrations beyond the reference domain slice (§5.4)

### 5.3 MVP success statement

> The same bounded synthetic feature can be started, planned, implemented, independently verified, evaluated, stopped and resumed using either Claude Code or Codex, with identical kernel semantics, validated role handoffs and a reproducible evidence bundle. In addition, one real Embedded Targets feature reaches an authoritative CI verdict using real MATLAB gates, and every governance control reports its true enforcement tier.

### 5.4 Reference domain slice (in scope)

The MVP must prove the kernel against real Embedded Targets work, not only synthetic fixtures. The slice is deliberately narrow:

1. **MATLAB session port.** A kernel port (`MatlabSessionPort`) with two adapters:
   - `batch`: runs `matlab -batch` per invocation; simplest, slowest.
   - `session`: reuses a warm MATLAB session, for example through the MATLAB MCP Core Server or the MATLAB Engine API for Python.
   The adapter records MATLAB release, installed products, licence checkout result and session start time as evidence.
2. **L1 gate package `matlab-unittest`.** Runs `matlab.unittest` suites selected by the feature contract, emits JUnit XML and test counts, and maps results to acceptance criteria.
3. **L3 gate package `codegen-compile-<reference-target>`.** Generates code for one reference model on one reference target chosen by the team, compiles it with the target toolchain, and preserves the code-generation report and build log.
4. **One real skill package** for the reference target (for example, the C2000 configuration package in §10).
5. **One Simulink model-diff provider** (§9.21), or an explicit decision to use the hash-only fallback with human model review.

Rules:

- If MATLAB, a required product, the licence or the toolchain is unavailable, the gate returns `BLOCKED` with a reason code. It must never be skipped silently or simulated.
- Slice gates run in a CI runner with MATLAB installed. Without such a runner, the slice's acceptance (§36) is `BLOCKED`, not `PASSED`.
- Domain logic stays in packages. The kernel only gains the generic `MatlabSessionPort`.

### 5.5 Deferred to v1.2

The following 1.0 requirements remain part of the design but are not required for MVP acceptance. Interfaces may be stubbed; stubs must report `NOT_IMPLEMENTED`, never success.

- UC-18 Reproduce a prior run
- UC-21 Diagnose a failed run (beyond recording a normalised failure signature)
- Package revocation lists and revocation checking
- Trust and sensitivity enforcement beyond recording the declared classification
- Sensitive-content quarantine
- Comparison between evidence attempts
- Nested reusable workflows
- Semantic-version range resolution (MVP locks exact versions and checks declared compatibility ranges only)

---

## 6. Deployment and repository model

The harness repository is a shared engineering platform. Product engineering work remains in product repositories.

```text
GitLab: embedded-targets-agent-harness
  - kernel
  - CLI
  - schemas
  - built-in policies
  - agent adapters
  - package interfaces
  - reference packages
  - tests

GitLab: product-or-feature-repository
  - product source and models
  - product tests
  - feature contracts
  - project harness configuration
  - package lock
  - local state and evidence
```

A typical user flow is:

```text
Clone product repository
  -> bootstrap pinned harness
  -> resolve approved packages
  -> draft feature contract (optionally with Intake)
  -> feature owner approves and locks contract
  -> initialise one feature (profile: full or lite)
  -> launch selected agent host
  -> Planner handoff (full) or kernel plan (lite)
  -> QE test-design handoff (full, when enabled)
  -> Development handoff
  -> QE handoff (full) or kernel-run gates (lite)
  -> deterministic evaluation (advisory)
  -> push branch; GitLab CI re-runs gates and issues the authoritative verdict
```

Do not require developers to modify the harness source to use it in a product repository.

Workflow state, leases, handoffs and local evidence are local working data. CI does not read them. CI reconstructs what it needs from the committed contract, package lock and candidate commit (§32).

---

## 7. Required repository layout

```text
embedded-targets-agent-harness/
|
|-- AGENTS.md
|-- CLAUDE.md
|-- README.md
|-- pyproject.toml
|-- harness.yaml
|-- CHANGELOG.md
|-- LICENSE
|
|-- src/
|   `-- et_harness/
|       |-- __init__.py
|       |-- cli/
|       |-- application/
|       |-- config/
|       |-- repository/
|       |-- contracts/
|       |-- sessions/
|       |-- orchestration/
|       |-- personas/
|       |-- leases/
|       |-- packages/
|       |-- context/
|       |-- agents/
|       |-- policies/
|       |-- approvals/
|       |-- tools/
|       |-- workflows/
|       |-- gates/
|       |-- evidence/
|       |-- evaluator/
|       |-- audit/
|       |-- security/
|       |-- artefacts/
|       |-- enforcement/
|       |-- matlab/
|       `-- common/
|
|-- schemas/
|   |-- harness-config.schema.json
|   |-- project-config.schema.json
|   |-- feature.schema.json
|   |-- session.schema.json
|   |-- orchestration-state.schema.json
|   |-- persona.schema.json
|   |-- agent-instance.schema.json
|   |-- lease.schema.json
|   |-- package.schema.json
|   |-- plan.schema.json
|   |-- workflow.schema.json
|   |-- gate.schema.json
|   |-- gate-result.schema.json
|   |-- handoff.schema.json
|   |-- approval.schema.json
|   |-- evidence-manifest.schema.json
|   |-- qe-result.schema.json
|   |-- verdict.schema.json
|   |-- enforcement-report.schema.json
|   `-- artefact-classes.schema.json
|
|-- instructions/
|   |-- operating-contract.md
|   |-- intake.md
|   |-- planner.md
|   |-- development.md
|   |-- qe.md
|   |-- evaluator.md
|   |-- scope-policy.md
|   |-- verification-policy.md
|   |-- tool-policy.md
|   |-- approval-policy.md
|   |-- enforcement-policy.md
|   `-- lifecycle.md
|
|-- builtins/
|   |-- policies/
|   |-- personas/
|   |-- workflows/
|   |-- gates/
|   `-- packages/
|
|-- reference-packages/
|   |-- matlab-unittest/
|   |-- codegen-compile-<reference-target>/
|   |-- <reference-target>-skill/
|   `-- simulink-model-diff/
|
|-- adapters/
|   |-- claude_code/
|   |-- codex/
|   |-- git/
|   |-- filesystem/
|   |-- subprocess/
|   `-- matlab/
|
|-- tools/
|   `-- monitor/                 # read-only run monitor (§9.22)
|       |-- index.html
|       |-- harness_monitor.py
|       |-- simulate_run.py
|       |-- sample-run.jsonl
|       `-- README.md
|
|-- templates/
|   |-- product-project/
|   |-- feature/
|   |-- package/
|   `-- gitlab-ci/
|
|-- tests/
|   |-- unit/
|   |-- integration/
|   |-- contract/
|   |-- security/
|   |-- fixtures/
|   `-- end_to_end/
|
`-- docs/
    |-- architecture.md
    |-- package-authoring.md
    |-- workflow-authoring.md
    |-- persona-authoring.md
    |-- monitor.md
    `-- operations.md
```

A bootstrapped product repository should contain:

```text
product-repository/
|
|-- .agent-harness/
|   |-- project.yaml
|   |-- packages.lock.yaml
|   |-- generated/
|   |   |-- CLAUDE.md
|   |   `-- AGENTS.md
|   |-- state/
|   |   `-- orchestration-state.json   # active workflow (§16)
|   |-- handoffs/
|   |-- evidence/
|   |-- audit/
|   |   `-- audit.jsonl                # §9.19
|   `-- cache/
|
|-- features/
|   |-- backlog/
|   |-- active/
|   |-- blocked/
|   `-- completed/
|
|-- source-or-model-content/
|-- tests/
`-- qe-tests/              # QE-owned acceptance tests (path configurable)
```

Generated and transient directories must be configurable and suitable for `.gitignore`, while feature contracts and package locks should normally be version controlled.

Version-control rules:

| Path | Committed | Notes |
|---|---|---|
| `.agent-harness/project.yaml`, `packages.lock.yaml` | Yes | Required by CI. |
| `features/**` | Yes | Contracts are the CI input. |
| `.agent-harness/state/`, `handoffs/`, `audit/`, `cache/` | No | Local working data. |
| `.agent-harness/evidence/` | No | CI publishes authoritative evidence as pipeline artefacts. |
| Installed host projections (root `CLAUDE.md`, `AGENTS.md`, host settings) | Yes | Regenerated by bootstrap; `.agent-harness/generated/` staging may be ignored. |

Host projections must be installed where each host actually reads them (for example a root `CLAUDE.md` and `.claude/settings.json` for Claude Code, a root `AGENTS.md` and its configuration for Codex), not only under `.agent-harness/generated/`. The kernel records the hash of every installed projection; a hand edit is reported by `harness doctor` as drift and regenerated on request.

---

## 8. Core use cases

### UC-01: Bootstrap a product repository

**Intent:** Prepare a repository for governed agent-assisted development.

The kernel must:

1. Find the Git repository root.
2. Read project and organisation policy.
3. Detect supported agent hosts.
4. Detect available runtimes and registered tool adapters.
5. Resolve the requested harness version.
6. Resolve packages from `project.yaml` and lock file.
7. Generate Claude Code and Codex instruction projections and install them where each host reads them.
8. Generate approved tool configuration, including host hooks and permission rules where the host supports them.
9. Run installation self-tests, including tier probes that prove which controls are `ENFORCED` (§3.11).
10. Report loaded, skipped and blocked capabilities, and the enforcement tier of each control, with reason codes.

Output must be machine-readable and human-readable.

### UC-02: Create a workflow from a feature contract

The kernel must validate objective, acceptance criteria, verification methods, scope, required gates, permissions and approvals, confirm that the contract carries a valid feature-owner approval (§9.4.1), select the workflow profile (§13.5), then allocate a workflow ID and persist `CREATED` state.

### UC-03: Start or resume a session

The kernel must load the contract, prior state and handoff; validate repository and environment; run baseline checks; record baseline commit; and either continue from the persisted state or stop with a structured result.

### UC-04: Lock the feature contract and scope

The kernel must canonicalise the contract, calculate its digest, record allowed and protected paths, and reject unapproved contract mutation after lock.

### UC-05: Discover and activate packages

The kernel must resolve packages using:

```text
feature contract
+ project configuration
+ persona
+ target and release context
+ operating system
+ available tools
+ required verification level
+ policy
```

The resolver must explain every load, skip, conflict or block decision.

### UC-06: Assemble persona-specific context

The kernel must give each persona only the context required for the current state. It must keep instructions, knowledge, runtime state and evidence distinct.

### UC-07: Plan bounded work

The Planner must produce an ordered plan in which every task maps to acceptance criteria, allowed paths, tools, gates and approvals. In the lite profile, the kernel generates a single-task plan directly from the contract.

### UC-08: Launch or attach an agent instance

The kernel must create an agent-instance record, grant an exclusive lease where required, render role instructions for Claude Code or Codex, and start or prepare the role session through the adapter.

### UC-09: Mediate tool execution

All kernel-registered tools must run through the tool broker. The broker validates role, package, scope, lifecycle phase, permission class and approval before execution. Where the host supports pre-execution hooks, the host's native file and command tools are also routed to the broker through `harness hook pre-tool`. Native host actions that cannot be routed are governed at the `DETECTED` tier and are reported as such.

### UC-10: Detect and enforce scope

The kernel must block out-of-scope actions where the tier is `ENFORCED`, and must detect additions, modifications, deletions, renames, copies, path traversal, protected-path changes, kernel-state tampering and locked-contract changes at every checkpoint, before handoff and before evaluation.

### UC-11: Execute workflow steps

The workflow engine must sequence steps, validate preconditions, checkpoint state, pause for approval, enforce timeouts and propagate failure.

### UC-12: Validate handoff from Planner to Development

The kernel must validate plan coverage, scope, packages, tools and approval points before granting Development ownership.

### UC-13: Validate handoff from Development to QE

The kernel must compare the handoff with the actual diff, evidence and contract. Development cannot transition directly to QE.

### UC-14: Run independent QE verification

The QE persona must receive fresh role-specific context and execute mandatory gates. QE must not silently repair implementation code. When test design is enabled, QE writes acceptance tests from the contract before Development starts, and Development cannot modify them (§14.5).

### UC-15: Evaluate completion

A deterministic evaluator must issue `PASSED`, `FAILED`, `BLOCKED` or `INCONCLUSIVE` based on immutable evidence and explicit rules, and must mark the verdict `advisory` or `authoritative` (§22.5).

### UC-16: Execute bounded rework

A failed gate may create a narrow rework contract without changing the original feature contract. Rework cycles are limited and must produce new evidence.

### UC-17: Stop safely and hand off

Success, failure, blocking, interruption and cancellation must all leave persisted state, artefacts and an exact restart path.

### UC-18: Reproduce a prior run (deferred to v1.2)

The kernel must use the feature contract, package lock, environment requirements and evidence metadata to run a separate reproduction attempt without overwriting history.

### UC-19: Run locally and in GitLab CI

The same commands and result schemas must work under developer, QE, CI and hardware-lab execution profiles. Only CI and registered hardware-lab runners issue authoritative verdicts.

### UC-20: Author and validate a plug-in package

The kernel must scaffold, validate, test and digest versioned packages and detect metadata/version inconsistencies.

### UC-21: Diagnose a failed run (deferred to v1.2)

The kernel must normalise failure information, retrieve applicable diagnostic knowledge through a provider, track hypotheses and discriminating checks, and never mark an unproven diagnosis as fact. The MVP records only a normalised failure signature per failed gate attempt.

### UC-22: Draft and approve a feature contract

A feature owner drafts a contract, optionally with the Intake persona. The kernel lints it: every acceptance criterion must name a verification method (`gate` with at least one gate ID, or `human_review`), scope globs must be valid, and the required verification level must be achievable by the listed gates. The feature owner approves it; the approval is recorded in the contract and, for authoritative CI runs, must be verifiable against GitLab (§9.13).

### UC-23: Run a lite-profile feature

For an eligible small change (§13.5), the kernel generates the plan, the Development agent implements, the kernel runs the required gates and the evaluator issues a verdict, with the same evidence as the full profile. If eligibility is violated at any checkpoint, the kernel stops and requires escalation to the full profile.

### UC-24: Handle Simulink models and derived artefacts

The kernel classifies changed paths (§9.21). Changed model files require a model-diff artefact from a registered provider or a recorded human model review. Derived artefacts are ignored by scope checks but rejected if committed.

---

## 9. Kernel capability requirements

### 9.1 Repository service

Must provide:

- Repository-root discovery
- Git status and branch inspection
- Baseline commit capture
- Diff and changed-path enumeration
- Added, modified, deleted, renamed and copied file detection
- Worktree safety validation
- Repository fingerprint
- Safe checkpoint creation, recording a tree hash that includes untracked, non-ignored files so changes made after a checkpoint are detectable
- Classification of every changed path by artefact class (§9.21)

The Git implementation must be behind an interface to permit testing with fixtures.

### 9.2 Environment service

Must provide:

- Operating system and architecture detection
- Python/runtime detection
- Registered tool-adapter discovery
- Environment fingerprinting
- Optional discovery contributed by packages
- Clear distinction between missing optional and mandatory dependencies

### 9.3 Configuration service

Must load and explain layered configuration using this precedence:

```text
Kernel invariant
  > organisation policy
  > project policy
  > feature contract
  > persona profile
  > user-local preference
```

Lower-precedence configuration must not weaken higher-precedence controls.

Required behaviour:

- JSON Schema validation
- Unknown required-field rejection
- Schema-version validation
- Effective-configuration rendering
- Source attribution for each effective setting
- Conflict detection

### 9.4 Contract service

Must provide:

- Feature parsing and validation
- Canonical serialisation
- Cryptographic hashing
- Scope normalisation
- Locking
- Contract-diff reporting
- Acceptance-criterion indexing
- Acceptance-criterion lint (every criterion has a verification method)
- Feature-owner approval check before lock
- Approved scope-change records

Use a stable canonical representation before hashing: parse YAML to a JSON data model, then canonicalise with RFC 8785 (JSON Canonicalization Scheme) and hash with SHA-256.

#### 9.4.1 Feature contract (minimum schema)

```yaml
api_version: embedded-targets.ai/v1
kind: FeatureContract

metadata:
  id: FEATURE-001
  title: Add ADC trigger source option to reference driver block
  owner: jane.doe
  target_context:
    family: c2000
    release: R2026b

profile: full            # full | lite (§13.5)

objective: >
  Allow the ADC driver block to select ePWM SOCA or SOCB as trigger source.

acceptance_criteria:
  - id: AC-001
    statement: Block mask exposes trigger source with values SOCA and SOCB.
    verification_method: gate          # gate | human_review
    gates: [matlab-unittest]
    expected_on_baseline: fail         # fail | pass (used by TEST_DESIGN, §14.5)
  - id: AC-002
    statement: Generated code configures the selected trigger source.
    verification_method: gate
    gates: [codegen-compile-reference]
    expected_on_baseline: fail
  - id: AC-003
    statement: Block help text describes the new option.
    verification_method: human_review

scope:
  allowed_paths:
    - src/blocks/adc/**
    - tests/adc/**
  protected_paths:
    - src/blocks/adc/legacy/**
    - tests/golden/**
  qe_test_paths:
    - qe-tests/adc/**
  # derived paths come from project artefact classes (§9.21)

verification:
  required_level: L3
  required_gates: [structural, matlab-unittest, codegen-compile-reference]
  qe_test_design: required        # required | optional | off

approvals:
  required: []                    # e.g. Class D/E actions (§25)

rework:
  maximum_cycles: 3

intake:
  drafted_by: intake-001          # or a human user ID
  approved_by: null               # set by `harness feature approve`
  approval_ref: null              # GitLab MR approval or signed commit for CI
```

Rules:

- A criterion with `verification_method: gate` must list at least one gate whose level is at or below `required_level` and is listed in `required_gates`.
- A criterion with `verification_method: human_review` passes only with a valid approval bound to that criterion.
- `profile: lite` is rejected if the contract violates lite eligibility (§13.5).
- The contract cannot be locked until `intake.approved_by` is set by a human identity other than an agent instance.

### 9.5 State service

Must provide:

- Atomic writes
- Versioned state schemas
- Optimistic concurrency through revision numbers
- Lifecycle transition validation
- Crash recovery
- Checkpoints
- Last-known-good state
- Blocker tracking
- Handoff tracking
- Schema migration hooks
- Integrity chaining: each state write records the hash of the previous state, and the same hash is appended to the audit log

Only the kernel may change authoritative workflow state. Because agents have filesystem access, kernel-owned files can be edited outside the kernel unless the host tier is `ENFORCED` for those paths. On every kernel command, the state service must verify the state file against the last audit entry and stop with `STATE_TAMPER_DETECTED` on mismatch.

### 9.6 Persona registry

Must support versioned persona definitions with:

- ID and version
- Responsibilities
- Allowed inputs
- Writable paths
- Allowed package types
- Allowed tool permissions
- Required outputs
- Forbidden actions
- Handoff criteria
- Context-rendering policy

Built-in MVP personas:

```text
intake        (optional, before CREATED)
planner
development
qe
evaluator
```

The evaluator persona is deterministic and read-only. An LLM adapter may explain its result but not decide eligibility.

Each persona definition must also declare `model_preference` (optional). Policy may require that the QE agent instance use a different host or model family from the Development agent instance; the evidence manifest records the actual pairing as `independence: same_model | different_model`.

### 9.7 Agent-instance service

Must record:

```yaml
id: dev-001
persona: development
host: claude-code
workflow_id: workflow-001
feature_id: FEATURE-001
status: active
started_at: null
ended_at: null
```

It must prevent host-specific state from becoming the kernel’s source of truth.

### 9.8 Ownership lease service

MVP semantics:

- At most one exclusive modifying lease per workflow.
- Planner, QE (test design), Development and QE (verification) run sequentially.
- In the lite profile, only Development holds a modifying lease; kernel-run steps need none.
- Evaluator has read-only access.
- Leases have IDs, owner, timestamps and status.
- Lease acquisition uses expected workflow revision.
- Expired or interrupted leases require kernel recovery.
- A new agent cannot modify until prior ownership is released or revoked.

### 9.9 Package manager

Supported kinds:

```text
SkillPackage
ToolPackage
KnowledgeProviderPackage
WorkflowPackage
GatePackage
PolicyPackage
TemplatePackage
AgentAdapterPackage
```

Must provide:

- Manifest validation
- Semantic-version comparison (for compatibility checks)
- Dependency graph creation
- Conflict and cycle detection
- Lock-file generation and verification
- Package digest verification
- Compatibility checks
- Activation/deactivation
- Trust and sensitivity classification (recorded; enforcement deferred to v1.2)
- Package provenance
- Resolution explanation

The MVP resolves packages only from local paths and Git checkouts pinned to a commit SHA, and the lock records exact versions. Version-range resolution, revocation and a remote registry service are deferred (§5.5).

### 9.10 Context broker

Must provide:

- Progressive disclosure
- Persona-aware selection
- Context-size budgets
- Deduplication
- Provenance references
- Separation of instruction, knowledge, runtime state and evidence
- Applicability filtering
- Conflict surfacing
- Host-neutral context object
- Claude Code and Codex rendering through adapters

### 9.11 Agent-host abstraction

Interface must support:

```text
detect()
prepare_environment()
render_instructions(context)
render_tool_configuration(tools)
render_skills(packages)
enforcement_capabilities()
install_guards(persona, scope)
probe_guards()
launch_or_attach(agent_instance)
collect_result(agent_instance)
terminate(agent_instance)
normalise_events(agent_instance)
```

Implement Claude Code and Codex adapters sufficiently to render their project instruction files and prepare a role session. If reliable programmatic launch is not available in the working environment, provide guided mode rather than simulating launch success.

Enforcement responsibilities:

- `enforcement_capabilities()` declares, for each control below, the best tier the host can offer in the current environment.
- `install_guards()` renders host hooks, permission rules or sandbox settings for the active persona and scope. For Claude Code, this includes a pre-tool hook that calls `harness hook pre-tool` and permission rules for paths and commands. For Codex, this includes its sandbox and approval configuration and writable-root limits, plus hooks where the installed version supports them.
- `probe_guards()` attempts a harmless forbidden action (for example, a write to a canary path outside scope) and reports whether it was blocked. A control is reported `ENFORCED` only if its probe is blocked.

Controls to classify:

| Control | Minimum MVP tier |
|---|---|
| Write outside allowed paths | `DETECTED` (target `ENFORCED` for Claude Code) |
| Write to protected paths | `DETECTED` (target `ENFORCED` for Claude Code) |
| Write to kernel-owned state | `DETECTED` via integrity chain (§9.5) |
| Unregistered shell commands | `ADVISORY` (target `ENFORCED` where hooks exist) |
| Network access | `ADVISORY` |
| Class D and E actions | Must be `ENFORCED` or performed only by a human (§25) |

`terminate()` must be used to stop an outgoing session at handoff where the host supports it. Where it does not, the kernel relies on the freeze checkpoint (§18).

### 9.12 Policy engine

Decision outcomes:

```text
ALLOW
DENY
REQUIRE_APPROVAL
NOT_APPLICABLE
```

Decision context includes:

- Persona
- Workflow state
- Feature scope
- Package trust level
- Tool action
- Risk class
- Target/environment context
- Approval records

Every decision must have a reason code and audit record.

### 9.13 Approval service

An approval must bind to:

- Approval ID
- Feature ID
- Workflow ID where applicable
- Action
- Scope
- Requester
- Approver
- Conditions
- Issue and expiry time
- Evidence reference
- Verification source: `local` or `gitlab`

The service must reject expired or mismatched approvals.

An approval with source `local` is a YAML record on a developer machine and its approver field is not authenticated. It is valid only for advisory runs and for Class B and C actions. Approvals for Class D and E actions, for criteria marked `human_review`, for the feature-owner approval in an authoritative run, and for L5/L6 sign-off must have source `gitlab`: a merge-request approval, protected-environment approval or signed commit by a user in the configured approver group, verified through the GitLab API in CI. Otherwise the evaluator reports `APPROVAL_UNVERIFIABLE`.

### 9.14 Tool broker

Every tool must declare:

- Tool ID and version
- Input schema
- Output schema
- Permission class
- Required approvals
- Supported lifecycle states
- Writable paths
- Timeout
- Idempotency characteristic
- Cleanup behaviour
- Execution mode: `process` (new process per call) or `session` (reuses a warm, kernel-supervised session such as MATLAB, §5.4)

The broker governs every tool registered with the kernel. It also receives pre-execution requests from host hooks (`harness hook pre-tool`) and must answer them with an `ALLOW` or `DENY` decision and reason code within a bounded time; a timeout is treated as `DENY`. The broker cannot govern host actions that bypass hooks; those are covered by detection (§3.11).

The broker must:

1. Validate registration.
2. Validate input.
3. Check persona permission.
4. Check workflow state.
5. Check scope.
6. Check approval.
7. Record planned invocation.
8. Execute with timeout and cancellation.
9. Capture stdout and stderr separately.
10. Limit output size.
11. Redact secrets where configured.
12. Collect declared artefacts.
13. Emit structured result.
14. Audit the action.

Do not provide an unrestricted shell tool as a default approved capability.

### 9.15 Workflow engine

MVP must support:

- Sequential steps
- Preconditions and postconditions
- Conditional branches
- Explicit approvals
- Checkpoints
- Retry policies
- Pause and resume
- Cancellation
- Cleanup steps
- Failure propagation
- Step-level evidence

Nested reusable workflows are deferred to v1.2. Parallel execution is prohibited in the MVP.

### 9.16 Verification engine

Must support:

- Gate registration
- Gate dependency ordering
- Verification levels L0-L6
- Preconditions
- Timeouts
- Attempts and retries
- Failed-attempt preservation
- Required evidence
- Environment applicability
- Developer, QE, CI and hardware-lab execution profiles
- Per-profile timeouts (MATLAB start-up, code generation and builds can take minutes)
- Session-scoped tools, so a sequence of gates can reuse one warm MATLAB session
- Test-count capture from test runners, for test-weakening detection (§14.7)
- Structured gate results
- Aggregate result

### 9.17 Evidence service

Must support:

- Evidence manifest
- Artefact registration
- Cryptographic hashes
- Feature/criterion/gate/tool mapping
- Package and environment provenance
- Immutable historical attempts
- Completeness checking
- Enforcement report (effective tier of every control during the run)
- Authority marking (`advisory` or `authoritative`)
- GitLab artefact export

Sensitive-content quarantine and comparison between attempts are deferred to v1.2.

### 9.18 Evaluator

Must implement deterministic rules for:

- Contract hash match
- Scope integrity
- Acceptance-criterion coverage
- Mandatory gate results
- Required verification level
- Evidence completeness
- Approval validity and verification source
- Package integrity
- Blocker status
- State integrity chain
- Verdict authority (§22.5)

The evaluator must have no write access to product source, tests, gate results or prior evidence.

### 9.19 Audit service

Use append-only JSON Lines. Every event must include, where applicable:

```json
{
  "timestamp": null,
  "workflow_id": null,
  "session_id": null,
  "feature_id": null,
  "persona": null,
  "agent_instance": null,
  "state": null,
  "event_type": null,
  "action": null,
  "tool": null,
  "package": null,
  "result": null,
  "reason_codes": [],
  "evidence": [],
  "approval_id": null
}
```

#### 9.19.1 Record envelope

Every record also carries:

| Field | Rule |
|---|---|
| `sequence` | Integer starting at 1, contiguous across the whole log file (not per workflow). Readers use it to order and de-duplicate. |
| `previous_entry_hash` | `entry_hash` of the previous record; for the first record, the SHA-256 of the fixed genesis string `et-harness-audit-genesis`. |
| `entry_hash` | `sha256:` + hex SHA-256 of the record's RFC 8785 canonical form with `entry_hash` removed. |
| `details` | Object with event-specific data, keyed as in §9.19.2. Always present; `{}` when empty. |

The audit log is `.agent-harness/audit/audit.jsonl`, one canonical JSON object per line. Secret redaction (§9.20) is applied before hashing and writing. A broken chain, a gap in `sequence` or an unparseable line is `STATE_TAMPER_DETECTED`.

`persona` holds a persona ID from §9.6, or `kernel` for events the kernel performs itself. `timestamp` is ISO 8601 with an explicit offset or `Z`.

#### 9.19.2 Event vocabulary

The kernel emits only the `event_type` values below. A new value requires a specification change. Readers must still display unknown values, so that logs from newer kernels remain readable.

| `event_type` | Emitted when | Required fields and `details` keys |
|---|---|---|
| `workflow.created` | Workflow ID allocated (§14.1) | `details.title`, `details.profile`, `details.required_level`, `details.maximum_rework_cycles`; optional `details.target` |
| `state.write` | State service commits a state file (§9.5) | `details.state_key`, `details.state_hash`, `details.previous_state_hash`, `details.revision` |
| `state.write_rolled_back` | A write was interrupted between audit append and file replace, and the prior state was kept (§9.5) | `details.state_key`, `details.state_hash`, `details.abandoned_state_hash`, `details.abandoned_sequence`; `reason_codes: [STATE_WRITE_INTERRUPTED]` |
| `state.transition` | Workflow state changes (§15) | `details.from`, `details.to`, `details.reason` (§9.19.3); `state` = the new state; `reason_codes` = blocker codes when `to` is `BLOCKED`, otherwise `[]` |
| `lease.acquired`, `lease.released`, `lease.revoked` | Lease changes (§17) | `persona`, `agent_instance`, `details.host`, `details.lease_id`, `details.mode` |
| `tool.decision` | Broker or pre-tool hook decides (§9.14) | `tool`, `action` (path or command), `result` = `ALLOW`, `DENY` or `REQUIRE_APPROVAL`, `reason_codes`, `details.via` = `broker` or `pre-tool hook` |
| `tool.executed` | A registered tool finishes | `tool`, `result`, `details.exit_code`, `details.duration_ms`; optional `details.summary`, `details.tests` |
| `gate.attempt` | A gate attempt ends (§20) | `action` = gate ID, `result` = `PASSED`, `FAILED`, `BLOCKED` or `INCONCLUSIVE`, `details.level`, `details.attempt`, `details.execution_profile`; optional `details.purpose`, `details.tests` (`run`, `passed`, `failed`, `skipped`), `details.failure_signature`; `evidence` = attempt file paths |
| `handoff.submitted` | Outgoing persona requests handoff (§18 step 1) | `details.handoff_id`, `details.to_persona`, `details.summary` |
| `handoff.accepted`, `handoff.rejected` | Kernel validation ends (§18 steps 4–6) | `details.handoff_id`, `details.summary`; on rejection, `reason_codes` |
| `checkpoint.recorded` | Freeze or safe checkpoint (§9.1, §17) | `details.tree_hash`, `details.kind` = `freeze` or `safe` |
| `scope.violation` | A checkpoint detects a change the guards did not block (§3.11 `DETECTED` tier) | `action` = path, `reason_codes`, `details.change_kind` |
| `enforcement.probe` | A tier probe runs (§9.11) | `action` = control ID, `result` = `ENFORCED`, `DETECTED` or `ADVISORY`, `details.host` |
| `package.resolved` | Package lock resolved (§9.9) | `details.packages` (`id@version` strings), `details.lock_hash` |
| `approval.recorded` | An approval is accepted or rejected (§9.13) | `approval_id`, `result`, `details.source` = `local` or `gitlab`, `details.action` |
| `blocker.raised`, `blocker.resolved` | Blocker set or cleared (§15) | `reason_codes`; `details.resume_state` |
| `evaluation.result` | Evaluator finishes a non-terminal evaluation, for example one that leads to rework (§14.9) | `result`, `reason_codes` (§27 only), `details.failed_gates`, `details.rework_allowed`, `details.summary` |
| `verdict.issued` | Evaluator writes `verdict.yaml` (§22) | `result` = verdict, `reason_codes`, `details.authority`, `details.execution_profile`, `details.required_level`, `details.highest_level_passed`, `details.summary` |

Control IDs used by `enforcement.probe`: `write_outside_scope`, `protected_paths`, `kernel_state`, `shell_commands`, `network`, `class_d_e_actions` (§9.11).

#### 9.19.3 Transition reasons

`reason_codes` carries only §27 codes. The cause of a state transition is a separate vocabulary, used in `details.reason` of `state.transition` and in `history[].reason` of the state file (§16):

| Reason | Transition (§15) |
|---|---|
| `START_REQUESTED` | `CREATED -> INITIALISING` |
| `INITIALISATION_PASSED` | `INITIALISING -> PLANNING` |
| `BASELINE_FAILURE_ESTABLISHED` | `INITIALISING -> FAILED` |
| `PLAN_SUBMITTED` | `PLANNING -> PLAN_REVIEW` |
| `PLAN_ACCEPTED` | `PLAN_REVIEW -> TEST_DESIGN` or `-> DEVELOPING` |
| `PLAN_REJECTED` | `PLAN_REVIEW -> PLANNING` |
| `TESTS_ACCEPTED` | `TEST_DESIGN -> DEVELOPING` |
| `HANDOFF_SUBMITTED` | `DEVELOPING -> DEVELOPMENT_REVIEW` |
| `HANDOFF_ACCEPTED` | `DEVELOPMENT_REVIEW -> VERIFYING` |
| `HANDOFF_REJECTED` | `DEVELOPMENT_REVIEW -> DEVELOPING` |
| `NON_RECOVERABLE_FAILURE` | `DEVELOPING -> FAILED` |
| `QE_RESULT_SUBMITTED` | `VERIFYING -> EVALUATING` (full profile) |
| `GATE_RUN_RECORDED` | `VERIFYING -> EVALUATING` (lite profile) |
| `COMPLETION_RULES_SATISFIED` | `EVALUATING -> PASSED` |
| `REWORK_ALLOWED` | `EVALUATING -> REWORK_PLANNING` |
| `FAILURE_ESTABLISHED` | `EVALUATING -> FAILED` |
| `EVIDENCE_INSUFFICIENT` | `EVALUATING -> INCONCLUSIVE` |
| `REWORK_ACCEPTED` | `REWORK_PLANNING -> DEVELOPING` |
| `BLOCKER_RAISED` | any `-> BLOCKED` (the blocker's §27 codes go in `reason_codes`) |
| `BLOCKER_RESOLVED` | `BLOCKED ->` recorded resumable state |
| `ADDITIONAL_EVIDENCE_AUTHORISED` | `INCONCLUSIVE ->` recorded resumable state |
| `FEATURE_ABANDONED` | `BLOCKED -> FAILED` |

A rejected handoff with no state change (§15, last paragraph) is recorded as `handoff.rejected`, not as a transition.

### 9.20 Security service

Must provide:

- Approved-source enforcement
- Package digest checking
- Path traversal protection
- Secret-pattern redaction in captured tool output (configurable patterns)
- Command-argument constraints
- Network policy declaration
- Trust and sensitivity levels (recorded)
- Safe handling of customer or restricted content: such content must not be placed in an agent's context unless the project policy explicitly permits it for the host in use

Deferred to v1.2: package revocation checking, repository-wide secret scanning, sensitive-data quarantine and enforcement of trust levels.

### 9.21 Artefact classification service

Every path in the product repository belongs to exactly one class, configured in `project.yaml` with built-in defaults:

| Class | Default examples | Scope behaviour |
|---|---|---|
| `source` | `*.m`, `*.c`, `*.h`, `*.tlc`, `*.py` | Normal scope rules. |
| `model` | `*.slx`, `*.mdl`, `*.sldd` | Normal scope rules, plus a model-diff artefact or human model review is required at handoff. |
| `derived` | `slprj/**`, `*.slxc`, `*_ert_rtw/**`, `codegen/**`, build directories | Ignored by scope checks during work; rejected with `DERIVED_ARTEFACT_COMMITTED` if present in the candidate commit. Regenerated by gates. |
| `qe_test` | Paths in `scope.qe_test_paths` | Writable by QE only. Protected during `DEVELOPING`. |
| `harness` | `.agent-harness/**`, installed host projections | Writable by the kernel only. |
| `protected` | Paths in `scope.protected_paths` | Never writable without an approved scope change. |

Model-diff providers implement:

```python
class ModelDiffProvider(Protocol):
    def supports(self, path: Path) -> bool: ...
    def diff(self, baseline: Path, candidate: Path) -> ModelDiffResult: ...
```

`ModelDiffResult` is a structured, text-reviewable summary of block, parameter, connection and configuration changes, stored under `model-diffs/` in the handoff and evidence bundle. If no provider supports a changed model file, the kernel records its baseline and candidate hashes, sets `requires_human_model_review: true` on the handoff, and the evaluator requires a matching `human_review` approval before `PASSED`.

### 9.22 Read-only run monitor

A local web page that lets developers, QE engineers and reviewers watch a workflow as it runs, or replay a finished one. It is an observer, not a control surface.

It shows:

- The §13 state machine, with the current state marked and coloured by its owner (§13.2)
- One lane per persona (kernel, planner, qe, development, evaluator), marked while that persona holds the modifying lease
- Gate attempts, tool decisions (including hook denials), handoffs and blockers on those lanes
- Gates grouped by verification level
- Enforcement tiers per host and control, as reported by `enforcement.probe`
- The audit log, with reason codes

Components, in `tools/monitor/`:

| File | Role |
|---|---|
| `index.html` | The page. Self-contained; opens in demo mode with a built-in sample when no server is present. |
| `harness_monitor.py` | Local server. Serves `index.html` and streams the audit log and state file to the page with Server-Sent Events. Python standard library; PyYAML only to read a YAML state file. |
| `simulate_run.py` | Replays `sample-run.jsonl` into a scratch `.agent-harness` directory in real time, for trying the monitor without a kernel. |
| `sample-run.jsonl` | One full-profile synthetic run that conforms to §9.19. |

Modes:

| Mode | How | Use |
|---|---|---|
| Live | Served by `harness_monitor.py --repo <product-repo>` | Watch a run while it happens. |
| Replay | **Open audit file** on the page | Step through a finished run, including `audit.jsonl` from a CI evidence bundle (§21). |
| Demo | Open `index.html` directly | Built-in sample. |

Requirements:

1. **Read-only.** The monitor never writes to the product repository, kernel state, evidence or audit log. The server accepts only `GET` and `HEAD`, refuses every other method with 405, and serves only `index.html`, `/api/snapshot` and `/api/stream`. It has no approve, override, transition, launch or retry controls.
2. **Inputs.** It reads only `.agent-harness/audit/audit.jsonl` and `.agent-harness/state/orchestration-state.json` (or paths given explicitly). It does not read handoffs, evidence or product files.
3. **Partial writes.** A partial last line of the audit log is left for the next poll. A file that shrinks or is replaced resets the stream. A malformed line produces a visible warning and is never silently dropped.
4. **Local by default.** The server binds to `127.0.0.1`. Binding to any other address prints a warning, because audit records can contain paths and summaries from restricted work (§9.20). Remote font loading must be removable for air-gapped use.
5. **Not evidence.** Nothing the monitor shows is evidence or a verdict. A verdict is shown with its `authority` (§22.5); an advisory `PASSED` must never be rendered as an authoritative one.
6. **Honest coverage.** The monitor shows only what reaches the audit log. Host actions governed at the `DETECTED` tier appear only when a checkpoint catches them (`scope.violation`), and agent conversations never appear. The page must state this limit.
7. **Unknown events.** Event types outside §9.19.2 are listed in the log, not discarded.
8. **Integrity display.** The monitor does not verify the hash chain; it may show `sequence` gaps as a warning. `harness doctor` and the evaluator remain the integrity authority.
9. **Portability.** Standard library plus an optional PyYAML; runs on Windows and Linux.

---

## 10. Plug-in package contract

Every package must contain:

```text
package-root/
|-- package.yaml
|-- README.md
|-- CHANGELOG.md
|-- content/
|-- tests/
|-- schemas/
`-- examples/
```

Example manifest:

```yaml
api_version: embedded-targets.ai/v1
kind: SkillPackage

metadata:
  id: mathworks.embedded-targets.c2000-configuration
  name: C2000 Target Configuration
  version: 2.3.0
  owner: c2000-targets
  description: Reusable procedure for supported C2000 configuration work.

compatibility:
  harness: ">=0.1.0 <1.0.0"
  agents:
    claude-code: ">=1.0"
    codex: ">=1.0"

activation:
  topics:
    - c2000
    - epwm
    - adc
  explicit_commands:
    - configure-c2000-target

entrypoints:
  skill: content/SKILL.md

dependencies:
  required: []
  optional: []

permissions:
  tools:
    - repository.read
  filesystem:
    write: []

verification:
  commands:
    - harness package validate .
    - harness package test .

integrity:
  licence: MathWorks-Internal
  sensitivity: internal
```

Rules:

1. One authoritative version in `package.yaml`.
2. CI validates release tag and changelog consistency.
3. Package ID is globally unique within configured sources.
4. Dependencies and compatibility are explicit.
5. Permissions are explicit.
6. Package activation never bypasses policy.
7. Discovery alone never executes package code.
8. Unlocked package versions cannot be used for authoritative CI verification.

---

## 11. Extension interfaces

Define Python protocols or abstract base classes for the following.

### 11.1 Skill provider

```python
class SkillProvider(Protocol):
    def describe(self) -> SkillDescriptor: ...
    def activation_rules(self) -> ActivationRules: ...
    def required_capabilities(self) -> list[str]: ...
    def render_instructions(self, context: ContextBundle) -> str: ...
    def validate(self) -> ValidationResult: ...
```

### 11.2 Tool provider

```python
class ToolProvider(Protocol):
    def describe(self) -> ToolDescriptor: ...
    def input_schema(self) -> dict: ...
    def output_schema(self) -> dict: ...
    def permissions(self) -> PermissionDeclaration: ...
    def preflight(self, context: ToolContext) -> PreflightResult: ...
    def execute(self, request: ToolRequest) -> ToolResult: ...
    def cleanup(self, context: ToolContext) -> CleanupResult: ...
```

### 11.3 Knowledge provider

```python
class KnowledgeProvider(Protocol):
    def describe_catalog(self) -> CatalogDescriptor: ...
    def query(self, request: KnowledgeQuery) -> list[KnowledgeHit]: ...
    def fetch(self, item_id: str) -> KnowledgeItem: ...
    def provenance(self, item_id: str) -> Provenance: ...
    def validate_applicability(
        self, item_id: str, runtime: RuntimeContext
    ) -> ApplicabilityResult: ...
```

Do not couple the kernel to Markdown, SQLite, ECM, Atlas or a graph database.

### 11.4 Workflow provider

```python
class WorkflowProvider(Protocol):
    def describe(self) -> WorkflowDescriptor: ...
    def required_packages(self) -> list[PackageRequirement]: ...
    def steps(self) -> list[WorkflowStep]: ...
    def preconditions(self) -> list[Condition]: ...
    def completion_conditions(self) -> list[Condition]: ...
    def cleanup_steps(self) -> list[WorkflowStep]: ...
```

### 11.5 Gate provider

```python
class GateProvider(Protocol):
    def describe(self) -> GateDescriptor: ...
    def verification_level(self) -> str: ...
    def preconditions(self) -> list[Condition]: ...
    def execute(self, context: GateContext) -> GateAttempt: ...
    def required_evidence(self) -> list[EvidenceRequirement]: ...
    def evaluate(self, attempt: GateAttempt) -> GateResult: ...
```

### 11.6 Policy provider

```python
class PolicyProvider(Protocol):
    def applicable_actions(self) -> list[str]: ...
    def evaluate(self, context: PolicyContext) -> PolicyDecision: ...
    def reason_codes(self) -> list[str]: ...
```

### 11.7 Template provider

```python
class TemplateProvider(Protocol):
    def describe(self) -> TemplateDescriptor: ...
    def applicability(self, context: RuntimeContext) -> ApplicabilityResult: ...
    def materialise(self, destination: Path, parameters: dict) -> MaterialiseResult: ...
    def verify_materialisation(self, destination: Path) -> ValidationResult: ...
```

### 11.8 Agent adapter

```python
class AgentAdapter(Protocol):
    def detect(self) -> DetectionResult: ...
    def prepare_environment(self, context: AgentContext) -> PreparationResult: ...
    def render_instructions(self, context: ContextBundle) -> RenderedInstructions: ...
    def render_tool_configuration(self, tools: list[ToolDescriptor]) -> list[Path]: ...
    def render_skills(self, packages: list[ResolvedPackage]) -> list[Path]: ...
    def enforcement_capabilities(self) -> EnforcementCapabilities: ...
    def install_guards(self, persona: Persona, scope: ScopeLock) -> GuardInstallResult: ...
    def probe_guards(self) -> EnforcementReport: ...
    def launch_or_attach(self, instance: AgentInstance) -> LaunchResult: ...
    def collect_result(self, instance: AgentInstance) -> AgentResult: ...
    def terminate(self, instance: AgentInstance) -> TerminationResult: ...
```

### 11.9 MATLAB session port

```python
class MatlabSessionPort(Protocol):
    def detect(self) -> MatlabDetection: ...          # release, products, licence status
    def start(self, profile: ExecutionProfile) -> MatlabSession: ...
    def run(self, session: MatlabSession, request: MatlabRequest) -> MatlabResult: ...
    def stop(self, session: MatlabSession) -> CleanupResult: ...
```

`MatlabResult` must carry exit status, captured output, produced artefact paths, elapsed time and any licence or product error as a structured reason code. The kernel uses this port only to run tools and gates supplied by packages; it contains no MATLAB domain logic.

---

## 12. Verification levels

```text
L0 Structural
L1 Component
L2 Model
L3 Generated code
L4 Virtual target
L5 Physical target
L6 Production evidence
```

### L0 Structural

Examples:

- Schema validation
- Required files
- Package manifest consistency
- Scope integrity
- Instruction references

### L1 Component

Examples:

- Kernel unit tests
- MATLAB unit tests through the `matlab-unittest` reference gate (§5.4)
- Parser and configuration tests
- Negative and boundary tests

### L2 Model

Examples supplied by future packages:

- Model load and update
- Block and parameter checks
- Simulation and assertions
- Sample-time and architecture checks

### L3 Generated code

Examples:

- Code generation and compilation for the reference target through the `codegen-compile-<reference-target>` gate (§5.4)
- Code generation
- Compilation
- Interface checks
- Static analysis
- Report preservation

### L4 Virtual target

Examples:

- SIL
- PIL
- Emulator or virtual platform
- Numerical equivalence

### L5 Physical target

Examples:

- Hardware identity
- Flash and execution
- Telemetry
- GPIO/PWM/ADC/interrupt measurements
- Expected-versus-observed comparison

### L6 Production evidence

Examples:

- Requirements traceability
- Coverage
- Reproducible toolchain
- Approved baseline
- Known limitations
- Required reviews

If a feature requires L5 and hardware is unavailable, preserve L0-L4 results and return `BLOCKED`, not `PASSED`.

Features with a required level of L5 or L6 additionally require a human sign-off approval with verification source `gitlab` (§9.13) before the evaluator may issue `PASSED`.

---

## 13. MVP orchestration state machine

### 13.1 States

```text
CREATED
INITIALISING
PLANNING
PLAN_REVIEW
TEST_DESIGN
DEVELOPING
DEVELOPMENT_REVIEW
VERIFYING
EVALUATING
REWORK_PLANNING
PASSED
FAILED
BLOCKED
INCONCLUSIVE
```

Use enum values consistently in code and schemas. Use British spelling `INITIALISING` everywhere in the implementation. Verdict values use the same names as the terminal states: `PASSED`, `FAILED`, `BLOCKED`, `INCONCLUSIVE`.

### 13.2 State ownership

| State | Owner (full profile) | Owner (lite profile) | Write authority |
|---|---|---|---|
| `CREATED` | Kernel | Kernel | State only |
| `INITIALISING` | Kernel | Kernel | Harness state and generated config |
| `PLANNING` | Planner | Kernel (generated plan) | Plan artefacts only |
| `PLAN_REVIEW` | Kernel | Kernel | Review result only |
| `TEST_DESIGN` | QE | Not used | QE test paths only |
| `DEVELOPING` | Development | Development | Allowed implementation and Development test paths |
| `DEVELOPMENT_REVIEW` | Kernel | Kernel | Handoff review only |
| `VERIFYING` | QE | Kernel (gate runner) | QE test, gate result and evidence paths |
| `EVALUATING` | Evaluator | Evaluator | Verdict only |
| `REWORK_PLANNING` | Planner | Kernel (generated rework contract) | Rework contract only |
| Terminal states | Kernel | Kernel | Closure artefacts only |

### 13.3 High-level flow

```text
CREATED
   -> INITIALISING
   -> PLANNING
   -> PLAN_REVIEW
   -> [TEST_DESIGN]          (full profile, when qe_test_design is not off)
   -> DEVELOPING
   -> DEVELOPMENT_REVIEW
   -> VERIFYING
   -> EVALUATING
       |-> PASSED
       |-> FAILED
       |-> BLOCKED
       |-> INCONCLUSIVE
       `-> REWORK_PLANNING -> DEVELOPING
```

### 13.4 Terminal verdicts

- `PASSED`: all completion rules satisfied.
- `FAILED`: evidence establishes failure and no permitted rework remains.
- `BLOCKED`: a required external dependency or approval is unavailable.
- `INCONCLUSIVE`: available evidence cannot establish pass or fail.

`INCONCLUSIVE` must never be converted automatically to `PASSED`.

### 13.5 Workflow profiles

The same states, transitions, evidence schema and evaluator apply to both profiles. Profiles only change who performs a state's work.

| | `full` | `lite` |
|---|---|---|
| Plan | Planner agent | Kernel generates a single-task plan from the contract |
| Acceptance tests before development | QE agent in `TEST_DESIGN` (configurable) | Not used |
| Implementation | Development agent | Development agent |
| Verification | QE agent runs gates and adds tests | Kernel runs the contract's required gates |
| Agent sessions per feature | 3–4 | 1 |

Lite eligibility (all must hold; thresholds configurable in project policy):

- Required verification level is L3 or lower.
- No Class D or E tool is required.
- No `model` or public-API path is in the allowed scope.
- Every acceptance criterion uses `verification_method: gate`.
- The candidate diff stays within a configured size (default: 5 changed files, 300 changed lines).

The kernel checks eligibility when the contract is linted, at `INITIALISING` and again at `DEVELOPMENT_REVIEW`. If the candidate breaks eligibility at `DEVELOPMENT_REVIEW`, the kernel moves the workflow to `BLOCKED` with blocker `PROFILE_ESCALATION_REQUIRED`. The feature owner then either asks Development to reduce the change (the blocker is resolved and the workflow resumes at `DEVELOPING`), or approves a contract revision with `profile: full`. A revision has a new contract hash, so it starts a new workflow; the blocked workflow records `superseded_by` and keeps its evidence.

---

## 14. Detailed state contracts

### 14.1 `CREATED`

Entry requirements:

- Feature contract exists.
- Workflow ID allocated.

Kernel actions:

- Validate basic schema.
- Confirm one active feature.
- Persist initial state.

Transitions:

```text
CREATED -> INITIALISING
CREATED -> BLOCKED
```

### 14.2 `INITIALISING`

Kernel actions:

1. Discover repository.
2. Validate worktree policy.
3. Capture baseline commit.
4. Load effective configuration.
5. Resolve package lock.
6. Detect agent hosts.
7. Discover required tools.
8. Capture environment fingerprint.
9. Run baseline checks, recording baseline test counts.
10. Verify the feature-owner approval on the contract.
11. Check lite eligibility when `profile: lite`.
12. Install host guards and probe enforcement tiers (§9.11).
13. Canonicalise and hash feature contract.
14. Lock contract and scope.

Exit guard:

```text
repository_acceptable
AND effective_configuration_valid
AND required_packages_resolved
AND baseline_passed
AND owner_approval_valid
AND profile_eligible
AND enforcement_meets_policy
AND contract_locked
AND scope_locked
```

Transitions:

```text
INITIALISING -> PLANNING
INITIALISING -> BLOCKED
INITIALISING -> FAILED
```

### 14.3 `PLANNING`

Planner inputs:

- Locked contract
- Repository map
- Package metadata
- Available workflow descriptors
- Gates
- Tool descriptors
- Prior rework context where applicable

Planner output must map:

```text
Task -> acceptance criteria -> allowed paths -> tools -> gates -> evidence
```

Planner cannot modify product source, tests, feature contract or gate requirements.

Transitions:

```text
PLANNING -> PLAN_REVIEW
PLANNING -> BLOCKED
```

### 14.4 `PLAN_REVIEW`

Kernel validates:

- Current contract hash
- Scope compliance
- Acceptance-criterion coverage
- Gate coverage
- Package availability
- Tool registration
- Approval points
- Writable-path validity

Decision:

```text
ACCEPTED
REJECTED
REQUIRES_APPROVAL
BLOCKED
```

Transitions:

```text
PLAN_REVIEW -> TEST_DESIGN     (full profile, qe_test_design required or optional)
PLAN_REVIEW -> DEVELOPING      (lite profile, or qe_test_design off)
PLAN_REVIEW -> PLANNING
PLAN_REVIEW -> BLOCKED
```

### 14.5 `TEST_DESIGN`

Purpose: QE writes acceptance tests from the locked contract before any implementation exists, so Development cannot shape the tests that judge it.

QE receives:

- Locked contract
- Accepted plan
- Baseline repository state
- Gate descriptors and QE packages

QE may:

- Write tests in `qe_test_paths` only.
- Run those tests against the baseline.

Exit guard (kernel-validated):

```text
every gate-verified acceptance criterion has at least one QE test mapped to it
AND every test marked expected_on_baseline: fail does fail (or is reported pending) on the baseline
AND every test marked expected_on_baseline: pass passes on the baseline
AND no path outside qe_test_paths changed
```

A test that passes on the baseline while marked `fail` proves nothing about the new behaviour and is rejected with `BASELINE_TESTS_NOT_FAILING`.

When `qe_test_design: optional`, QE may submit an empty test set with a recorded reason; the kernel then proceeds to `DEVELOPING`.

After `TEST_DESIGN`, `qe_test_paths` become protected for Development.

Transitions:

```text
TEST_DESIGN -> DEVELOPING
TEST_DESIGN -> BLOCKED
```

### 14.6 `DEVELOPING`

Development may:

- Read project content, including QE acceptance tests.
- Modify authorised implementation and Development-test paths.
- Use permitted tools.
- Retrieve relevant knowledge.
- Run Development-level checks, including the QE acceptance tests.
- Produce candidate evidence.

Development may not:

- Change contract or scope.
- Modify protected paths.
- Modify QE test paths.
- Weaken failing tests.
- Update golden baselines without approval.
- Write QE verdicts.
- Claim hardware success without evidence.
- Declare final pass.

Checks, each at the tier reported by the host adapter (§3.11):

- Scope, protected paths and QE test paths: blocked by host guards where `ENFORCED`; otherwise detected at each checkpoint
- Contract hash
- Kernel-state integrity
- Tool permissions
- Audit
- State checkpoint

Transitions:

```text
DEVELOPING -> DEVELOPMENT_REVIEW
DEVELOPING -> BLOCKED
DEVELOPING -> FAILED
```

### 14.7 `DEVELOPMENT_REVIEW`

Kernel validates:

- Candidate commit
- Actual diff versus claimed changed paths
- Scope integrity
- Protected paths and QE test paths unchanged
- No derived artefacts committed
- Model-diff artefact or human-review flag for every changed model file
- Test counts not lower than the baseline for any existing suite, and no test file deleted or renamed outside allowed paths (otherwise `TEST_WEAKENING_SUSPECTED`, which requires human review)
- Lite eligibility (lite profile)
- Evidence references
- Development gate results
- Safe repository checkpoint
- No active tools

Transitions:

```text
DEVELOPMENT_REVIEW -> VERIFYING
DEVELOPMENT_REVIEW -> DEVELOPING
DEVELOPMENT_REVIEW -> BLOCKED
```

### 14.8 `VERIFYING`

In the lite profile, the kernel runs the contract's required gates directly; no QE agent is started. The rest of this section describes the full profile.

QE receives:

- Locked contract
- Accepted handoff
- Actual candidate diff
- Required gates
- QE persona and packages
- Applicable baselines and knowledge

QE may:

- Inspect implementation.
- Add QE tests in authorised paths.
- Run mandatory gates.
- Exercise negative, boundary and regression tests.
- Reproduce failures.
- Collect evidence.

QE may not:

- Silently modify implementation.
- Change acceptance criteria.
- Update baselines without approval.
- Treat Development narrative as proof.
- Issue terminal verdict.

Transitions:

```text
VERIFYING -> EVALUATING
VERIFYING -> BLOCKED
```

A failed gate still transitions to `EVALUATING`.

### 14.9 `EVALUATING`

Evaluator checks:

- Contract hash
- Scope integrity
- Criterion coverage, including `human_review` criteria
- Mandatory gates
- Required verification level
- Evidence completeness
- Approval validity and verification source
- Package integrity
- Kernel-state integrity
- Blockers
- Verdict authority (§22.5)

Transitions:

```text
EVALUATING -> PASSED
EVALUATING -> FAILED
EVALUATING -> BLOCKED
EVALUATING -> INCONCLUSIVE
EVALUATING -> REWORK_PLANNING
```

### 14.10 `REWORK_PLANNING`

Owner: the Planner persona holds the modifying lease in the full profile; the kernel generates the rework contract in the lite profile.

A rework contract must include:

- Cycle number
- Failed gate
- Failure signature
- Affected criteria
- Narrow authorised tasks
- Allowed paths
- Required rerun gates
- Original immutable contract hash

Exit guard (rework review, applied by the kernel with the same rules as `PLAN_REVIEW`):

```text
original contract hash unchanged
AND allowed paths are a subset of the original scope
AND every task maps to an affected criterion
AND every failed gate is in the rerun set
AND cycle number <= maximum_cycles
AND, if the same failure signature repeats repeated_failure_threshold times, a valid human-review approval exists
```

QE acceptance tests written in `TEST_DESIGN` stay in force during rework; changing them requires a new `TEST_DESIGN` pass approved by the feature owner.

Transitions:

```text
REWORK_PLANNING -> DEVELOPING
REWORK_PLANNING -> BLOCKED
REWORK_PLANNING -> FAILED
```

---

## 15. Transition matrix

| Current | Event | Next |
|---|---|---|
| `CREATED` | Start requested | `INITIALISING` |
| `CREATED` | Required initial dependency unavailable | `BLOCKED` |
| `INITIALISING` | Initialisation accepted | `PLANNING` |
| `INITIALISING` | Mandatory dependency unavailable | `BLOCKED` |
| `INITIALISING` | Baseline failure established | `FAILED` |
| `PLANNING` | Plan submitted | `PLAN_REVIEW` |
| `PLANNING` | Planning dependency unavailable | `BLOCKED` |
| `PLAN_REVIEW` | Plan accepted; full profile with test design required or optional | `TEST_DESIGN` |
| `PLAN_REVIEW` | Plan accepted; lite profile or test design off | `DEVELOPING` |
| `PLAN_REVIEW` | Plan rejected | `PLANNING` |
| `PLAN_REVIEW` | Approval unavailable | `BLOCKED` |
| `TEST_DESIGN` | Acceptance tests accepted | `DEVELOPING` |
| `TEST_DESIGN` | QE environment unavailable | `BLOCKED` |
| `DEVELOPING` | Handoff submitted | `DEVELOPMENT_REVIEW` |
| `DEVELOPING` | Required tool unavailable | `BLOCKED` |
| `DEVELOPING` | Non-recoverable failure | `FAILED` |
| `DEVELOPMENT_REVIEW` | Handoff accepted | `VERIFYING` |
| `DEVELOPMENT_REVIEW` | Handoff rejected | `DEVELOPING` |
| `DEVELOPMENT_REVIEW` | Approval unavailable | `BLOCKED` |
| `DEVELOPMENT_REVIEW` | Lite eligibility exceeded | `BLOCKED` |
| `VERIFYING` | QE result submitted (full) or kernel gate run recorded (lite) | `EVALUATING` |
| `VERIFYING` | Mandatory QE environment unavailable | `BLOCKED` |
| `EVALUATING` | Completion rules satisfied | `PASSED` |
| `EVALUATING` | Failure established and rework allowed | `REWORK_PLANNING` |
| `EVALUATING` | Failure established and no rework | `FAILED` |
| `EVALUATING` | External dependency unavailable | `BLOCKED` |
| `EVALUATING` | Evidence insufficient | `INCONCLUSIVE` |
| `REWORK_PLANNING` | Rework accepted | `DEVELOPING` |
| `REWORK_PLANNING` | Rework dependency unavailable | `BLOCKED` |
| `REWORK_PLANNING` | Rework limit reached | `FAILED` |
| `BLOCKED` | Blocker resolved and validated | Recorded resumable state |
| `BLOCKED` | Feature abandoned | `FAILED` |
| `INCONCLUSIVE` | Additional evidence authorised | Recorded resumable state |
| Any state except `PASSED` and `FAILED` | Kernel-state tamper detected | `BLOCKED` (blocker `STATE_TAMPER_DETECTED`; resumable only after maintainer recovery) |

A rejected handoff or test-design submission causes no transition; the outgoing persona receives structured corrections (§18).

No other transitions are legal.

---

## 16. Workflow-state persistence

The active workflow's state is stored at `.agent-harness/state/orchestration-state.json` as JSON (JSON is valid YAML; JSON avoids YAML type ambiguity in hashed records). The example below is shown as YAML for readability. `history[].reason` uses the transition-reason vocabulary in §9.19.3.

Example:

```yaml
api_version: embedded-targets.ai/v1
kind: OrchestrationState

workflow:
  id: workflow-FEATURE-001-001
  feature_id: FEATURE-001
  profile: full
  current_state: DEVELOPING
  previous_state: TEST_DESIGN
  resumable_state: DEVELOPING
  revision: 7
  superseded_by: null

ownership:
  persona: development
  agent_instance: dev-001
  host: claude-code
  lease_id: lease-001
  lease_status: active

contract:
  path: features/active/FEATURE-001.yaml
  hash: sha256:placeholder

repository:
  baseline_commit: abc123
  candidate_commit: null

rework:
  current_cycle: 0
  maximum_cycles: 3

active_handoff: null
blocker: null

integrity:
  previous_state_hash: sha256:placeholder
  audit_sequence: 42

history:
  - sequence: 1
    from: CREATED
    to: INITIALISING
    reason: START_REQUESTED
  - sequence: 2
    from: INITIALISING
    to: PLANNING
    reason: INITIALISATION_PASSED
```

Persistence requirements:

- Atomic state replacement
- Revision increment per transition
- Expected-revision comparison before update
- Append-only transition history
- Only kernel writes `current_state`
- State hash chained to the audit log (§9.5)
- Crash-safe recovery
- `PASSED` and `FAILED` are final and reject further modification
- `BLOCKED` and `INCONCLUSIVE` end the current run but may resume, only through the transitions in §15

---

## 17. Agent ownership lease

Example:

```yaml
api_version: embedded-targets.ai/v1
kind: AgentLease

lease:
  id: lease-001
  workflow_id: workflow-FEATURE-001-001
  persona: development
  agent_instance: dev-001
  mode: exclusive-modifying
  acquired_at: null
  last_heartbeat_at: null
  expires_at: null
  status: active
  workflow_revision: 7
```

Requirements:

- One exclusive modifying lease.
- Read-only leases may be used by evaluator and inspection commands.
- Lease acquisition is transactional with state transition.
- Stale revision rejects acquisition.
- Lease loss freezes writes. Where the host tier for writes is `ENFORCED`, the adapter revokes the session's write permissions or terminates it. Otherwise the kernel records a freeze checkpoint (tree hash, §9.1); any later change to the worktree is detected and invalidates the next handoff with `HANDOFF_DIFF_MISMATCH`.
- Recovery must verify repository state before granting a replacement lease.

---

## 18. Handoff protocol

Required sequence:

```text
1. Outgoing persona requests handoff.
2. Kernel freezes outgoing writes (terminate or revoke where ENFORCED;
   record a freeze checkpoint in all cases).
3. Kernel reads actual repository and state.
4. Kernel validates required outgoing artefacts.
5. Kernel creates authoritative handoff package.
6. Kernel validates transition guards.
7. Kernel releases outgoing lease.
8. Kernel commits state transition.
9. Kernel assembles receiving context.
10. Kernel grants receiving lease.
11. Receiving persona starts.
```

If validation fails, no transition occurs. The outgoing persona receives structured corrections.

Before the receiving persona starts, the kernel re-verifies the worktree against the freeze checkpoint. A mismatch voids the handoff.

### 18.1 Handoff package layout

```text
.agent-harness/handoffs/<handoff-id>/
|-- handoff.yaml
|-- effective-contract.yaml
|-- plan.yaml
|-- repository-diff.patch        # text artefacts only
|-- model-diffs/                 # one structured diff per changed model file (§9.21)
|-- enforcement.yaml             # effective tier of each control for the outgoing session
|-- evidence-index.yaml
|-- decisions.md
|-- failures.yaml
`-- next-action.yaml
```

### 18.2 Handoff schema example

```yaml
api_version: embedded-targets.ai/v1
kind: AgentHandoff

metadata:
  id: handoff-dev-to-qe-001
  feature_id: FEATURE-001
  workflow_id: workflow-FEATURE-001-001
  session_id: session-001
  from_agent: dev-001
  from_persona: development
  to_persona: qe
  created_at: null

reason:
  transition: implementation-to-verification
  summary: Candidate implementation and Development evidence submitted.

baseline:
  commit: abc123
  contract_hash: sha256:placeholder
  package_lock_hash: sha256:placeholder

candidate:
  commit: def456
  changed_paths: []
  changed_models: []
  requires_human_model_review: false
  freeze_checkpoint: sha256:placeholder

completed_work: []

verification:
  gates_passed: []
  gates_failed: []
  gates_not_run: []

known_failures: []
open_questions: []

requested_next_action:
  workflow_step: qe-independent-verification
  required_gates: []
```

The kernel must replace agent claims about commit, diff, contract hash and gate status with verified values or reject mismatches.

---

## 19. Plan contract

```yaml
api_version: embedded-targets.ai/v1
kind: FeaturePlan

metadata:
  feature_id: FEATURE-001
  workflow_id: workflow-FEATURE-001-001
  contract_hash: sha256:placeholder
  created_by: planner-001

tasks:
  - id: T-001
    objective: Inspect approved baseline artefact.
    acceptance_criteria:
      - AC-001
    dependencies: []
    writable_paths: []
    required_tools:
      - repository.read
    required_packages: []
    expected_evidence:
      - baseline-inspection

  - id: T-002
    objective: Implement the bounded candidate change.
    acceptance_criteria:
      - AC-001
    dependencies:
      - T-001
    writable_paths:
      - src/example/**
    required_tools:
      - repository.write
    required_packages: []
    expected_evidence:
      - candidate-diff

verification:
  criteria_to_gates:
    AC-001:
      - structural
      - unit

approvals: []
assumptions: []
risks: []
```

Plan validation requires:

- Every task maps to at least one criterion.
- Every criterion maps to one or more gates.
- Writable paths are a subset of feature scope.
- Tools and packages exist.
- Approval points are identified.
- Dependencies are acyclic.

---

## 20. Gate contract

```yaml
api_version: embedded-targets.ai/v1
kind: VerificationGate

gate:
  id: structural
  version: 1.0.0
  name: Structural validation
  level: L0
  owner_persona: qe

preconditions: []

actions:
  - id: validate-structure
    tool: harness.schema-validator
    timeout_seconds: 120
    inputs: {}

evidence:
  required:
    - structural-result

pass_conditions:
  - action_exit_code_zero
  - all_required_evidence_present

failure_policy:
  maximum_retries: 1
  preserve_failed_attempts: true
  require_retry_reason: true
```

Reference-slice L1 gate (§5.4):

```yaml
api_version: embedded-targets.ai/v1
kind: VerificationGate

gate:
  id: matlab-unittest
  version: 1.0.0
  name: MATLAB unit tests
  level: L1
  owner_persona: qe            # kernel in the lite profile

preconditions:
  - matlab_available
  - required_products_licensed

actions:
  - id: run-suites
    tool: matlab.run-tests
    execution_mode: session
    timeout_seconds:
      developer: 900
      ci: 1800
    inputs:
      suites_from_contract: true
      output: junit

evidence:
  required:
    - junit-results
    - test-counts
    - matlab-environment

pass_conditions:
  - action_exit_code_zero
  - zero_failed_tests
  - test_count_not_below_baseline
  - all_required_evidence_present

failure_policy:
  maximum_retries: 1
  preserve_failed_attempts: true
  require_retry_reason: true
  on_precondition_failure: BLOCKED   # never skip or simulate
```

Gate-attempt output:

```yaml
api_version: embedded-targets.ai/v1
kind: GateAttempt

gate_id: structural
attempt: 1
workflow_id: workflow-FEATURE-001-001
feature_id: FEATURE-001
execution_profile: developer       # developer | qe | ci | hardware-lab
start_time: null
end_time: null
tool_invocations: []
exit_code: null
test_counts: null                  # {run, passed, failed, skipped} where applicable
result: INCONCLUSIVE
artefacts: []
stdout_path: null
stderr_path: null
log_hash: null
failure_signature: null
retry_reason: null
```

---

## 21. Evidence bundle

Required layout:

```text
.agent-harness/evidence/<feature-id>/<workflow-id>/
|-- manifest.yaml
|-- environment.yaml
|-- effective-config.yaml
|-- feature-contract.yaml
|-- scope-lock.yaml
|-- packages.lock.yaml
|-- plan.yaml
|-- repository-diff.patch
|-- model-diffs/
|-- enforcement.yaml
|-- approvals.yaml
|-- audit.jsonl
|-- handoffs/
|-- gates/
|   `-- <gate-id>/
|       |-- attempt-001.yaml
|       |-- attempt-002.yaml
|       `-- aggregate.yaml
|-- test-results/
|-- generated-artefacts/
|-- measurements/
|-- qe-result.yaml
`-- verdict.yaml
```

Manifest:

```yaml
api_version: embedded-targets.ai/v1
kind: EvidenceManifest

evidence_bundle:
  feature_id: FEATURE-001
  workflow_id: workflow-FEATURE-001-001
  profile: full
  authority: advisory            # advisory | authoritative (§22.5)
  execution_profile: developer
  created_at: null
  baseline_commit: null
  final_commit: null

provenance:
  harness_version: 0.1.0
  feature_contract_hash: null
  package_lock_hash: null
  agent_instances: []
  independence: null             # same_model | different_model
  environment_fingerprint: null
  matlab: null                   # release, products, licence status (§5.4)

enforcement:
  report: enforcement.yaml
  lowest_tier_for_writes: null   # ENFORCED | DETECTED | ADVISORY

verification:
  required_level: L1
  highest_level_attempted: null
  highest_level_passed: null
  mandatory_gates: []

acceptance_criteria: []
gates: []
artefacts: []
failures: []
approvals: []

integrity:
  complete: false
  manifest_hash: null
```

Evidence rules:

- Preserve contract and lock snapshots.
- Preserve every gate attempt.
- Hash relevant files.
- Map artefacts to criterion, gate and tool invocation.
- Do not mutate historical attempt files.
- Missing required evidence prevents pass.
- Evidence produced under the developer or QE execution profile is marked `advisory` and cannot satisfy a mandatory gate in an authoritative evaluation (§22.5).
- Record the enforcement report for every agent session in the workflow.
- Quarantine of sensitive artefacts is deferred to v1.2; until then, policy must prevent restricted content from entering evidence bundles that are published as CI artefacts.

---

## 22. Deterministic evaluator rules

### 22.1 `PASSED`

Allowed only when all are true:

```text
contract hash matches
AND all acceptance criteria pass
    (gate criteria by gate results; human_review criteria by valid approvals)
AND all mandatory gates pass
AND required verification level is achieved
AND scope validation passes
AND no unapproved protected-path or QE-test-path changes exist
AND no derived artefacts are committed
AND every changed model file has a model diff or a valid human model review
AND no unresolved TEST_WEAKENING_SUSPECTED finding exists
AND package integrity passes
AND kernel-state integrity chain is intact
AND all required approvals are valid, with verification source gitlab where §9.13 requires it
AND L5/L6 human sign-off exists where the required level is L5 or L6
AND evidence manifest is complete
AND no unresolved blocker exists
```

### 22.2 `FAILED`

Use when evidence establishes that a mandatory gate or acceptance criterion failed and no permitted rework remains or rework is declined.

### 22.3 `BLOCKED`

Use when a mandatory dependency, licence, toolchain, environment, instrument, hardware resource or approval is unavailable.

### 22.4 `INCONCLUSIVE`

Use when work occurred but available evidence cannot establish success or failure, including corrupted evidence, environment drift or conflicting authoritative results.

### 22.5 Verdict authority

Every verdict carries `authority`:

- `advisory`: issued under the developer or QE execution profile. Useful for fast feedback; never sufficient to merge.
- `authoritative`: issued under the CI or registered hardware-lab execution profile, where the runner itself re-executed every mandatory gate on the committed candidate, using the committed contract and package lock, and verified approvals through GitLab.

An evaluation that would be `PASSED` but relies on any advisory evidence for a mandatory gate is reported as `PASSED` with `authority: advisory` and reason `VERDICT_NOT_AUTHORITATIVE`. Merge policy must require an authoritative `PASSED`.

Verdict schema:

```yaml
api_version: embedded-targets.ai/v1
kind: FeatureVerdict

feature_id: FEATURE-001
workflow_id: workflow-FEATURE-001-001
profile: full
authority: advisory
execution_profile: developer
verdict: INCONCLUSIVE

acceptance_criteria: []

verification:
  required_level: L1
  highest_level_attempted: null
  highest_level_passed: null
  mandatory_gates_passed: false

scope:
  contract_hash_matches: null
  unauthorised_changes: null
  protected_path_changes: null

package_integrity:
  valid: null
  invalid_packages: []

evidence:
  manifest_complete: false
  missing_artefacts: []

enforcement:
  lowest_tier_for_writes: null

reason_codes:
  - EXECUTION_NOT_STARTED

human_review:
  required: false
  reason: null
```

---

## 23. Rework policy

```yaml
rework_policy:
  maximum_cycles: 3
  require_failure_signature: true
  require_new_evidence_per_cycle: true
  require_scope_revalidation: true
  preserve_all_previous_attempts: true
  repeated_failure_threshold: 2
  on_repeated_failure: require-human-review
```

A rework request must identify:

- Failed gate
- Failure signature
- Affected acceptance criteria
- Root-cause hypothesis or investigation objective
- Narrow authorised change
- Required rerun gates
- New expected evidence

“Try again” is not a valid rework plan.

---

## 24. Built-in persona contracts

### 24.1 Planner

Can read:

- Feature contract
- Repository map
- Package metadata
- Gate descriptors
- Tool descriptors
- Previous failure/rework data

Can write:

- Plan artefact only

Cannot:

- Modify product source or tests
- Change contract or scope
- Waive gates
- Mark tasks complete
- Issue verdict

### 24.2 Development

Can read:

- Locked contract
- Accepted plan
- QE acceptance tests from `TEST_DESIGN`
- Relevant packages and knowledge
- Previous rework request

Can write:

- Feature-authorised implementation paths
- Development test paths
- Development evidence
- Decision record
- Handoff request

Cannot:

- Change scope
- Modify protected paths or QE test paths
- Weaken tests, including deleting, renaming or skipping existing tests
- Approve own work
- Write QE results
- Issue verdict

### 24.3 QE

Can read:

- Locked contract
- Candidate diff
- Development evidence
- Gate definitions
- Approved baselines
- Relevant QE packages and knowledge

Can write:

- Authorised QE test paths (acceptance tests in `TEST_DESIGN`; further tests in `VERIFYING`)
- Gate results
- Defect artefacts
- QE result

Cannot:

- Silently repair implementation
- Change contract
- Update expectations without approval
- Accept Development claims as proof
- Issue final verdict

In `TEST_DESIGN`, QE sees the contract, plan and baseline only; it does not see any candidate implementation.

### 24.4 Evaluator

Can read:

- Immutable contract and hash
- Scope results
- Package lock and integrity
- Gate results
- Evidence manifest
- Approval records
- QE result

Can write:

- Verdict only

Cannot:

- Modify source, tests, gates, plans or evidence
- Run repair actions
- Alter prior results

### 24.5 Intake (optional, before `CREATED`)

Can read:

- Repository map
- Package and gate descriptors
- Feature request text supplied by the feature owner

Can write:

- Draft contracts in `features/backlog/` only

Cannot:

- Approve or lock a contract
- Create a workflow
- Modify product source or tests

The Intake persona's job is to turn a request into a contract that passes the lint in §9.4.1: testable acceptance criteria, gate mappings, a tight scope and a sensible profile. The feature owner reviews and approves the draft.

---

## 25. Tool risk classes

### Class A: Read-only

Examples:

- Read files
- Inspect repository
- Read logs
- Query approved knowledge

Default: allowed when package and persona permit.

### Class B: Local and reversible

Examples:

- Modify authorised files
- Run unit tests
- Generate temporary artefacts

Default: allowed only in authorised paths and workflow states.

### Class C: Environment-changing

Examples:

- Change paths
- Change compiler configuration
- Install or remove dependencies
- Invoke vendor configuration tools

Default: controlled and logged; installation disabled by default.

### Class D: Hardware-affecting

Examples:

- Flash or reset a board
- Change boot configuration
- Drive physical outputs
- Reconfigure instruments

Default: explicit approval with verification source `gitlab`, and safety preconditions. The action must run through a kernel-registered tool on a host whose tier for that tool is `ENFORCED`, or be performed by a human. Otherwise policy returns `DENY` with `ENFORCEMENT_TIER_INSUFFICIENT`.

### Class E: Protected engineering action

Examples:

- Update golden baseline
- Modify qualification artefacts
- Change public API
- Merge or publish
- Change release metadata

Default: human approval with verification source `gitlab` required. The same tier rule as Class D applies.

---

## 26. CLI requirements

Provide commands such as:

```text
harness bootstrap
harness doctor                      # includes enforcement tiers and projection drift
harness feature create
harness feature draft               # Intake persona, writes to features/backlog/
harness feature lint
harness feature approve             # records feature-owner approval
harness workflow create
harness workflow start
harness workflow status
harness workflow history
harness workflow resume
harness workflow block
harness workflow cancel
harness persona launch <persona>
harness handoff submit
harness handoff validate
harness gate run <gate-id>
harness verify --execution-profile <developer|qe|ci|hardware-lab>
harness evidence collect
harness evidence validate
harness evaluate
harness close
harness package scaffold
harness package validate
harness package test
harness package resolve
harness config show-effective
harness hook pre-tool               # entrypoint called by host hooks; reads a JSON request on stdin
harness enforcement probe
harness ci verify                   # CI entrypoint: re-run gates, verify approvals, authoritative verdict
```

`harness hook pre-tool` must be a Python console entrypoint (not a shell script) so it works on Windows and Linux, must answer within a configured time budget, and must fail closed (`DENY`) on any internal error.

Every command must:

- Return a meaningful exit code.
- Emit concise human-readable status.
- Support structured JSON output.
- Validate lifecycle preconditions.
- Avoid partial state writes.
- Audit material actions.
- Never report success if the underlying operation is unavailable or simulated.

---

## 27. Error and reason-code model

Define stable reason codes, including at least:

```text
CONFIG_INVALID
SCHEMA_VERSION_UNSUPPORTED
FEATURE_CONTRACT_INVALID
MULTIPLE_ACTIVE_FEATURES
NO_ACTIVE_FEATURE
BASELINE_DIRTY
BASELINE_FAILED
CONTRACT_HASH_MISMATCH
CONTRACT_MUTATED_AFTER_LOCK
PATH_OUTSIDE_SCOPE
PROTECTED_PATH_CHANGED
PACKAGE_NOT_FOUND
PACKAGE_VERSION_CONFLICT
PACKAGE_DEPENDENCY_CYCLE
PACKAGE_DIGEST_MISMATCH
TOOL_NOT_REGISTERED
TOOL_PERMISSION_DENIED
APPROVAL_REQUIRED
APPROVAL_INVALID
LEASE_ALREADY_HELD
LEASE_STALE
WORKFLOW_REVISION_CONFLICT
TRANSITION_NOT_ALLOWED
HANDOFF_INVALID
HANDOFF_DIFF_MISMATCH
PLAN_COVERAGE_INCOMPLETE
GATE_PRECONDITION_FAILED
GATE_TIMEOUT
GATE_EVIDENCE_MISSING
RETRY_LIMIT_EXCEEDED
REWORK_LIMIT_EXCEEDED
EVIDENCE_MANIFEST_INCOMPLETE
ENVIRONMENT_DRIFT
MANDATORY_DEPENDENCY_UNAVAILABLE
EXECUTION_NOT_STARTED
NOT_IMPLEMENTED
OWNER_APPROVAL_MISSING
ACCEPTANCE_CRITERION_UNVERIFIABLE
PROFILE_ESCALATION_REQUIRED
ENFORCEMENT_TIER_INSUFFICIENT
HOOK_DECISION_TIMEOUT
STATE_TAMPER_DETECTED
PROJECTION_DRIFT
QE_TEST_MODIFIED
BASELINE_TESTS_NOT_FAILING
TEST_WEAKENING_SUSPECTED
DERIVED_ARTEFACT_COMMITTED
MODEL_DIFF_UNAVAILABLE
HUMAN_REVIEW_REQUIRED
APPROVAL_UNVERIFIABLE
VERDICT_NOT_AUTHORITATIVE
MATLAB_UNAVAILABLE
MATLAB_PRODUCT_MISSING
LICENCE_UNAVAILABLE
TOOLCHAIN_UNAVAILABLE
```

Reserved for v1.2 (defined but not emitted by the MVP): `PACKAGE_REVOKED`, `SENSITIVE_ARTEFACT_QUARANTINED`.

Errors must carry structured context without exposing secrets.

---

## 28. Required tests

### 28.1 Configuration and schemas

- Valid configuration passes.
- Missing required keys fail.
- Unsupported schema version fails.
- Effective precedence is correct.
- User-local settings cannot weaken organisation policy.

### 28.2 Feature and scope

- Exactly one active feature passes.
- Zero active features fails start.
- Multiple active features fail start.
- Canonical hash is stable.
- Contract mutation after lock fails.
- Allowed path passes.
- Outside-scope path fails.
- Protected path fails even if overlapping an allowed glob.
- Added, deleted, renamed and copied unauthorised files are detected.
- Path traversal is rejected.
- A criterion without a verification method fails lint (`ACCEPTANCE_CRITERION_UNVERIFIABLE`).
- A contract without feature-owner approval cannot be locked (`OWNER_APPROVAL_MISSING`).
- Canonical hash is identical for semantically equal YAML with different key order and formatting.
- Paths are classified into the correct artefact class; derived paths do not raise scope violations.

### 28.3 Packages

- Valid manifest passes.
- Duplicate ID fails.
- Incompatible version fails.
- Missing dependency fails.
- Dependency cycle fails.
- Digest mismatch fails.
- A Git source not pinned to a commit SHA is rejected.
- Lock-file resolution is reproducible.
- Package metadata version consistency is checked.

### 28.4 State machine

- Valid feature reaches `PLANNING`.
- Invalid baseline prevents planning.
- Planner writes to product files are blocked where `ENFORCED`, and rejected at `PLAN_REVIEW` where `DETECTED`.
- Plan without gate coverage is rejected.
- Development cannot start without accepted plan.
- With test design required, `PLAN_REVIEW` leads to `TEST_DESIGN`, not `DEVELOPING`.
- Development cannot transition directly to `VERIFYING`.
- Invalid transition fails without state change.
- Every transition increments revision.
- Stale revision fails.
- `PASSED` and `FAILED` reject modification; `BLOCKED` and `INCONCLUSIVE` resume only through §15 transitions.
- A hand-edited state file is detected (`STATE_TAMPER_DETECTED`) and the workflow moves to `BLOCKED`.

### 28.5 Leases

- Only one modifying lease exists.
- Planner lease is released before Development lease.
- QE cannot acquire while Development lease is active.
- Lease loss records a freeze checkpoint; any later worktree change invalidates the next handoff.
- Recovery validates repository before replacement lease.

### 28.6 Handoffs

- Planner-to-Development handoff succeeds with valid plan.
- Development-to-QE handoff succeeds with matching diff.
- Mismatched changed paths reject handoff.
- Missing evidence rejects handoff.
- Active tool process rejects or blocks handoff.
- Receiving persona gets fresh bounded context.
- Prior chat history is not required.

### 28.7 Tool broker

- Unregistered tool is denied.
- Persona permission is enforced.
- Workflow-state permission is enforced.
- Path restriction is enforced.
- Required approval pauses execution.
- Timeout terminates execution.
- Stdout and stderr are captured separately.
- Secret redaction hook operates.
- Artefacts are registered.
- `harness hook pre-tool` returns `DENY` for an out-of-scope write request and `ALLOW` for an in-scope one.
- `harness hook pre-tool` fails closed on malformed input, internal error and timeout.
- A session-scoped tool reuses one session across consecutive gate actions and cleans it up on cancellation.

### 28.8 Gates

- Preconditions are enforced.
- Steps execute in order.
- Missing required evidence prevents pass.
- Failed attempts remain immutable.
- Retry limit is enforced.
- Retry requires a reason.
- Aggregate result reflects attempts correctly.

### 28.9 QE and evaluation

- QE cannot write implementation paths.
- Failed QE gate reaches evaluator.
- All required conditions can produce `PASSED`.
- Failed mandatory gate produces failure or rework.
- Missing mandatory hardware produces `BLOCKED` for L5.
- Missing or conflicting evidence produces `INCONCLUSIVE`.
- Contract mismatch prevents pass.
- Unauthorised change prevents pass.
- Evaluator cannot modify source or evidence.
- A `human_review` criterion without a valid approval prevents pass.
- A committed derived artefact prevents pass.
- A changed model file without a model diff or human model review prevents pass.
- A lower test count than baseline raises `TEST_WEAKENING_SUSPECTED` and prevents pass until reviewed.
- An L5 feature without GitLab-verified human sign-off cannot pass.

### 28.10 Rework

- Rework preserves original contract hash.
- Rework scope remains within original scope.
- Prior failure evidence remains.
- New cycle requires new evidence.
- Repeated failure triggers review.
- Maximum cycle count is enforced.

### 28.11 Restart and recovery

- Interrupted session resumes from disk.
- Completed steps remain complete.
- Handoff survives process restart.
- Crash during atomic write leaves valid prior state.
- Blocked workflow resumes at recorded state.
- Environment drift is detected.

### 28.12 Agent adapters

- Claude Code and Codex receive equivalent semantic instructions.
- Host-specific rendered files are generated from common source and installed where each host reads them.
- A hand edit to an installed projection is reported as `PROJECTION_DRIFT`.
- Unsupported launch capability reports guided mode, not success.
- Agent events normalise to common schema.

### 28.13 End-to-end

Create a synthetic reference project and prove, for the full profile:

1. Bootstrap.
2. Create, lint and approve feature.
3. Initialise workflow.
4. Planner produces valid plan.
5. QE writes acceptance tests that fail on the baseline.
6. Development implements a small authorised change.
7. Kernel accepts Development handoff.
8. QE runs independent tests.
9. Evaluator emits `PASSED` (advisory locally).
10. `harness ci verify` re-runs gates and emits an authoritative `PASSED`.
11. Evidence validates.
12. A fresh process loads terminal state.

Prove the lite profile on the same project: one Development session, kernel-run gates, the same evidence schema, and an authoritative `PASSED` in CI.

Also implement negative end-to-end scenarios for `FAILED`, `BLOCKED`, `INCONCLUSIVE`, one rework cycle, and lite-profile escalation.

### 28.14 Enforcement tiers

- Each adapter reports a tier for every control in §9.11.
- A control is reported `ENFORCED` only when its probe was blocked.
- With the Claude Code guards installed, a probe write outside scope is blocked.
- With guards absent, the same write is reported `DETECTED` and is caught at the next checkpoint.
- A Class D tool on a host without an `ENFORCED` tier is denied with `ENFORCEMENT_TIER_INSUFFICIENT`.

### 28.15 Profiles and test design

- A lite contract that requires L4 is rejected at lint.
- A lite candidate exceeding the size limit moves the workflow to `BLOCKED` with `PROFILE_ESCALATION_REQUIRED`.
- An acceptance test marked `expected_on_baseline: fail` that passes on the baseline is rejected.
- A Development change to a QE test path is rejected with `QE_TEST_MODIFIED`.

### 28.16 CI authority and approvals

- A locally issued verdict is always `advisory`.
- `harness ci verify` ignores local evidence for mandatory gates and re-runs them.
- A Class D/E or `human_review` approval with source `local` produces `APPROVAL_UNVERIFIABLE` in CI.
- A GitLab-verified approval by a user outside the approver group is rejected.

### 28.17 Reference domain slice

These tests need a runner with MATLAB and the reference toolchain. Where unavailable, they must report `BLOCKED`, never pass by skipping.

- `matlab-unittest` runs a real suite, emits JUnit and test counts, and maps results to criteria.
- Missing MATLAB, product or licence produces `BLOCKED` with the matching reason code.
- `codegen-compile-<reference-target>` generates and compiles code for the reference model and preserves the report and build log.
- A changed `.slx` file produces either a model diff from the registered provider or a human-review requirement.
- Derived folders created by code generation (for example `slprj/`) do not raise scope violations.

### 28.18 Audit vocabulary and run monitor

- Every `event_type` the kernel emits is listed in §9.19.2, and each record carries the required fields and `details` keys for its type.
- `reason_codes` in every kernel-emitted record contain only §27 codes; `state.transition` carries a §9.19.3 reason in `details.reason`.
- `sample-run.jsonl` validates against the same audit record schema as kernel output.
- The monitor server returns 405 for `POST`, `PUT`, `PATCH` and `DELETE`, and 404 for any path other than `/`, `/index.html`, `/api/snapshot` and `/api/stream`.
- Running the monitor against a fixture `.agent-harness` directory leaves every file byte-identical and creates no new file.
- A partial trailing audit line is not emitted until it is complete; a truncated log emits `reset`; a malformed line emits `warning`.
- Replaying the simulator run through the live stream delivers every event in `sequence` order and the final state file revision.
- An advisory `PASSED` verdict is rendered with its advisory authority.

---

## 29. Non-functional requirements

### Reliability

- Atomic state writes
- Idempotent recovery operations
- Explicit timeouts
- No silent exception swallowing
- Immutable historical evidence

### Security

- No unrestricted default shell
- Validate paths and schemas
- Avoid logging secrets
- Enforce package source and digest
- Treat retrieved content as data, not instructions

### Portability

- Support Windows and Linux in the core design.
- Avoid shell-specific logic in domain services.
- Put OS-specific behaviour behind adapters.

### Performance

- Reuse a warm MATLAB session across consecutive gate actions where the session adapter is available.
- Record MATLAB start-up, licence checkout and per-gate durations in evidence so slow gates are visible.
- `harness hook pre-tool` decisions must complete within a configurable budget (default 2 seconds) so they do not stall the agent host.

### Usability

- One-command bootstrap
- Clear status display
- Reason-coded failures
- Exact next action after block or rejection
- Generated examples and starter project

### Maintainability

- Typed models
- Small modules with single responsibilities
- Ports-and-adapters architecture
- Core domain independent of CLI and subprocess implementation
- Versioned schemas

### Reproducibility

- Pin harness and packages
- Capture environment fingerprint
- Persist contract and package digests
- Preserve tool and gate versions

### Observability

- Append-only audit
- Correlation by workflow, session, feature and agent
- Structured outputs suitable for GitLab artefacts

---

## 30. Implementation sequence

Build one feature at a time and do not claim later capabilities before their tests pass.

The sequence is grouped into three milestones. Milestone A is the first usable release: a governed single-agent loop with a real MATLAB gate. It deliberately comes before multi-agent orchestration so the team gets value, and real feedback on gate speed and scope rules, early.

Mapping from 1.0:

| 1.0 | 1.1 | Change |
|---|---|---|
| HK-001 | HK-001 | Adds state integrity chain |
| HK-002 | HK-002 | Adds lint, owner approval, artefact classes |
| HK-003 | HK-003 | Adds `TEST_DESIGN` and profile-aware ownership |
| HK-009 | HK-004 | Moved earlier; adds execution profiles and session tools |
| HK-010 (evaluator part) | HK-005 | Moved earlier; adds authority marking |
| — | HK-006 | New: MATLAB port, L1 gate, lite profile |
| HK-004 | HK-007 | Adds freeze checkpoints |
| HK-005 | HK-008 | Adds test design and model diffs |
| HK-006 | HK-009 | Reduced scope (§5.5) |
| HK-007 | HK-010 | Adds enforcement tiers and host guards |
| HK-008 | HK-011 | Adds GitLab-source approvals and tier rules |
| HK-010 (QE part), HK-011 | HK-012 | Adds test-weakening detection and rework review |
| HK-012 | HK-013 | Adds CI authority |
| — | HK-014 | New: L3 code-generation slice |
| — | HK-015 | New (1.1.1): read-only run monitor and audit vocabulary conformance |

### Milestone A: Governed single-agent loop

Outcome: a developer runs Claude Code or Codex as usual on a real Embedded Targets repository. The kernel locks an approved contract, detects scope violations, runs a real MATLAB gate and issues an evidence-backed advisory verdict.

#### HK-001: Kernel skeleton

Deliver:

- Python package
- CLI framework
- Configuration loading
- Schemas
- Repository discovery
- Atomic state store with integrity chain
- Audit writer

#### HK-002: Feature contract, scope and artefact classes

Deliver:

- Feature validation and acceptance-criterion lint
- RFC 8785 canonical hashing
- Feature-owner approval record (local source)
- Scope lock
- Changed-path validation with tree-hash checkpoints
- Protected-path detection
- Artefact classification (§9.21)

#### HK-003: Orchestration state machine

Deliver:

- State enums, including `TEST_DESIGN`
- Transition table
- Profile-aware state ownership
- Revision checks
- Final-state enforcement
- Block and resume
- Transition audit

#### HK-004: Gate engine and evidence

Deliver:

- Sequential workflow executor
- Gate ordering
- Precondition checking
- Attempts, timeout and retry
- Execution profiles and per-profile timeouts
- Session-scoped tool support
- Test-count capture
- Evidence manifest, hashing and collection

#### HK-005: Deterministic evaluator

Deliver:

- Verification-level calculation
- Deterministic verdict rules (§22)
- Authority marking
- Verdict file

#### HK-006: MATLAB session port, L1 gate and lite profile

Deliver:

- `MatlabSessionPort` with `batch` and `session` adapters (§11.9)
- `matlab-unittest` gate package (§5.4)
- Kernel-generated plans and kernel-run verification
- Lite eligibility checks
- Minimal Claude Code and Codex instruction projections (operating contract and active feature)
- Guided launch mode

Exit criterion: one real lite-profile feature on an Embedded Targets repository reaches an advisory `PASSED` with a real `matlab-unittest` result.

#### HK-015: Read-only run monitor (added in 1.1.1)

Deliver:

- `tools/monitor/` as described in §9.22
- A JSON Schema for the audit record (§9.19.1) and vocabulary (§9.19.2), used by the kernel's own tests and by `sample-run.jsonl`
- Tests in §28.18
- `docs/monitor.md`

Dependencies: HK-003, so that `state.transition` events exist. It may be built at any point after HK-003; it is listed here because it serves the first users of Milestone A. Until then, every HK feature must emit its events in the §9.19.2 vocabulary so the monitor needs no kernel change when it lands.

### Milestone B: Role-isolated orchestration

Outcome: the full profile works end to end with Planner, QE test design, Development and QE, with honest enforcement reporting.

#### HK-007: Persona and lease system

Deliver:

- Persona registry
- Agent-instance records
- Exclusive leases
- Freeze checkpoints
- Recovery behaviour

#### HK-008: Plan, test-design and handoff protocol

Deliver:

- Plan schema and review
- `TEST_DESIGN` exit guard
- Handoff schema
- Actual-diff validation
- Model-diff provider interface with hash-only fallback
- Freeze/release/acquire sequence

#### HK-009: Package manager

Deliver:

- Package schema
- Exact-version lock with local-path and pinned-SHA sources
- Dependency and cycle checks
- Digests
- Activation explanations

#### HK-010: Context broker, agent adapters and enforcement tiers

Deliver:

- Common context object
- Progressive context selection
- Full Claude Code and Codex projections, installed where each host reads them
- Projection drift detection
- `install_guards`, `probe_guards` and `harness hook pre-tool`
- Claude Code pre-tool hook and permission rules
- Codex sandbox and approval configuration
- Enforcement report

#### HK-011: Policy, approval and tool broker

Deliver:

- Policy decisions, including minimum-tier rules for Class D and E
- Approval records with `local` and `gitlab` verification sources
- Registered local command tool
- Permission and scope checking
- Structured tool results

#### HK-012: QE, rework, recovery and closure

Deliver:

- QE result schema
- Test-weakening detection
- Rework contract and rework review guard
- Cycle limit
- Resume after block, interruption or tamper recovery
- Final handoff and closure

### Milestone C: CI authority and code-generation slice

Outcome: merge decisions rest on authoritative CI verdicts, and the kernel is proven on a real L3 code-generation feature.

#### HK-013: GitLab CI authority and starter project

Deliver:

- CI template (§32)
- `harness ci verify`
- GitLab approval verification
- Authoritative verdicts
- Synthetic starter project
- Local/CI parity test
- Installation and usage documentation

#### HK-014: L3 code-generation slice

Deliver:

- `codegen-compile-<reference-target>` gate package
- Reference-target skill package
- Simulink model-diff provider, or a recorded decision to use the hash-only fallback with human model review
- A CI runner with MATLAB and the target toolchain

Exit criterion: one real full-profile feature reaches an authoritative `PASSED` at L3.

### Bootstrapping rule

HK-001 to HK-003 are built before the kernel can govern its own work. For those three features, use a hand-written contract in the §9.4.1 format, pytest results and a short evidence note, reviewed and approved by a human. From HK-004 onward, use the kernel's own contract, scope and gate commands. Once HK-005 passes its tests, re-run the evaluator over the HK-001 to HK-004 records at L0 and L1 so all features are judged by the same rules. Do not claim self-hosting before the component that provides it passes its own tests.

Apart from this rule, each HK feature must have its own feature contract and evidence bundle.

---

## 31. Suggested built-in CLI output

Example status:

```text
Workflow: workflow-FEATURE-001-001
Feature: FEATURE-001
Profile: full
State: DEVELOPMENT_REVIEW
Revision: 12
Contract: locked, hash verified, owner approval verified (local)
Owner: kernel
Previous agent: dev-001 (Claude Code)
Enforcement: writes ENFORCED, protected paths ENFORCED, shell ADVISORY
Scope: valid
Candidate commit: def456
Required next action: validate Development handoff
Blocked capabilities: none
Evidence bundle: incomplete
Verdict authority: advisory (authoritative verdict comes from CI)
```

Example transition rejection:

```text
Transition rejected: DEVELOPMENT_REVIEW -> VERIFYING

Reasons:
- HANDOFF_DIFF_MISMATCH: 2 changed files were not declared.
- GATE_EVIDENCE_MISSING: development unit-test result is absent.

Next action:
Return control to Development, update the handoff from actual repository
state, and attach the required unit-test result. The feature contract and
scope remain locked.
```

---

## 32. GitLab CI requirements

Provide a reusable template with stages resembling:

```text
validate
  - configuration
  - schemas
  - feature contract
  - package lock

scope
  - contract hash
  - allowed paths
  - protected paths

test
  - kernel tests
  - project tests
  - package tests

verify
  - re-execute every mandatory gate for the contract's required level
  - verify approvals through the GitLab API

evidence
  - collect
  - hash
  - validate

evaluate
  - deterministic verdict (authoritative)
```

CI must:

- Use the committed package lock.
- Reject uncommitted/unlocked packages.
- Reconstruct inputs from the committed contract, package lock and candidate commit; never read local `.agent-harness/state/` or local evidence as proof.
- Re-execute every mandatory gate itself. Local gate results may be shown for comparison only.
- Verify feature-owner, `human_review`, Class D/E and L5/L6 approvals against GitLab identities and the configured approver group.
- Run MATLAB-dependent gates only on runners tagged with the required MATLAB release, products and toolchain; on other runners, report `BLOCKED`.
- Run L5 gates only on registered hardware-lab runners.
- Publish evidence and the authoritative verdict as pipeline artefacts.
- Use the same result schemas as local execution.
- Never infer a pass from job completion alone.
- Fail the merge-request pipeline unless the verdict is an authoritative `PASSED`.

---

## 33. Required documentation

Create:

- Architecture overview
- Quick start for Development
- Quick start for QE
- Claude Code setup
- Codex setup
- Enforcement tiers per host: what is enforced, detected or advisory, and how to verify it
- Feature-contract authoring, including acceptance-criterion verification methods and the Intake persona
- Workflow profiles: when to use lite and how escalation works
- Simulink models and derived artefacts
- MATLAB runner setup for local and CI use
- Package authoring
- Workflow and gate authoring
- Policy and approval guide, including GitLab-verified approvals
- Failure and recovery guide
- GitLab CI integration and verdict authority
- Security and sensitive-content guide
- Troubleshooting and reason-code reference
- Run monitor: live, replay and demo modes, and what it cannot show (§9.22)

The quick start must work from a clean clone without undocumented manual steps.

---

## 34. Instructions to Claude Code or Codex implementing this specification

### Before modification

1. Inspect the repository read-only.
2. Identify existing language, packaging, tests and instructions.
3. Run the existing baseline tests.
4. Create the first implementation feature contract, `HK-001`, following the bootstrapping rule (§30) and ask the human for approval before locking scope.
5. Map planned tasks to acceptance criteria and tests.
6. Lock scope before editing.

### During implementation

1. Implement only the active HK feature.
2. Keep domain logic separate from CLI and adapters.
3. Add tests with every behaviour.
4. Use synthetic fixtures for kernel behaviour. Use real MATLAB only in HK-006 and HK-014, and only through `MatlabSessionPort`.
5. Persist machine-readable state.
6. Preserve failures.
7. Do not fabricate integrations or results. If MATLAB, a licence or a toolchain is unavailable, report `BLOCKED`.
8. Do not implement deferred capabilities (§5.5) opportunistically.
9. Do not report a control as `ENFORCED` without a passing probe test.
10. Record assumptions and limitations.
11. Leave an exact handoff at the end of every session.

### Completion requirements

Do not declare an HK feature complete unless:

- Acceptance criteria pass.
- Required tests pass.
- Scope validates.
- Contract hash matches.
- Evidence bundle validates.
- Restart behaviour is tested when applicable.
- Deterministic evaluator emits `PASSED` (from HK-005 onward; before that, the bootstrapping rule applies).
- For HK-013 and later, the verdict is authoritative from CI.

### Required final implementation report

Report:

- Active feature implemented
- Files added and modified
- Acceptance-criterion status
- Tests and gates run
- Evidence path
- Verdict and its authority
- Enforcement tier of each control, where the feature touches enforcement
- Deferred capabilities
- Known limitations
- Exact next command

---

## 35. Initial prompt to use with this specification

```text
Treat the attached Markdown file as the authoritative build contract for the
Embedded Targets Agentic AI Harness Kernel and Multi-Agent Orchestrator.

Implement the system incrementally using the HK feature sequence in §30, which
is grouped into Milestones A, B and C. Begin with HK-001 only. Before modifying
files, inspect the repository, read existing instructions, run baseline tests,
create the HK-001 feature contract and plan under the bootstrapping rule, ask
me to approve the contract, and lock scope.

The MVP is sequential and role-isolated: Planner -> QE test design ->
Development -> QE -> Deterministic Evaluator, plus a lite profile in which the
kernel plans and runs gates around a single Development session. Only the
kernel may change workflow state. Agents may request transitions and submit
artefacts, but they may not write authoritative orchestration state directly.
Only one modifying agent lease may exist.

Use Python for the reference implementation unless this repository already has
a clearly established implementation language suitable for the kernel. Keep
core domain logic independent of CLI, Git, filesystem, subprocess, MATLAB,
Claude Code and Codex adapters.

Be honest about enforcement: classify every control as ENFORCED, DETECTED or
ADVISORY per host, and never report ENFORCED without a passing probe test.

Do not build concurrent agents, background agents, a remote service, a user
interface, a knowledge graph, hardware scheduling, autonomous merging or any
capability deferred in §5.5. Real MATLAB integration belongs only to HK-006
and HK-014. Use synthetic fixtures elsewhere and report deferred integrations
accurately. If MATLAB, a licence or a toolchain is unavailable, report BLOCKED.

Do not declare completion without passing tests, matching contract hash, valid
scope, complete evidence, restartable state where required and a deterministic
PASSED verdict.
```

---

## 36. Final acceptance definition

The MVP is accepted when all of the following hold.

**Kernel acceptance (synthetic project).** A fresh user can clone the harness and synthetic product project, bootstrap either Claude Code or Codex, execute a complete full-profile workflow (Planner, QE test design, Development, QE), observe kernel-validated role handoffs, obtain an evidence-backed deterministic verdict, interrupt and resume the workflow from persisted state, run the same feature in the lite profile, and obtain an authoritative verdict from GitLab CI.

**Enforcement acceptance.** `harness doctor` reports the tier of every control for both hosts, each `ENFORCED` claim is backed by a passing probe, and at least the out-of-scope write control is `ENFORCED` on Claude Code.

**Domain acceptance (real repository).** On a real Embedded Targets repository:

1. A lite-profile feature reaches an authoritative `PASSED` using the real `matlab-unittest` gate (end of Milestone A for the advisory result; Milestone C for the authoritative one).
2. A full-profile feature reaches an authoritative `PASSED` at L3 using the real `codegen-compile-<reference-target>` gate.
3. If the MATLAB or toolchain runner is unavailable, these items are recorded as `BLOCKED`, and MVP acceptance is not claimed.

The kernel's end-to-end contract is:

```text
Feature intent
  -> approved, immutable contract
  -> bounded plan
  -> [acceptance tests written before implementation]
  -> authorised agent ownership
  -> approved packages and tools
  -> candidate implementation
  -> validated handoff
  -> independent verification
  -> immutable evidence
  -> deterministic verdict (advisory locally, authoritative in CI)
  -> restartable closure
```
