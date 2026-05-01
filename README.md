# NL2SQL Data Agent

A console application that accepts natural language questions and answers them by generating and executing SQL queries against an SQLite employee database.

---

## Assumptions
- `employees.db` is committed to the repository intentionally so the evaluator can run the application without any database setup. This would not be appropriate in a production environment.
- Questions are assumed to be in English.
- Modification requests (update, delete, insert) are treated as out of scope. The spec frames the application as a question-answering and lookup tool, and all example queries are reads. DML (except SELECT) operations are rejected with a plain-language message.
- Conversation history is preserved for the duration of a session — follow-up questions and pronoun references ("those employees", "that person") resolve correctly within the same run. History resets when the application is restarted; there is no cross-session persistence.
- Numeric results are rounded to 2 decimal places. Raw float values from SQLite (e.g. `143984.82333...`) are normalised by a Pydantic result model before display.
- The LLM is instructed to add `LIMIT 25` to every query by default. Results are capped at 200 rows as a hard backstop. Users can ask for more rows explicitly (e.g. "show me all") and the LLM will omit the limit accordingly.
- The ReAct loop attempts up to 3 Thought/Action/Observation steps per question. If a working query cannot be produced within 3 steps the question is declined with a plain-language message.
- The guardrail is enforced at two levels: the LLM is instructed via the system prompt to always include a department filter, and a post-generation check validates every branch of the generated SQL before it reaches the database. The application does not implement row-level security in the database itself.


---

## Setup

**Requirements:** Python 3.10+

### 1. Create a virtual environment

**macOS / Linux**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

**Windows PowerShell**
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

**Windows CMD**
```cmd
python -m venv .venv
.venv\Scripts\activate.bat
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

The app supports two LLM providers, selected via `LLM_PROVIDER`:

| Provider | Env var | Model used |
|---|---|---|
| `anthropic`  | `ANTHROPIC_API_KEY` | `claude-sonnet-4-6` |
| `gemini` (default) | `GEMINI_API_KEY` | `gemini-3.1-pro-preview` |


You only need the key for the provider you intend to use.

### Setting API keys (recommended: terminal environment variables)

Setting keys directly in your terminal session is the most secure option — they live only in memory for that session and are never written to disk, so no file can expose them.

**macOS / Linux**
```bash
# Anthropic
export ANTHROPIC_API_KEY=sk-ant-...
export LLM_PROVIDER=anthropic

# Gemini
export GEMINI_API_KEY=AIza...
export LLM_PROVIDER=gemini
```

**Windows PowerShell**
```powershell
# Anthropic
$env:ANTHROPIC_API_KEY = "sk-ant-..."
$env:LLM_PROVIDER = "anthropic"

# Gemini
$env:GEMINI_API_KEY = "AIza..."
$env:LLM_PROVIDER = "gemini"
```

**Windows CMD**
```cmd
set ANTHROPIC_API_KEY=sk-ant-...
set LLM_PROVIDER=anthropic
```

> **Alternative:** You can also copy `.env.example` to `.env` and fill in your keys there. The app will load it automatically via `python-dotenv`. Note that `.env` files are stored on disk — keep them out of version control (already covered by `.gitignore`).

### 3. Query log

The app writes to `logs/queries.log` automatically on first run. Every question, generated SQL, and outcome (OK, REPAIRED, REACT_RETRY, BLOCKED) is recorded there with a timestamp. Nothing internal is printed to the console — the log is the only place the generated SQL appears.

---

## Running

```bash
python src/main.py
```

Example session:

```
[INFO] Provider: anthropic
[INFO] Department selected: Engineering
[INFO] All queries are scoped to the 'Engineering' department.
[INFO] Type a question, or 'exit' / 'quit' to stop.

Question> Who are the software engineers?
+----------------+-------------------+---------------------+
| Name           | Role              | EmploymentStartDate |
+----------------+-------------------+---------------------+
| James Nguyen   | Software Engineer | 2021-03-15          |
| Priya Sharma   | Software Engineer | 2022-07-01          |
+----------------+-------------------+---------------------+
  2 rows

