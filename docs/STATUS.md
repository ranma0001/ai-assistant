# STATUS: what's done / what's next

Last updated: 2026-11-09. Repo: https://github.com/ranma0001/ai-assistant (branch `main`).
Related docs: `SPEC.md` (requirements), `PLAN.md` (design/phases), `USER_TODO.md` (detailed setup steps).

## Done (all tested without Facebook: 121 tests passing, ~37s)

| Area | What works |
|---|---|
| Core | `Assistant` behind a `Channel` interface (console/sim channel and Messenger channel), DB-driven outbox scheduler (tick loop), fake clock for time-travel tests |
| Messenger 24h window | 23h send limit, warning at last_inbound+21h (replaced on each inbound, with "👍 Tuloy" quick reply), pending queue ("Na-miss mo:"), late briefing, quiet hours only shift earlier (22:00) |
| Daily flow | Reminders by chat, meetings (30 and 10 min before), 07:30 briefing with hourly follow-ups until 12:00, 16:30 check-in with hourly follow-ups until 22:30, ad-hoc logging, all confirm-before-save |
| Day modes | Work / Aral / Off / Leave / PH holidays; Friday asks about the weekend; Aral logs are excluded from the work report |
| Parsing | Taglish time parser (Python resolves dates), rule-based intent parser, OpenAI-compatible LLM parser with grounding checks (never invents tasks) |
| Report | Accomplishment report as .docx/.xlsx from work logs only, always a DRAFT, raw text kept, rejects invented numbers |
| Messenger code | Webhook, X-Hub-Signature-256 check, dedup, owner-PSID filter, Graph client with file upload, `/health` |
| Calendar sync | Read-only ICS feed (`CALENDAR_ICS_URLS`), every 15 min, 14 days ahead, recurring events, reschedule/rename/remove handled. See `USER_TODO.md` section D |
| Tooling | `app/console.py` (terminal chat, `--sim` fake clock), `scripts/run_prod.ps1` / `run_dev.ps1` (manual start, no service manager) |

## NOT verified yet (needs the real thing)
1. **Messenger client against live Meta**: never run against real Facebook.
2. **Does a quick-reply tap reopen the 24h window?** Not stated in Meta docs. If it doesn't, change the warning to ask for a typed reply.
3. **Does a 👍 reaction reopen the window?** (listed on the policy page, but confirm live)
4. **LLM parser against real Ollama**: only tested with mocks. The rule parser is the tested fallback.
5. **Calendar sync against a real ICS feed**, including a moved event (currently the old time is cancelled and the new one added as a new meeting).
6. **Report file arrives as an attachment** in Messenger.

## Next steps (in order)

### A. On your computer (quick, no Facebook)
- [ ] `git clone https://github.com/ranma0001/ai-assistant.git`, then venv + `pip install -e .[dev]`, then `pytest` (expect 121 passed)
- [ ] Try the sim: `.venv\Scripts\python -m app.console --sim --start "2026-11-13 16:20"`
  (commands: `/+10m`, `/goto 2026-11-14 13:30`, `/tap KEEPALIVE`, `/react`, `/state`)

### B. Facebook (your part; do DEV first, then PROD with a separate Page/App/tokens)
- [ ] Create Page and App (Development mode), get Page Access Token and App Secret
- [ ] Fill `.env`: `CHANNEL=messenger`, `FB_PAGE_TOKEN`, `FB_APP_SECRET`, `FB_VERIFY_TOKEN`, `FB_PAGE_ID`
- [ ] Tunnel to `localhost:8000`, set webhook `https://<host>/webhook`, subscribe `messages`, `messaging_postbacks`, `message_reactions`
- [ ] Message the Page, copy the PSID from the log into `OWNER_PSID`, restart
- [ ] Do the verification checks (items 2, 3, 6 above) and tell me the results

### C. Run machine (manual start)
- [ ] Python 3.12, Git, Ollama; `ollama pull qwen3:4b-instruct-2507`; set `OLLAMA_MAX_LOADED_MODELS=1`
- [ ] `.env` with PROD values and `LLM_BASE_URL=http://localhost:11434/v1`
- [ ] Windows sleep = Never; start with `.\scripts\run_prod.ps1`, then the tunnel
- [ ] Back up `data\assistant.db`

### D. Optional / later
- [ ] Calendar: put the secret ICS URL in `.env` (`CALENDAR_ICS_URLS`) and check the log for `calendar sync: {...}`
- [ ] Send a sample of the office accomplishment report template so I can match it (current one is a generic table: Date | Accomplishment | Original log entry | IPCR/Remarks)
- [ ] E-mail fallback: `SMTP_*` in `.env`
- [ ] Test and tune the LLM parser with real Taglish messages once Ollama is running

## Possible follow-ups for me (Claude) once you have results
- Change the warning wording if quick-reply taps don't reopen the window.
- Fix anything the live Messenger run exposes (attachment-only events are loosely mapped to KEEPALIVE in `app/messenger/events.py`).
- Handle a moved calendar event as an update instead of cancel + add.
- Match the report template; add weekly/monthly report variants.
- Tidy `app/console.py` (stray `_state` helper).

## Quick reference
- Run tests: `.venv\Scripts\python -m pytest`
- Sim console: `.venv\Scripts\python -m app.console --sim`
- Dev server: `.\scripts\run_dev.ps1`; prod: `.\scripts\run_prod.ps1`
- Health: `http://localhost:8000/health`
- Never commit `.env`; only `.env.example` is tracked.
