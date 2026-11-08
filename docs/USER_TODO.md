# Your part (not done by the build)

Everything in `app/` works and is tested without Facebook. These steps connect it to Messenger and put it on
the run machine. Do the whole Facebook section twice: once for DEV, once for PROD (separate Page, App, tokens).

## A. Try it first, no Facebook needed
```
.venv\Scripts\python -m app.console --sim --start "2026-11-13 16:20"
```
Type messages, `/+10m`, `/goto 2026-11-14 13:30`, `/tap KEEPALIVE`, `/react`. The off-weekend timeline can be
replayed in a few commands.

## B. Facebook / Messenger (per environment)
1. Create a Facebook Page ("Assistant Dev" / "Assistant").
2. developers.facebook.com -> Create App -> use case **Engage with customers on Messenger**. Leave it in
   **Development mode** (you are admin; no App Review needed to message yourself).
3. Messenger settings: connect the Page, generate the **Page Access Token**. Copy the **App Secret**
   (Settings -> Basic).
4. In `.env`: `CHANNEL=messenger`, `FB_PAGE_TOKEN`, `FB_APP_SECRET`, `FB_VERIFY_TOKEN` (any random string you
   choose), `FB_PAGE_ID`.
5. Start a public HTTPS tunnel to `localhost:8000`:
   - quick test: `cloudflared tunnel --url http://localhost:8000` (URL changes every start)
   - stable: Cloudflare named tunnel (needs a domain) or Tailscale Funnel.
6. App -> Messenger -> Webhooks: Callback URL `https://<your-host>/webhook`, Verify token = `FB_VERIFY_TOKEN`.
   Subscribe to: `messages`, `messaging_postbacks`, **`message_reactions`**. Subscribe the Page to the app.
7. Message your Page from your own Facebook account. The log prints
   `OWNER_PSID is not set. Message came from PSID 123...` -> put that in `.env` as `OWNER_PSID`, restart.
8. **Verify these against the real thing (not confirmable from docs):**
   - Does tapping a quick reply (`👍 Tuloy`) reopen the 24h window? (Expect the warning to be rescheduled:
     check `/health` -> `last_inbound_at`.)
   - Does a 👍 reaction count (`last_inbound_at` changes)?
   - Does the report file arrive as an attachment?
   If a quick-reply tap does NOT count, tell me; the warning text would need to ask for a typed reply instead.
9. Optional: One-Time Notification is not used (Beta, one message per token, availability in dev mode unknown).

## C. Run machine (manual start)
1. Install Python 3.12, Git, Ollama. `ollama pull qwen3:4b-instruct-2507` and `ollama pull qwen3:8b`
   (try other ~4B models too; the parser is swappable via `LLM_MODEL`).
2. Set `OLLAMA_MAX_LOADED_MODELS=1` (6GB VRAM cannot hold both models).
3. `git clone` the private repo, then:
   ```
   py -3.12 -m venv .venv
   .venv\Scripts\pip install -e .
   copy .env.example .env     # fill in PROD values + LLM_BASE_URL=http://localhost:11434/v1
   ```
4. Windows: Settings -> Power -> sleep **Never** (the script also asks Windows to stay awake while it runs).
5. Start: `.\scripts\run_prod.ps1`, then start the tunnel. The startup banner shows the window state and any
   missed work.
6. Update: `git pull`, `.venv\Scripts\pip install -e .`, restart the script. Back up `data\assistant.db`.

## D. Optional: calendar sync (built, read-only)
Put your calendar's secret iCal (ICS) address in `.env` as `CALENDAR_ICS_URLS` (comma-separate several).
Google: Calendar settings -> your calendar -> Integrate calendar -> *Secret address in iCal format*.
Outlook/M365: Publish calendar -> ICS link (if your agency allows publishing; otherwise just tell the bot meetings by chat).
Every 15 min (`CALENDAR_SYNC_MINUTES`) the next 14 days are mirrored as meetings with the 30/10-min reminders.
Recurring events are expanded; all-day and cancelled events are skipped; moved/renamed/removed events update their
reminders; a meeting you cancel in chat is not resurrected; nothing is ever written back to the calendar.
Treat the URL like a password (anyone with it can read your calendar). Not tested against a live feed yet:
check the log line `calendar sync: {...}` after the first start. Government accounts may block secret-URL sharing.

## E. Things to decide later
- Office report template: send the sample; the current .docx/.xlsx is a generic table
  (Date | Accomplishment | Original log entry | IPCR/Remarks) in `app/reports/builder.py`.
- Weekly or monthly report: ask the bot "gawa mo report ng Oktubre" / "report this week".
- E-mail fallback: set `SMTP_*` in `.env` (meeting reminders are marked urgent).
- Government data: keep confidential or personal data of others out of the chat; it passes through Meta.
