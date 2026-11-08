# PLAN

## Decisions (approved)
- All unanswered questions use the defaults proposed in the planning session.
- **Build order:** core first behind a `Channel` interface with a console channel and a fake clock; Messenger last.
- **Manual start** (no NSSM/systemd/Task Scheduler). `scripts/run_prod.ps1` starts bot (+ checks Ollama/tunnel). Startup runs a missed-job sweep and prints a status banner.
- **Scheduler:** DB-driven `outbox` (due_at, expires_at, dedup_key) polled by a tick loop; single process. APScheduler not required.
- **Left to the user:** Facebook Page/App/webhook/tunnel setup (dev and prod) and deployment on the run machine. See `docs/USER_TODO.md`.
- Calendar sync (Phase 6) = read-only ICS feed polling (`app/services/calendar_sync.py`), no OAuth.
- Report template is unknown: a generic default .docx/.xlsx template is provided; swap when the office sample arrives.

## Architecture
Channel (console | messenger) -> inbox (dedup, persisted) -> router/state machine -> LLM intent JSON -> services -> SQLite.
Outbox tick -> OutboundGate (window 23h, quiet hours -> 22:00 earlier, expiry) -> channel.send; closed window -> pending; next inbound -> "Na-miss mo".

## Key rules
- Window open = now < last_inbound_at + 23h. Only inbound message/quick-reply/reaction updates it.
- Warning at last_inbound+21h, one row (`warning`), replaced on each inbound; quiet hours shift earlier to 22:00.
- Expired messages are not sent; they appear as one line under "Na-miss mo". Briefing is regenerated, not replayed.
- Check-in answer = checkin_answer / checkin_skip / log_activity after the prompt was sent.
- LLM entries must have `raw` as a substring of the user's message; nothing invented.

## Phases
0 Foundation, 1 Parsing + check-in, 2 Reminders/briefing/warning/queue, 3 Day modes, 4 Report, 5 Messenger channel (code only; setup by user), 6 Calendar (read-only ICS sync, done).
Acceptance criteria and test plan as discussed; tests live in `tests/`.
