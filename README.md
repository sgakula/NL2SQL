# NL2SQL Data Agent

A console application that accepts natural language questions and answers them by generating and executing SQL queries against an SQLite employee database.

---

## Assumptions

- `employees.db` is located in the `data/` directory relative to the project root.
- The database schema matches the one documented in the spec (Employee, Certification, Benefits).
- Questions are assumed to be in English.
- Modification requests (update, delete, insert) are out of scope — the spec describes read-only lookups and instructs candidates to document assumptions. These are rejected with a plain-language message.
- Conversation history resets on each run; there is no cross-session persistence.
- The guardrail applies at the SQL generation level; the application does not implement row-level security in the database itself.
- `employees.db` is committed to the repository intentionally so the evaluator can run the application without any database setup. This would not be appropriate in a production environment.

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
| `anthropic` (default) | `ANTHROPIC_API_KEY` | `claude-sonnet-4-6` |
| `gemini` | `GEMINI_API_KEY` | `gemini-3.1-pro-preview` |


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

The app writes to `logs/queries.log` automatically on first run. Every question, generated SQL, and outcome (OK, BLOCKED, UNSAFE, EXEC_ERROR) is recorded there with a timestamp. Nothing internal is printed to the console — the log is the only place the generated SQL appears.

---

## Running

```bash
python src/main.py
```

Example session:

```
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
 ├── startup              — randomly selects department, prints [INFO] log
 ├── build_system_prompt()— injects schema + department guardrail + response rules
 ├── query loop
 │    ├── user types natural language question
 │    ├── _generate_sql() — sends full conversation history to LLM, returns raw response
 │    ├── signal handling — INVALID_QUERY / NEEDS_CLARIFICATION detected before SQL parsing
 │    ├── _is_select_only()    — blocks non-SELECT and multi-statement payloads
 │    ├── _guardrail_passes()  — confirms department filter is present in generated SQL
 │    └── _execute()           — runs query against employees.db, returns rows
 └── _format_table()      — renders results as an ASCII table
```

All queries and outcomes (OK, BLOCKED, UNSAFE, EXEC_ERROR) are written to `logs/queries.log` with timestamp, department, user question, and generated SQL. Nothing internal is printed to the user.

### Department guardrail — two layers

1. **System prompt (primary):** The LLM is instructed before every query that it must include `WHERE Employee.Department = '<selected>'`. The department is chosen at startup and fixed for the session.

2. **Post-generation validation (defence-in-depth):** `_guardrail_passes()` checks the generated SQL for both the word `DEPARTMENT` and the selected department value. If either is missing the query is refused outright.

### Design decisions

| Decision | Rationale |
|---|---|
| Single Python file | Easy to read, run, and demo; no unnecessary abstraction for a focused task |
| Anthropic SDK (no LangChain) | Direct API calls keep the guardrail logic explicit rather than buried in framework internals |
| System prompt + runtime check | Defence-in-depth: LLM is responsible for correct SQL, runtime check is the hard safety net |
| `_is_select_only()` check | Blocks any LLM-generated non-SELECT or multi-statement payload before it reaches the database |
| `INVALID_QUERY` / `NEEDS_CLARIFICATION` signals | Clean protocol for the LLM to decline unanswerable or vague questions without attempting bad SQL |
| Conversation history | Each turn carries prior questions and generated SQL so follow-up questions resolve correctly |
| Friendly user messages | Internal errors and blocked queries are logged to file; the user only sees plain-language responses |
| stdlib-only output | No `tabulate` or `rich` dependency — less setup friction for the evaluator |

---

## AI tooling used

**Claude Code (Anthropic CLI)** was used throughout development:

- Explored the `employees.db` schema and sample data via inline Python snippets
- Drafted the initial `main.py` structure and system prompt
- Suggested the two-layer guardrail design (system prompt + runtime SQL check)
- Iterated on edge case handling (empty results, SQL errors, markdown fence stripping)
- Generated this README
