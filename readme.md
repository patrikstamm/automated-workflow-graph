# Automated Workflow Graph

An LLM (Claude or Gemini) designs a workflow graph for an office task. We verify the graph before anything
runs (AgentProof's checks plus our own) and send any fault back to the LLM to repair, for up to 3 rounds.

```
task → LLM draft (JSON) → format check → normalize → verify ──PASS──► (later: improve → best version → LangGraph)
                                                       │
                                                      FAIL → repair message → LLM fixes it (max 3 rounds)
```

All team decisions (graph format, providers, verdicts, evaluation) are in [DECISIONS.md](DECISIONS.md).
Read it before changing anything shared.

---

## 1. What you need

- **Git**
- **Docker Desktop**, running. Everything runs in Docker, so you do **not** need Python installed locally.
- **A Gemini API key** for live runs (Claude key optional). Tests need **no** key.

## 2. Get the code

```bash
git clone https://github.com/patrikstamm/automated-workflow-graph.git
cd automated-workflow-graph
git checkout plan          # integration branch with the latest work
```

## 3. Add your API keys

Copy the template to `.env` and fill in your own keys:

```bash
cp .env.example .env                 # macOS / Linux / Git Bash
```
```powershell
Copy-Item .env.example .env          # Windows PowerShell
```

Then open `.env` and fill in:

```
GEMINI_API_KEY=your-gemini-key
ANTHROPIC_API_KEY=your-claude-key   # optional, only for --provider claude
```

- `.env` is in `.gitignore`. **Never commit it, and never put keys in `.env.example`** (that file is tracked).
- `docker compose` needs a `.env` file to exist, even if the keys are empty.
- Optional settings in `.env`:
  - `GEMINI_MODEL` / `CLAUDE_MODEL`: override the default models (`gemini-3.5-flash`, `claude-haiku-5-5`)
  - `LLM_CACHE=off | record | replay`: save LLM answers to `.llm_cache/` and reuse them without paying again

## 4. Build the Docker image

Run this from the project folder (where the `Dockerfile` is), once and again after code changes:

```bash
docker build -q -t awg-test .
```

If you see `failed to read dockerfile: open Dockerfile: no such file or directory`, you are in the wrong
folder: `cd` into the project first.

## 5. Run the tests (no API key, no cost)

```bash
docker run --rm awg-test sh -c "pip install -q -r requirements-dev.txt && python -m pytest tests -q"
```

It should end with `181 passed` (or more). The `pip install` installs pytest **inside the container**;
nothing is installed on your computer.

Useful variations (replace the part after `pytest`):

| Command | Runs |
|---|---|
| `python -m pytest tests -v` | all tests, one line each |
| `python -m pytest tests/verify/test_soundness.py -v` | one file |
| `python -m pytest tests -v -k inconclusive` | only tests whose name contains a word |

What the test folders cover:

| Folder / file | Covers |
|---|---|
| `tests/schema/` | the graph format, format errors, normalization |
| `tests/llm/` | the Claude/Gemini adapters and the record/replay cache, with fake clients (no API calls) |
| `tests/verify/test_verify.py` | AgentProof's checks, merged findings, repair messages |
| `tests/verify/test_data_flow.py` | our data-flow check |
| `tests/verify/test_soundness.py` | our soundness check (deadlock, lack of synchronisation, search bounds) |
| `tests/verify/test_parallel.py` | our parallel-conflicts check (Bernstein, tool resources) |
| `tests/verify/test_order_rules.py` | our exact order-rule check vs AgentProof |
| `tests/pipeline/` | conditions A and B, the repair loop, run statuses |

## 6. Run the app (uses your API key, costs tokens)

```bash
docker compose run --rm langgraph-app python main.py
```

It asks `Your task:`. Type a task, e.g. *"Summarise my unread emails and send the summary to my team after I
approve it"*, and press Enter. For each round you see the verdict, tokens and time, the repair message, then
the final workflow JSON and the findings. Type `quit` to exit.

Options (add after `main.py`):

| Option | Effect |
|---|---|
| `--provider claude` | use Claude instead of Gemini (needs `ANTHROPIC_API_KEY`) |
| `--no-repair` | condition A: one LLM call, no repair |
| `--sensitive gmail_send` | that tool needs a human approval step before it |
| `--require-human` | the workflow must contain a human step |
| `--input email` | a value that is available at the start |

The app only **designs and checks** workflows; nothing is executed. The tools in `src/catalog.py` (Gmail, Sheets,
Calendar, web search) are names and parameters only. A run can take up to about 2 minutes.

To check that your keys work (two small real calls):

```bash
docker compose run --rm langgraph-app python scripts/smoke_llm.py
```

## 7. Where things are

| Folder | Contents | Owner |
|---|---|---|
| `src/schema/` | graph format: draft (what the LLM writes), format check, normalized graph | shared |
| `src/llm/`, `src/generation/`, `src/prompt.py` | Claude/Gemini calls, prompt, generating and repairing drafts | patrick (prompt-to-LLM) |
| `src/verify/baseline/` | AgentProof's checks, unchanged (the only place that imports `agentproof`) | om (evaluator) |
| `src/verify/ours/` | our checks: data flow, soundness, parallel conflicts, exact order rules | om (evaluator) |
| `src/verify/report.py`, `repair.py` | combined report, verdict, repair message | om (evaluator) |
| `src/pipeline.py` | conditions A and B, the repair loop (`MAX_REPAIR_ROUNDS = 3`) | shared |
| `src/catalog.py` | the tools the LLM may use, and what each tool reads/writes | shared |
| improver (refactoring, Pareto, LangGraph export) | not built yet | march |

## 8. Troubleshooting

| Problem | Fix |
|---|---|
| `open Dockerfile: no such file or directory` | `cd` into the project folder first |
| `env file .env not found` | create it: `cp .env.example .env` (step 3) |
| Docker command not found / cannot connect | start Docker Desktop and wait until it says it is running |
| `LLM call failed: ... API key` | check the key in `.env`; run `scripts/smoke_llm.py` |
| Changed code but nothing changed in the tests | rebuild the image: `docker build -q -t awg-test .` |
| A pip warning about "running as root" | normal inside Docker, ignore it |
