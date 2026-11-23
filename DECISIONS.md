# Project Decisions

Decisions the team has agreed on. Change this file through a reviewed commit, not silently.
Last updated: 2026-11-23.

## Open items

| Item | Status |
|---|---|
| Final deadline | TBD |
| Checkpoints | TBD |
| Owner: prompt-to-LLM | TBD |
| Owner: evaluator | Okkaushik |
| Owner: improver | TBD |

## 1. Graph format

- Our workflow format is a **superset of AgentProof's dict format** (`agentproof.graph.model.graph_from_dict`).
- On top of it, each node carries `metadata` with:
  - gateway logic: `split` or `join`, and `AND` / `XOR` / `OR`
  - `reads` and `writes` (state values)
  - `args` (tool parameters; `"$name"` refers to a state value)
- The format is defined once, as a Pydantic model (`WorkflowGraph`). It is the contract between all three modules.
- A **normalize** step inserts explicit gateway nodes wherever a node has more than one edge in or out, so the graph meets Vanhatalo et al.'s workflow-graph rules.
- Mapping to AgentProof: XOR-splits become `router` nodes with `conditional` edges; AND gateways become `passthrough` nodes with `parallel` edges.
- Witness traces must be mapped back to the LLM's own node ids before they go into a repair prompt.

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
- Only `verify/baseline/` may import it.

## 4. Professor's requirements

1. **Separate our contribution from AgentProof's six checks**, in the code (`verify/baseline/` vs `verify/ours/`) and in reported results. Every finding carries `origin: agentproof | ours`.
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
| prompt-to-LLM | `llm/`, `generation/`, `cli/` |
| evaluator | `verify/baseline/`, `verify/ours/`, `verify/report.py`, repair-message formatting |
| improver | `refactor/`, `runtime/` (LangGraph export, executor), `store/` (SQLite, Pareto) |
| shared | `schema/` (graph format), `eval/` (tasks, mocks, oracles, runner) |

## 10. Branches and commits

- `main`: always working and demo-ready.
- `plan`: integration branch for building the full plan, created from `main`.
- Feature branches: short-lived, created from `plan` (e.g. `eval/dataflow-check`) and merged back once tested.
- `plan` is merged into `main` at each milestone.
- Commits are made under each team member's own git identity. **No AI attribution lines** in commit messages or PR descriptions.
- Never commit `.env` (it is in `.gitignore` and `.dockerignore`).