Question> which of those started after 2022?
+---------------+-------------------+---------------------+
| Name          | Role              | EmploymentStartDate |
+---------------+-------------------+---------------------+
| Priya Sharma  | Software Engineer | 2022-07-01          |
+---------------+-------------------+---------------------+
  1 row

Question> can you update Helen's salary?
  This application can only look up information, not make changes to employee records.

Question> exit
[INFO] Goodbye.
```

---

## Architecture

```
NL2SQL/
├── data/
│   └── employees.db          — SQLite database
├── logs/
│   └── queries.log           — generated at runtime
├── src/
│   └── main.py               — entire application
├── .env.example
├── requirements.txt
└── README.md
```

`src/main.py` internals:
```
 ├── startup                  — randomly selects department, prints [INFO] log
 ├── build_system_prompt()    — injects schema + department guardrail + ReAct format rules
 ├── query loop
 │    ├── user types natural language question
 │    └── _run_react_loop()   — ReAct agent: up to 3 Thought/Action/Observation steps
 │         ├── _generate_sql()     — sends history + turn to LLM, returns raw response
 │         ├── signal handling     — INVALID_QUERY / NEEDS_CLARIFICATION raised as exceptions
 │         ├── _extract_action()   — parses SQL from Action: label in LLM response
 │         ├── _is_select_only()   — blocks non-SELECT, multi-statement, disallowed keywords
 │         ├── _guardrail_passes() — checks every UNION/INTERSECT/EXCEPT branch independently
 │         ├── _execute()          — runs query against employees.db, returns rows
 │         └── QueryResult         — Pydantic model: rounds floats, wraps columns + rows
 └── _format_table()          — renders results as an ASCII table
```

All queries and outcomes (OK, REPAIRED, REACT_RETRY, BLOCKED) are written to `logs/queries.log` with timestamp, department, step count, user question, and generated SQL. Nothing internal is printed to the user.

### Department guardrail — two layers

1. **System prompt (primary):** The LLM is instructed before every query that it must include `WHERE Employee.Department = '<selected>'`. The department is chosen at startup and fixed for the session.

2. **Post-generation validation (defence-in-depth):** `_guardrail_passes()` splits the SQL on `UNION`, `INTERSECT`, and `EXCEPT` and checks every branch independently for the department filter. A valid first branch cannot mask an unfiltered second branch. If any branch fails, the query is refused and fed back to the LLM as an observation for self-correction.

### Design decisions

| Decision | Rationale |
|---|---|
| Single Python file | Easy to read, run, and demo; no unnecessary abstraction for a focused task |
| ReAct loop (Thought/Action/Observation) | Lets the agent reason about errors and self-correct across up to 3 steps without external frameworks; works with both providers |
| Anthropic + Gemini (no LangChain) | Direct SDK calls keep the ReAct loop and guardrail checks fully visible with no added dependency weight |
| System prompt + runtime guardrail | Defence-in-depth: LLM enforces the filter, runtime check is the hard safety net that cannot be prompt-injected away |
| Per-branch UNION/INTERSECT/EXCEPT check | A valid first SELECT branch cannot mask an unfiltered second branch in a set operation |
| `_is_select_only()` check | Blocks non-SELECT, multi-statement payloads, and disallowed DDL/DML keywords before they reach the database |
| `INVALID_QUERY` / `NEEDS_CLARIFICATION` signals | Clean protocol for the LLM to decline unanswerable or vague questions without attempting bad SQL |
| Conversation history (session-scoped) | Each turn carries prior questions and generated SQL so follow-up questions and pronouns resolve correctly within a session |
| Pydantic result model | Rounds floats to 2 decimal places consistently — formatting rule lives in code, not in the SQL or system prompt |
| Friendly user messages | Internal errors and blocked queries are logged to file; the user only sees plain-language responses |
| stdlib-only table output | No `tabulate` or `rich` dependency — less setup friction for the evaluator |

---

## AI tooling used

**Claude Code (Anthropic CLI)** was used throughout development:

- Drafted the initial `main.py` structure and system prompt
- Iterated on edge case handling (empty results, SQL errors, markdown fence stripping)
- Tested guardrail robustness with adversarial prompts
