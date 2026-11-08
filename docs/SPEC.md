# SPEC — Personal AI Assistant (Messenger + local LLM)

(Saved from the original brief. Decisions made later are in PLAN.md.)

## 1. Goal
A personal assistant chatted with on Facebook Messenger:
- Reminders set by chatting.
- Morning briefing: today's tasks, unfinished tasks from earlier days, meetings.
- Meeting reminders.
- Afternoon check-in ("Ano natapos mo today?") -> logs.
- Log activity any time by chatting.
- Work accomplishment report written from the logs.
- Keep Messenger's 24-hour window open by warning before it closes, especially on weekends.

User: IT Officer, Philippine government agency. Office hours 07:00-18:00 Mon-Fri, Asia/Manila. Some weekends are Work/Aral (study), some Off.
Messages are casual Taglish, e.g. "paalala mo ko bukas 3pm magpasa ng report", "may meeting sa Huwebes 10am with the director", "tapos ko na yung login bug, tsaka nag-assist ako sa printer sa admin", "mag-aaral ako ngayong Sabado", "gawa AR ngayong Nobyembre".

## 2. Machines
- Dev machine: no usable GPU.
- Run machine: separate PC, ~6GB VRAM GPU, 16GB RAM, runs Ollama + bot. **Manual start** (no service manager); must not sleep while running.
- Code moves via private Git. Machine-specific values only in `.env`; commit only `.env.example`.
- Webhook needs public HTTPS (Cloudflare Tunnel or similar). Dev and prod use separate Messenger setups.

## 3. LLM
OpenAI-compatible API (Ollama). Configurable: `LLM_BASE_URL`, `LLM_MODEL`, `LLM_REPORT_MODEL`.
Parser model ~3-4B, JSON-schema structured output, thinking off. Report model ~7-8B Q4.
Python does all date/time math (Asia/Manila); the LLM only extracts the time expression as text.
The LLM must never invent tasks or accomplishments. Uncertain parse -> ask to confirm.

## 4. Messenger 24-hour window
- Outbound only within 24h of last inbound action (message, quick-reply tap, reaction if subscribed). Safety limit 23h.
- Tags CONFIRMED_EVENT_UPDATE / ACCOUNT_UPDATE / POST_PURCHASE_UPDATE deprecated (2026). No HUMAN_AGENT for automation. One-Time Notification optional (Beta).
- Store `last_inbound_at`; check before every outbound; closed -> pending queue; on next inbound deliver pending starting with "Na-miss mo: ...". Optional SMTP for urgent.
- Window warning: on each inbound, one one-shot job at last_inbound_at+21h replacing the previous; short message with "👍 Tuloy" quick reply; mentions upcoming reminders.
- Quiet hours 23:00-06:00: move EARLIER to 22:00, never later.
- Off-weekend timeline: Fri 16:30 answer -> Sat ~13:30 warning -> tap -> Sun ~10:30 warning -> tap -> Mon 07:30 briefing delivered.
- Follow-up nudges are outbound and do NOT extend the window.
- Late briefing: on any inbound, if today's briefing hasn't been delivered, send it right away.

## 5. Daily flow (workdays, and weekend days set to Work/Aral)
- 07:30 briefing. No reply -> follow-up 08:30 then hourly; any message stops them; short nudge; cap default no follow-ups after 12:00.
- Meeting reminders at 30 and 10 minutes before.
- 16:30 check-in: answer -> log entries, match open tasks and mark done, carry unfinished over. Show understanding, confirm/edit before saving. No answer -> hourly follow-up until a real check-in answer ("wala"/"skip" count); other messages don't stop it. Last follow-up <= 22:30.
- Log any time.
- All times/intervals/limits configurable.
- Weekends/holidays/leave: Friday check-in asks work/aral/off (💼/📚/😴), per day if possible. Work/Aral -> normal flow; study entries context=school, excluded from work report. Off -> quiet except window warnings. Mode changeable by chat ("mag-aaral ako today", "naka-leave ako bukas").

## 6. Meetings
Undecided -> default: told to the bot; calendar sync later (optional).

## 7. Accomplishment report
From log entries with context=work in a date range. Template/frequency undecided (office sample to come). Formal English, filled into .docx (python-docx) or .xlsx (openpyxl). Optional IPCR tagging. Always a draft; raw text of every entry kept. Delivery: folder and/or Messenger attachment.

## 8. Stack
Python 3.12, FastAPI, APScheduler (SQLAlchemyJobStore) , SQLite/SQLAlchemy, httpx, pydantic, dateparser, python-docx, openpyxl, pytest. Docker optional.

## 9. Non-functional
Asia/Manila explicit everywhere. Verify X-Hub-Signature-256; ack fast, process async; dedupe. Survive restarts; missed jobs handled. Logging + health endpoint. Secrets only in `.env`. Work data stays local except what goes through Messenger.
Tests: Taglish date parsing, window check, quiet-hours shifting, warning rescheduling, off-weekend timeline, pending queue.

## 10. Phases
1 webhook/logging/check-in; 2 reminders/briefing/meetings/warning/queue; 3 weekend/holiday/leave; 4 report; 5 calendar (optional).
