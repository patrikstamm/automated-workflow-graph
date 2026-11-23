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
- One thin adapter in `src/llm/`: `call(provider, system, messages, schema=None) -> LLMResult`.
  All provider-specific code stays inside that provider's own file (`claude.py`, `gemini.py`).
  `src/providers.py` only re-exports it so older imports keep working.
- Both providers get the **same** JSON schema, built from the `WorkflowDraft` Pydantic model
  (`src/llm/schema.py`, with `default`/`title` removed because Gemini does not support them),
  and every response is validated with `load_draft`.
  - Claude: `output_config={"format": {"type": "json_schema", "schema": ...}}`
  - Gemini: `response_json_schema=...` (full JSON Schema; not the older `response_schema`)
- Models: **Claude Haiku 5.5** (`claude-haiku-5-5`) and **Gemini 3.5 Flash** (`gemini-3.5-flash`),
  overridable with `CLAUDE_MODEL` / `GEMINI_MODEL` in `.env`. Keys go in `.env` (template: `.env.example`).
- **Both providers run at their default temperature.** Claude's Messages API (anthropic 2.12) has no
  temperature setting, so setting Gemini to 0 would make the consistency comparison unfair.
- Token counts are normalised: `input_tokens` includes cached prompt tokens; `output_tokens` is the visible
  answer; `thinking_tokens` is counted separately (billed output = output + thinking).
- Gemini's thinking is left at its default for now; thinking tokens are logged and a limit is decided after the pilot.
- To keep costs low:
  - an output-token cap (4000)
  - record and replay LLM answers during development: `LLM_CACHE=off | record | replay`, saved in `.llm_cache/`
  - a spending limit set in each provider's console
  - prompt caching for the fixed system prompt (not enabled yet; decided after the pilot)
- A live check (`scripts/smoke_llm.py`) makes one real call per provider to confirm both accept the schema.
- A **pilot run** (10 tasks × 1 repeat, both models) happens before the full evaluation, to estimate cost.

## 3. AgentProof

- Used as the installed package `agentproofx` (pinned in `requirements.txt`). **Never copy its code into this repo.**
- Only `src/verify/baseline/` may import it.

## 4. Professor's suggestions

1. **Separate our contribution from AgentProof's six checks**, in the code (`src/verify/baseline/` vs `src/verify/ours/`) and in reported results. Every finding carries `origin: agentproof | ours`.
2. **Bound the state-space search** (state limit, loop bound, timeout). If a bound is hit, the result is **INCONCLUSIVE, never PASS**. Report verification time as graph size grows. Use the RPST block tree to skip the search where possible: sequence blocks, simple branch blocks and loop blocks are decided by pattern, so only rigid blocks need a search, each on its own subgraph.
   - **How we do it (differs from the slides):** we do not build the RPST. The soundness check uses SESE fragment
     reduction (Vanhatalo, Völzer & Leymann, ICSOC 2007): sequence, branch and loop fragments are decided by
     pattern and collapsed into one edge, innermost first; only what is left is searched. What is left is
     searched as one subgraph, so a simple fragment that contains a rigid one is searched together with it,
     not separately. The RPST is built later for refactoring (Step 7).
   - Bounds: `max_states` (default 10,000) and `timeout_s` (default 2 s). There is **no loop bound**: a second
     token on an edge is reported as a fault and not explored further, so the number of states is finite.
3. **Bernstein's conditions also cover tool side effects**: catalog resources (e.g. `gmail:inbox`) as well as state values. When a resource name depends on an argument known only at run time, assume a conflict.
4. **Measure real execution**: time, tokens and task accuracy per node and per run. Fewer nodes does not mean faster.
5. **"Best version" = Pareto selection** over accuracy, cost and latency (see section 6).
6. **Consistency across LLMs**: the same verify + repair pipeline is evaluated on both Claude and Gemini.
7. **Evaluation conditions**:
   - **A**: LLM only — exactly one call (`generate_draft`); an answer with format errors counts as a failed task
   - **B**: A + verification and repair (format-error retries belong here, within the 3 repair rounds)
   - **C**: B + optimisation

   All three run on both LLMs.

## 5. Verification results and repair

- Possible results: **PASS**, **FAIL**, **INCONCLUSIVE**.
- **INCONCLUSIVE**: the workflow is exported **with a warning**. It is reported as **its own category** and is **not** sent to repair. Causes:
  - a search bound was hit (our soundness or order-rule check: `max_states` or `timeout_s`)
  - a parallel conflict that exists only through an argument known at run time (e.g. `"$sheet"` against `"Budget"`)
- **FAIL**: the witness trace goes back to the LLM as a repair instruction.
- **At most 3 repair rounds.** After that the run is recorded as failed.
- Overall verdict: FAIL if any finding fails, otherwise INCONCLUSIVE if any is inconclusive, otherwise PASS.
- **Order rules: our exact check decides** (section 5.3). AgentProof's result for the same rule stays in the
  raw data unchanged, but gives no finding and does not count towards the verdict or repair.
