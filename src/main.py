"""
NL2SQL Data Agent
Natural language to SQL console application.
Supports Anthropic Claude and Google Gemini via LLM_PROVIDER env var.
"""

import logging
import os
import random
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import anthropic
from google import genai
from google.genai import types as genai_types
from dotenv import load_dotenv

load_dotenv()

_ROOT = Path(__file__).parent.parent
DB_PATH = _ROOT / "data" / "employees.db"
LOG_PATH = _ROOT / "logs" / "queries.log"

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(message)s",
)
_log = logging.getLogger("nl2sql")
DEPARTMENTS = ["Sales", "Marketing", "Engineering"]

SCHEMA = """
Database: employees.db (SQLite)

Tables:

Employee (
    EmployeeId   INTEGER PRIMARY KEY,
    Name         TEXT    NOT NULL,
    Department   TEXT    NOT NULL,  -- one of: 'Sales', 'Marketing', 'Engineering'
    Role         TEXT    NOT NULL,
    EmploymentStartDate TEXT NOT NULL,  -- format: YYYY-MM-DD
    SalaryAmount REAL    NOT NULL,
    YearlyBonusAmount REAL             -- nullable
)

Certification (
    CertificationId INTEGER PRIMARY KEY,
    EmployeeId      INTEGER NOT NULL REFERENCES Employee(EmployeeId),
    CertificationName TEXT  NOT NULL,
    DateAchieved    TEXT    NOT NULL   -- format: YYYY-MM-DD
)

Benefits (
    BenefitId        INTEGER PRIMARY KEY,
    EmployeeId       INTEGER NOT NULL REFERENCES Employee(EmployeeId),
    BenefitsPackage  TEXT    NOT NULL,
    RemainingBalance REAL    NOT NULL
)
"""


def build_system_prompt(department: str) -> str:
    return f"""You are a SQL query generator for a SQLite employee database.

{SCHEMA}

═══════════════════════════════════════════════════════
DEPARTMENT GUARDRAIL — MANDATORY, NON-NEGOTIABLE
═══════════════════════════════════════════════════════
Active department: {department}

Every SELECT query you produce MUST contain a filter that restricts results
to ONLY the '{department}' department, for example:
  WHERE Employee.Department = '{department}'

This applies even when the user does not mention a department.
Never return rows belonging to any other department.
═══════════════════════════════════════════════════════

Rules:
1. Produce a single, valid SQLite SELECT statement.
2. Always enforce the department filter described above.
3. Return ONLY the raw SQL — no markdown, no code fences, no explanation.
4. If the question cannot be answered with the available schema, or asks for
   a modification (update, delete, insert), respond with exactly:
   INVALID_QUERY: <brief reason in plain language for a non-technical user — no SQL keywords, no jargon>
5. If the question is too vague or ambiguous to produce a correct query
   (e.g. "show me some employees", "find that person"), respond with exactly:
   NEEDS_CLARIFICATION: <one specific question to ask the user in plain language>
"""


def _init_client(provider: str) -> Any:
    """Initialise and return the provider client, or exit with a clear error."""
    if provider == "anthropic":
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise SystemExit(
                "[ERROR] ANTHROPIC_API_KEY is not set.\n"
                "        Add it to your .env file or export it as an env var."
            )
        return anthropic.Anthropic(api_key=api_key)

    if provider == "gemini":
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise SystemExit(
                "[ERROR] GEMINI_API_KEY is not set.\n"
                "        Add it to your .env file or export it as an env var."
            )
        return genai.Client(api_key=api_key)

    raise SystemExit(
        f"[ERROR] Unknown LLM_PROVIDER '{provider}'. "
        "Choose 'anthropic' or 'gemini'."
    )


def _generate_sql(
    question: str,
    system_prompt: str,
    client: Any,
    provider: str,
    history: list[dict],
) -> str:
    """Call the active LLM and return the raw text response."""
    messages = history + [{"role": "user", "content": question}]

    if provider == "anthropic":
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            system=system_prompt,
            messages=messages,
        )
        block = response.content[0]
        return block.text.strip() if block.type == "text" else ""  # type: ignore[union-attr]

    # gemini — map role names and build Content objects
    gemini_contents = [
        genai_types.Content(
            role="model" if m["role"] == "assistant" else "user",
            parts=[genai_types.Part(text=m["content"])],
        )
        for m in messages
    ]
    response = client.models.generate_content(
        model="gemini-3.1-pro-preview",
        contents=gemini_contents,
        config=genai_types.GenerateContentConfig(system_instruction=system_prompt),
    )
    return response.text.strip()


_DISALLOWED_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER)\b",
    re.IGNORECASE,
)


def _is_select_only(sql: str) -> bool:
    """Block anything that isn't a plain SELECT and reject multi-statement payloads."""
    stripped = sql.strip().rstrip(";")
    if not stripped.upper().startswith("SELECT"):
        return False
    if ";" in stripped:
        return False
    if _DISALLOWED_KEYWORDS.search(stripped):
        return False
    return True


