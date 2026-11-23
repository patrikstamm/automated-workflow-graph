# Project Decisions

Decisions the team has agreed on. Change this file through a reviewed commit, not silently.
Last updated: 2026-11-24.

## Open items

| Item | Status |
|---|---|
| Final deadline | TBD |
| Checkpoints | TBD |
| Owner: prompt-to-LLM | patrick |
| Owner: evaluator | om |
| Owner: improver | march |

## 1. Graph format

### 1.1 Two layers

| Layer | Written by | Purpose |
|---|---|---|
| **Draft** (`WorkflowDraft`) | the LLM | Simple and flat; no gateway nodes. Same Pydantic model for Claude and Gemini. |
| **Normalized** (`WorkflowGraph`) | our code (`normalize`) | Explicit gateways and edge kinds; a superset of AgentProof's dict format (`agentproof.graph.model.graph_from_dict`). All checks and the improver work on this layer. |

### 1.2 Draft (what the LLM writes)

- Node kinds: `start`, `end`, `llm`, `tool`, `human`, `router`.
- Node fields: `id`, `kind`, `task` (llm/human), `tool` + `args` (tool), `reads`, `writes`, `join`.
- `args` is a list of `{name, value}` pairs. **Values are always strings.** `"$x"` refers to state value `x`;
  list parameters are written as a JSON string (e.g. `"[\"a@x.com\"]"`) and checked against the tool's type.
- Branching:
  - `router` or `human` with several outgoing edges, each with a `condition` → choose one branch (XOR-split).
  - Any other node with several outgoing edges, no conditions → run all branches (AND-split).
- Merging: a node with several incoming edges must set `join`:
  - `"all"` → wait for every branch (AND-join)
  - `"any"` → continue with whichever branch arrives (XOR-join)
- The LLM never writes OR-joins (only our completion step creates them) and never marks loops (our code finds them).

### 1.3 Format checks (on parsing)

A failure is a **format error** and goes back to the LLM. It is not a verification FAIL.
All errors are returned at once, so one repair round can fix them together.

- The response is valid JSON and matches the draft model (no unknown fields).
- Unique snake_case ids without double underscores (so they never clash with our `__split`/`__join` names).
- Every edge points to existing nodes; no edge appears twice.
- Exactly one `start` (no incoming edges); at least one `end` (no outgoing edges).
- `tool` nodes: `tool` is set and exists in the catalog; `args` names match its parameters exactly; literal values parse as the parameter type.
- Only `tool` nodes may have `tool` and `args`.
- `llm` and `human` nodes have a non-empty `task`.
- A `router` has ≥ 2 outgoing edges, each with a unique condition. A branching `human` follows the same rule. No other edges have conditions.
- `join` is set if and only if the node has more than one incoming edge.
- `reads` and `writes` names are snake_case.
- Every `"$x"` in `args` appears in the node's `reads`. A value is a reference only when it is exactly `"$name"`; text such as `"costs $5"` is a literal.

Not checked here: reachability, dead ends, router shape, human gates (AgentProof), and
written-before-read (our data-flow check).

### 1.4 Normalized graph

- Node kinds use AgentProof's names: `entry`, `exit`, `llm`, `tool`, `human`, `router`, `passthrough`.
- A node that starts several parallel branches gets a `<id>__split` gateway after it (AND).
- A node with several incoming edges gets a `<id>__join` gateway before it (AND for `"all"`, XOR for `"any"`).
- Every inserted gateway has kind `passthrough`; its logic is in metadata.
- `router` and branching `human` nodes are XOR-splits themselves; no extra node.
- `start` has exactly one outgoing edge (Vanhatalo); if not, a split gateway is added.
- Node `metadata`: `origin_id` (draft id, used to map witness traces back for repair), `task`, `args`,
  `reads`, `writes`, and `gateway: {role: split|join, logic: AND|XOR|OR}` on inserted gateways and on
  branching `router`/`human` nodes.
- Edge kinds: `conditional` (out of router/branching human), `parallel` (out of an AND-split), `direct` otherwise.
  Loop edges keep their kind and get `loop: true` in edge metadata (AgentProof requires every edge out of
  a router to be `conditional`).

### 1.5 Conversion to AgentProof

| Draft | Normalized graph / AgentProof |
|---|---|
| `start` / `end` | `entry` / `exit` (entry and exit ids derived automatically) |
| `llm`, `human`, `router` | same kinds |
| `tool` | `tool`, with `tools=[tool_name]` |
| AND or XOR gateway (inserted by `normalize`) | `passthrough` |

- Gateways are never mapped to `router`: AgentProof requires every edge out of a `router` to be
  `conditional`, and a join gateway has one plain outgoing edge.
- `to_agentproof_dict()` returns a plain dict and does not import AgentProof (section 3). The evaluator
  turns it into an `AgentGraph` with `graph_from_dict()`.
- AgentProof's `graph_from_dict` keeps node metadata but drops edge metadata, so `loop` exists only in our format.
- A dead end is reported by two AgentProof checks: `dead_ends`, and `reverse_reachability` (the dead end
  and every step that leads only to it). The evaluator merges these so the LLM gets each fault once.

### 1.6 Test graphs

1. Linear echo — passes everything.
2. AgentProof paper's email triage with a dead end — format OK; AgentProof `dead_ends` and
   `reverse_reachability` fail; the witness maps back to `start → classify → router → normal_handler → draft_response`.