- The report also records **`agentproof_verdict`**: the verdict from AgentProof's checks alone, with AgentProof's
  own order-rule results (suggestion 1). There, an AgentProof order-rule violation whose path goes through an
  AND-split is INCONCLUSIVE: AgentProof checks parallel branches as if only one of them runs, so it may be a
  false alarm.

### 5.1 Evaluator (`src/verify/`)

- `verify_graph(graph, VerifyConfig)` → `VerificationReport`. Settings are passed **per run**
  (each benchmark task sets its own); defaults are off:
  - `require_human`: the workflow must contain a human step
  - `sensitive_tools`: tool names that need a human step before them (AgentProof's human-gate coverage)
  - `order_rules`: AgentProof temporal DSL, e.g. `tool:deploy -> F tool:approve`. An invalid rule is a configuration error.
- `src/verify/baseline/agentproof.py` runs AgentProof's checks unchanged and is the only file in `src/` that
  imports `agentproof` (enforced by a test). Our own checks go in `src/verify/ours/` (Step 5).
- The report keeps every check's **raw** output unchanged (`CheckResult.raw`), so results can be reported per origin.
  **Findings** are our interpretation: one per fault, in the LLM's own node ids, tagged `origin`.
- Duplicate findings are merged: a step that cannot reach an end step only because it leads into an already
  reported dead end is listed under that dead end as "also affected". The remaining `reverse_reachability`
  findings are real endless loops, one finding per connected group of steps.
- Timings are recorded per check group (`agentproof.structural`, `agentproof.order_rule:<id>`, `ours.<check_id>`,
  `ours.order_rule:<id>`) and summed per origin.
- `repair_message(format_errors, report)` builds the text for the LLM: format errors and FAIL findings with
  their paths. INCONCLUSIVE findings are left out.
- Known AgentProof limit: its static order-rule check does **not** flag an obligation still open when the run
  ends (`deploy → end` passes `deploy -> F approve`). Our exact order-rule check covers this (section 5.3).

### 5.2 Repair loop (`src/pipeline.py`)

- `run_condition_a()`: one call (`generate_draft`); format errors or a FAIL verdict make the run FAILED.
- `run_condition_b()`: the first call, then up to **3 repair rounds**, so **at most 4 LLM calls** per run.
  Format errors and verification faults share this one budget.
- Each repair round sends **only the latest draft**: the task, the LLM's previous answer, and the repair
  message (`repair_draft`). The cost per round stays roughly flat.
- The loop stops with one of these run statuses:

  | Status | When |
  |---|---|
  | `PASS` | a round passes verification |
  | `INCONCLUSIVE` | a round is inconclusive (exported with a warning, not repaired) |
  | `FAILED` | still format errors or FAIL after round 3 (condition A: after its one call) |
  | `NO_PROGRESS` | the LLM returned the same answer as the round before (compared as JSON); counted as a failure |
  | `BLOCKED` | the provider blocked or refused the request; not retried |

- An answer cut off at the token limit is a format error, and the repair message asks for a shorter workflow.
- Every round is recorded (tokens, time, stop reason, format errors, report, draft, repair message) in
  `RunResult`, ready to be stored in SQLite later. `RunResult.totals()` sums tokens, LLM time and verification time.
- CLI: condition B is the default; `--no-repair` runs condition A. `--require-human` and `--sensitive TOOL`
  set the verification settings.
- The Gmail, Sheets and Calendar tools in `src/catalog.py` are enabled as stubs: generation and verification
  only read their names and parameters; they are not executed until mock tools exist (section 7).

### 5.3 Our checks (`src/verify/ours/`, origin `ours`)

Built in five sub-steps: 5.1 data flow, 5.2 soundness, 5.3 parallel conflicts, 5.4 exact order rules,
5.5 scalability benchmark. They never import `agentproof`; `run_ours()` times each check (`ours.<check_id>`).

**Data flow (`data_flow.py`)**: every value a step reads must be written before it, on every path that reaches it.
- A forward analysis on the normalized graph: an XOR-join keeps the values written on **every** incoming
  branch, an AND-join keeps the values written on **any** branch, and loops are iterated until nothing changes.
  A value written in a parallel sibling branch only counts after the join.
- `VerifyConfig.inputs` names values that are available at the start (e.g. `email`); every other value must
  be written by a step. CLI: `--input VALUE`.
- One FAIL finding per step and missing value, with a path on which the value is never written.
  Steps that cannot be reached from the start are skipped (AgentProof reports those).

**Soundness (`soundness.py`)**: no deadlock and no lack of synchronisation (Vanhatalo, Völzer & Leymann, ICSOC 2007).
- Token game on the normalized graph: an AND-join waits for all incoming branches, an XOR-join for any one;
  a router or branching human chooses one outgoing edge, every other step uses all of them. End steps take the
  token away; the run is finished when no token is left, so several end steps are allowed.
- **Lack of synchronisation**: an edge gets a second token, so a step runs twice at the same time
  (e.g. parallel branches into `"join": "any"`). **Deadlock**: no step can run but tokens are left
  (e.g. a router's branches into `"join": "all"`).
- Phase 1, patterns: sequence, branch and loop fragments are collapsed. A branch fragment with AND-split +
  XOR-join is lack of synchronisation, XOR-split + AND-join is deadlock; an XOR loop is sound.
- Phase 2, search: whatever is left is searched breadth-first over token placements, with the collapsed
  fragments as single edges, within `max_states` and `timeout_s` (section 4, suggestion 2). Hitting a bound
  gives an INCONCLUSIVE finding (not repaired); faults found before the bound still FAIL.
- The raw result records how many fragments were decided by each pattern, whether a search was needed, the
  size of the searched part and the number of states, for the scalability benchmark (5.5).
- The witness is the firing order up to the faulty step, in the LLM's node ids. A step with no outgoing edge
  takes the token away like an end step, so dead ends are reported only by AgentProof.

**Parallel conflicts (`parallel.py`, rules in `bernstein.py`)**: steps that run at the same time must satisfy
Bernstein's conditions (Bernstein 1966): neither writes what the other reads or writes. Read/read is fine.
- **Which steps are parallel** (structural rule): different branches of the same AND-split reach them, and
  neither can reach the other. Loop edges are left out, so a loop around a parallel block does not hide it.
  Exact for sound graphs; on a graph that fails soundness the result is best effort.
- **State values**: from each step's `reads`/`writes`. A step that reads a value written in a parallel branch
  is reported by data flow when the value is not written before the split; this check reports it only when
  the value **is** written before (the parallel branch overwrites it). Each fault reaches the LLM once.
- **Tool resources** (section 4, suggestion 3): from `RESOURCES` in `src/catalog.py`, e.g. `gmail_label` writes
  `gmail:inbox`, `gmail_search` reads it. `{sheet}` is filled in from the step's argument.
  **Strict Bernstein**: two parallel writes to an append-only resource (two `gmail_send`, two `sheets_append` to
  the same sheet, two `calendar_create`) are a conflict too, because their order is unpredictable.
  Tools not in `RESOURCES` have no resource effects.
- An argument known only at run time (`"$sheet"`) matches any name. Such a conflict is **INCONCLUSIVE**
  (exported with a warning, not repaired); the improver still treats it as a conflict and does not parallelise.
- One finding per pair of steps; the path goes from the start to the split, down to the first step, then
  down to the second.
- `bernstein.py` (`effects`, `conflicts`) and `parallel_pairs()` are shared with the improver (Step 7), so
  verification and refactoring use the same rule.

**Exact order rules (`order_rules.py`)**: the same rules as `VerifyConfig.order_rules`, checked exactly.
- **Same rule language and automata as AgentProof** (`G !a`, `a -> F b`, `a -> F[<=k] b`, chains `a -> F b -> F c`,
  `a U b`, `(x) AND (y)`, `(x) OR (y)`; atoms `tool:<name>`, `action:<kind>`, `decision:<x>` and the tags `tool`,
  `llm_step`, `human`, `router`). A task writes its rules once and both checkers run them. We parse the rules
  ourselves (section 3). As in AgentProof, `a -> F b` also fails if a second `a` comes before `b`.
- **Finished runs**: a run is finished when no token is left (the token game of the soundness check, shared in
  `tokens.py`). A rule still waiting at that point is a violation. Only finished runs are checked; a loop that
  can never be left is AgentProof's livelock finding.
- **Parallel branches** run in every interleaving, on the full graph (not the collapsed one: order matters).
- Same bounds as soundness (`max_states`, `timeout_s`); hitting one gives an INCONCLUSIVE finding (not repaired).
- **Differences from AgentProof, on purpose:**
  - Only the LLM's own steps (`llm`, `tool`, `human`, `router`) are events. Start, end and the inserted
    gateways are not, so they do not count towards `k` in `F[<=k]`. **Comparison limit:** on our normalized graph,
    AgentProof counts the gateways (and start/end) as steps, so its `k` is off and `action:entry` matches for it.
  - `decision:<x>` matches a router or human step that takes the branch with condition `x`. In AgentProof's
    static check it never matches, so a rule using it always passes there.
  - `a U (b U c)` is checked as written. AgentProof reads `(b U c)` as a single atom that never matches.
- One finding per rule: the shortest violating run, as the steps in firing order up to the step that breaks the
  rule, or up to the end step for a rule still open when the run ends.

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
