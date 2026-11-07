# Technology stack and assumptions

What nosh uses, why each tool is there, what we rejected, and what we assume about the machine.
Decision IDs (D#) point to `docs/decisions.md`. Update this file whenever a dependency is added,
removed or changes role.

## In use

| Tool | Role in nosh | Where | Why this one | Rejected / alternatives | Decision |
|---|---|---|---|---|---|
| Python 3.11.9 | Runtime | `.python-version`, `pyproject.toml` (`>=3.11.9`) | Broad wheel support on grader machines | 3.14 (originally pinned; wheel risk) | D16 |
| uv | Env, deps, lockfile, `uv run nosh ...` | `pyproject.toml`, `uv.lock` | Reproducible install in one command; replaces Docker for this app | pip + venv (no lock), Docker (D12) | D12 |
| hatchling | Build backend so `nosh` is an installed script | `pyproject.toml` | uv default, zero config | setuptools | - |
| DuckDB | Embedded analytical database: recipes + 1.27M ingredient lines; all stats, filters, rankings | `data/nosh.duckdb`, `src/nosh/data/build.py`, `store.py` | Columnar engine built for scans/aggregates over the full set; `median()`, `quantile_cont()`, list aggregates built in; single file, no server | SQLite (works too, see below); Postgres (needs a server) | D2 |
| DuckDB `fts` extension | BM25 title search, coverage probe | built in `nosh build` | Same engine as the data; no extra service | SQLite FTS5, rank_bm25, Tantivy | D2, D19 |
| rapidfuzz | Fuzzy title matching, typo tolerance, fallback if `fts` is unavailable | `tasks/search.py` | Fast C implementation, tiny | difflib (slow), embeddings (D14) | D14 |
| pydantic | Typed boundaries: LLM outputs (Slots, AdaptedRecipe), SessionState, actions | `router/slots.py`, `session.py`, `tasks/substitute.py` | Validates LLM JSON; both LLM SDKs accept pydantic schemas for structured output | Hand-written dict checks | D3 |
| google-genai | Default LLM provider (Gemini free tier) | `llm.py` | Named in the assignment; free for graders | - | D8 |
| anthropic | Optional LLM provider (Claude Haiku) | `llm.py` | Swap via `NOSH_LLM_PROVIDER` | - | D8 |
| python-dotenv | Loads API keys from `.env` | `config.py` | Standard, tiny | Manual env setup | - |
| PyYAML | Reads `evals/cases.yaml` | `evals/run.py` | Eval cases are easier to write by hand in YAML than JSON | JSON | D13 |
| Streamlit | Web UI: chat + sidebar with mode and active filters | `ui/app.py` | Chat widgets + sidebar + dataframes out of the box; state visible to graders | Gradio (fine too; less layout control) | D18 |

## Considered and not used

| Tool | Why not | Decision |
|---|---|---|
| SQLite | Would work at this scale (stdlib, offline FTS5). DuckDB chosen because the stats SQL is shorter and clearer (built-in median/quantiles) and aggregates are faster. Not worth switching after step 2 | D2 |
| LanceDB / FAISS / embeddings | Vector similarity only; can't do the joins and GROUP BYs stats need. Vector search is optional and off (graders would need a model download and minutes of embedding). If added later, it sits next to DuckDB | D6, D14 |
| LangChain / LlamaIndex / LangGraph | The session memory needed is a small typed state object, not a chat buffer; frameworks add magic that is hard to modify live in the interview | D3 |
| mem0 / Hindsight / Cognee | Long-term cross-session memory; the assignment needs in-session state | D4 |
| Graph database | Recipes link through ingredients; the `recipe_ingredients` table is already the graph | D5 |
| Docker | Local single-user app with a file DB; `uv.lock` already pins the environment | D12 |
| Agent / tool-calling router | Non-reproducible routing, can drop hard constraints; slots + pure `decide()` instead | D11 |

## Assumptions about the grader's machine

- Windows, macOS or Linux with uv installed; Python 3.11.9 is fetched by uv if missing.
- Network access: once for the dataset download and the DuckDB `fts` extension (first `nosh build`), and
  on every chat turn for the LLM API.
- A Gemini API key (free tier) or an Anthropic key in `.env`. Without a key, `nosh build` works but chat does not.
- About 200 MB for the raw dataset plus the DuckDB file; first build about 2 minutes, later builds under 1 minute
  (measured on the author's laptop: 105 s first, 43 s second).
- No GPU needed.

## How to explain the core choice in one sentence

"The questions are mostly exact operations over the whole dataset (counts, filters, rankings), so the core is an
embedded analytical database; the LLM only turns language into structured requests and writes the final text."