3. Parallel fetch + AND-join — correct split and join inserted.
4. Write draft → review (router) → back to write draft — loop detected, edge stays `conditional`.
5. Human approve/reject — human acts as an XOR-split.
6. Vanhatalo's multiple-ends example (slide G0) — 3 exits kept for later completion.
7. 22 malformed drafts, one per format error type — each gives its specific error.

### 1.7 Files

New code lives in `src/schema/`: `draft.py`, `graph.py`, `normalize.py`, `to_agentproof.py`;
tests in `tests/schema/`; pytest in `requirements-dev.txt`. Run the tests with:
`docker run --rm awg-test sh -c "pip install -q -r requirements-dev.txt && python -m pytest tests -q"`
(after `docker build -t awg-test .`).
Updating the format rules in `src/prompt.py` belongs to the prompt-to-LLM module and is reviewed by its owner.

## 2. LLM providers and models

- Exactly two providers: **Claude** and **Gemini**.
- One thin adapter: `call(provider, system, messages) -> LLMResult`. All provider-specific code stays inside that provider's own file.
- Both providers get their structured-output schema from the same Pydantic model, and every response is validated against it.
- Models: **Claude Haiku 5.5** (`claude-haiku-5-5`) and **Gemini 3.5 Flash** (`gemini-3.5-flash`).
- To keep costs low:
  - temperature 0 and an output-token cap
  - record and replay LLM responses during development
  - a spending limit set in each provider's console
  - prompt caching for the fixed system prompt
- A **pilot run** (10 tasks × 1 repeat, both models) happens before the full evaluation, to estimate cost.

## 3. AgentProof

- Used as the installed package `agentproofx` (pinned in `requirements.txt`). **Never copy its code into this repo.**
- Only `src/verify/baseline/` may import it.

## 4. Professor's suggestions

1. **Separate our contribution from AgentProof's six checks**, in the code (`src/verify/baseline/` vs `src/verify/ours/`) and in reported results. Every finding carries `origin: agentproof | ours`.
2. **Bound the state-space search** (state limit, loop bound, timeout). If a bound is hit, the result is **INCONCLUSIVE, never PASS**. Report verification time as graph size grows. Use the RPST block tree to skip the search where possible: sequence blocks, simple branch blocks and loop blocks are decided by pattern, so only rigid blocks need a search, each on its own subgraph.
3. **Bernstein's conditions also cover tool side effects**: catalog resources (e.g. `gmail:inbox`) as well as state values. When a resource name depends on an argument known only at run time, assume a conflict.
4. **Measure real execution**: time, tokens and task accuracy per node and per run. Fewer nodes does not mean faster.
5. **"Best version" = Pareto selection** over accuracy, cost and latency (see section 6).
6. **Consistency across LLMs**: the same verify + repair pipeline is evaluated on both Claude and Gemini.
7. **Evaluation conditions**:
   - **A**: LLM only
   - **B**: A + verification and repair
   - **C**: B + optimisation

   All three run on both LLMs.

## 5. Verification results and repair

- Possible results: **PASS**, **FAIL**, **INCONCLUSIVE**.
- **INCONCLUSIVE** (a search bound was hit): the workflow is exported **with a warning**. It is reported as **its own category** and is **not** sent to repair.
- **FAIL**: the witness trace goes back to the LLM as a repair instruction.
- **At most 3 repair rounds.** After that the run is recorded as failed.

## 6. Choosing the best version

- Keep the Pareto front over (accuracy ↑, cost ↓, latency ↓).
- Pick from the front by **accuracy, then cost, then latency**. This order is **configurable**.
- All versions and their metrics are stored in **SQLite**.

## 7. Mock tools

- Office domain: **Gmail, Sheets, Calendar**.
- Mock tools behave the same way every run and work on fixed fake data (inbox, sheets, calendar).
- Mock latencies are based on **a few real measured API calls** (Step 6 timing script, run by a teammate with a test Google account). **The results must state this assumption.**

## 8. Benchmark and statistics

- About 40–60 tasks, spread over these categories: linear, choice (XOR), parallel opportunity, loop, needs human approval, ambiguous/trap.
- Each task has a prompt, allowed tools, starting data, and an **independent oracle**: required tools, required orderings, forbidden tools, human step where needed, expected final data. Our own checks are never used to score accuracy, because condition B repairs against them, which would make the score circular.
- 3 repeats per task × 3 conditions × 2 LLMs.
- How results are analysed:
  - The **task** is the unit of analysis. Each task's success rate is taken over its 3 repeats.
  - B vs A and C vs B on each LLM: a **paired permutation test** on per-task success rates, with **task-resampling bootstrap 95% confidence intervals**.
  - The two-LLM question: a **mixed-effects logistic regression**, `success ~ condition × LLM + (1 | task)`.
  - **pass^3** (all 3 repeats succeed) as the consistency measure.
  - **Holm correction** across comparisons.
  - Latency and tokens: per-task means, the same paired test, reported as ratios.

## 9. Code layout

| Module | Folders |
|---|---|
| prompt-to-LLM | `src/llm/`, `src/generation/`, `src/cli/` |
| evaluator | `src/verify/baseline/`, `src/verify/ours/`, `src/verify/report.py`, repair-message formatting |
| improver | `src/refactor/`, `src/runtime/` (LangGraph export, executor), `src/store/` (SQLite, Pareto) |
| shared | `src/schema/` (graph format), `eval/` (tasks, mocks, oracles, runner) |

## 10. Branches

- `main`: always working and demo-ready.
- `plan`: integration branch for building the full plan, created from `main`.
- Feature branches: short-lived, created from `plan` (e.g. `eval/dataflow-check`) and merged back once tested.
- `plan` is merged into `main` at each milestone.
