# Personal AI Assistant (Messenger + local LLM)

Taglish chat assistant: reminders, morning briefing, meeting reminders, afternoon check-in -> logs,
accomplishment report, and Messenger 24-hour window management. See `docs/SPEC.md`, `docs/PLAN.md`,
and `docs/USER_TODO.md` (Facebook setup + deployment).

```
py -3.12 -m venv .venv && .venv\Scripts\pip install -e .[dev]
.venv\Scripts\python -m pytest                 # all tests, no network needed
.venv\Scripts\python -m app.console --sim      # chat in the terminal with a fake clock
.\scripts\run_prod.ps1                         # webhook server (needs .env)
```

Layout: `app/core` (assistant, channel interface), `app/nlp` (Taglish time parsing), `app/llm` (intent parsers),
`app/services`, `app/reports`, `app/messenger` (webhook, Graph client), `tests/`.