def _guardrail_passes(sql: str, department: str) -> bool:
    """
    Secondary safety check: confirm the SQL contains a department filter.
    The system prompt is the primary guardrail; this is a defence-in-depth layer.
    """
    upper = sql.upper()
    return "DEPARTMENT" in upper and department.upper() in upper


def _execute(sql: str) -> tuple[list[str], list[tuple]]:
    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.cursor()
        cursor.execute(sql)
        columns = [d[0] for d in cursor.description] if cursor.description else []
        rows = cursor.fetchall()
        return columns, rows
    finally:
        conn.close()


def _format_table(columns: list[str], rows: list[tuple]) -> str:
    if not rows:
        return "  (no results)"

    str_rows = [
        [str(v) if v is not None else "NULL" for v in row]
        for row in rows
    ]
    widths = [len(c) for c in columns]
    for row in str_rows:
        for i, val in enumerate(row):
            widths[i] = max(widths[i], len(val))

    sep = "+-" + "-+-".join("-" * w for w in widths) + "-+"
    header = "| " + " | ".join(c.ljust(w) for c, w in zip(columns, widths)) + " |"

    lines = [sep, header, sep]
    for row in str_rows:
        lines.append("| " + " | ".join(v.ljust(w) for v, w in zip(row, widths)) + " |")
    lines.append(sep)
    lines.append(f"  {len(rows)} row{'s' if len(rows) != 1 else ''}")
    return "\n".join(lines)


def main() -> None:
    provider = os.environ.get("LLM_PROVIDER", "anthropic").lower()
    client = _init_client(provider)

    department = random.choice(DEPARTMENTS)
    print(f"[INFO] Provider: {provider}")
    print(f"[INFO] Department selected: {department}")
    print(f"[INFO] All queries are scoped to the '{department}' department.")
    print("[INFO] Type a question, or 'exit' / 'quit' to stop.\n")

    system_prompt = build_system_prompt(department)
    history: list[dict] = []

    while True:
        try:
            question = input("Question> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[INFO] Exiting.")
            break

        if not question:
            continue
        if question.lower() in ("exit", "quit"):
            print("[INFO] Goodbye.")
            break

        # ── Generate SQL ──────────────────────────────────────────────────────
        try:
            raw = _generate_sql(question, system_prompt, client, provider, history)
        except Exception as exc:
            print(f"[ERROR] LLM request failed: {exc}\n")
            continue

        # ── Handle unanswerable questions ─────────────────────────────────────
        if raw.upper().startswith("INVALID_QUERY"):
            reason = raw.split(":", 1)[1].strip() if ":" in raw else raw
            print(f"  {reason}\n")
            continue

        # ── Ask for clarification when the question is too vague ──────────────
        if raw.upper().startswith("NEEDS_CLARIFICATION"):
            clarification_q = raw.split(":", 1)[1].strip() if ":" in raw else raw
            print(f"  {clarification_q}\n")
            history.append({"role": "user", "content": question})
            history.append({"role": "assistant", "content": raw})
            continue

        # Strip any accidental markdown fences the model may have added
        sql = re.sub(r"^```(?:sql)?\s*", "", raw, flags=re.IGNORECASE)
        sql = re.sub(r"\s*```$", "", sql).strip()

        # ── Reject non-SELECT / multi-statement SQL ───────────────────────────
        if not _is_select_only(sql):
            _log.info(
                "[%s] UNSAFE | department=%s | question=%r | sql=%r",
                datetime.now().isoformat(timespec="seconds"),
                department,
                question,
                sql,
            )
            print("[BLOCKED] Query contains unsafe or non-SELECT statements. Refused.\n")
            continue

        # ── Guardrail validation (defence-in-depth) ───────────────────────────
        if not _guardrail_passes(sql, department):
            _log.info(
                "[%s] BLOCKED | department=%s | question=%r | sql=%r",
                datetime.now().isoformat(timespec="seconds"),
                department,
                question,
                sql,
            )
            print(
                f"[BLOCKED] Generated SQL does not filter by '{department}'. "
                "Query refused.\n"
            )
            continue

        _log.info(
            "[%s] OK | department=%s | question=%r | sql=%r",
            datetime.now().isoformat(timespec="seconds"),
            department,
            question,
            sql,
        )

        # ── Execute ───────────────────────────────────────────────────────────
        try:
            columns, rows = _execute(sql)
            print(_format_table(columns, rows))
            # Record the exchange so follow-up questions have context
            history.append({"role": "user", "content": question})
            history.append({"role": "assistant", "content": sql})
        except sqlite3.Error as exc:
            _log.info(
                "[%s] EXEC_ERROR | department=%s | question=%r | sql=%r | error=%r",
                datetime.now().isoformat(timespec="seconds"),
                department,
                question,
                sql,
                str(exc),
            )
            print("[ERROR] Could not retrieve results. Try rephrasing your question.\n")
        print()


if __name__ == "__main__":
    main()
