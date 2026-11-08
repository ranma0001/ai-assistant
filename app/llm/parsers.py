"""Intent parsers: an offline rule-based Taglish parser and an OpenAI-compatible LLM parser.

Both only EXTRACT. Dates are resolved later by app.nlp.when_parser. Entries are validated so that
`raw` is a verbatim substring of the user's message (the model may never invent accomplishments).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Protocol

import httpx

from app.llm.schemas import Entry, FormalReport, Intent
from app.nlp.when_parser import find_when_span

log = logging.getLogger(__name__)


class IntentParser(Protocol):
    def parse(self, text: str, hint: str | None = None) -> Intent: ...


# ------------------------------------------------------------------ rule parser

_SKIP = {"wala", "skip", "wala po", "wala pa", "wala muna", "none", "pass", "wala naman"}
_REMIND = re.compile(r"\b(paalala|paalalahanan|ipaalala|pakipaalala|remind|reminder|alarm)\b", re.I)
_MEETING = re.compile(r"\b(meeting|pulong|conference|zoom|call with|huddle|consultation)\b", re.I)
_TASK = re.compile(r"^\s*(gawa|gagawin|todo|to-do|task|kailangan(?: kong)?|need(?: to)?|dapat|add task|i-?add)\b", re.I)
_QUERY = re.compile(r"\b(ano|anong)\b.*\b(sched|schedule|agenda|task|gagawin|meeting)\b|\bagenda\b", re.I)
_REPORT = re.compile(r"\b(report|accomplishment|ar)\b", re.I)
_REPORT_VERB = re.compile(r"\b(gawa(?:n)? mo|gawan mo|generate|buo(?:in)?|prepare|make)\b.*\b(report|accomplishment)\b", re.I)
_CANCEL = re.compile(r"\b(cancel|i-?cancel|kanselahin|burahin|tanggalin|alisin|delete)\b", re.I)
_PAST = re.compile(
    r"\b(tapos|natapos|tapos na|nag-?\w+|na-?\w+|done|finished|fixed|ginawa|nagawa|inayos|"
    r"sinagot|nilagay|nag|nag-assist|nag assist|ni-?\w+|completed|updated|installed|checked|attended)\b", re.I)
_NOT_DONE = re.compile(r"\b(di pa|hindi pa|wala pa|di ko pa|hindi ko pa|di pa tapos|pending|bukas na lang)\b", re.I)
_MODE = [
    ("aral", re.compile(r"\b(mag-?aaral|aaral|study|mag-?review|klase|class)\b", re.I)),
    ("leave", re.compile(r"\b(leave|nakaleave|naka-leave|bakasyon|sick)\b", re.I)),
    ("off", re.compile(r"\b(off|walang pasok|day off|pahinga|magpapahinga|rest day)\b", re.I)),
    ("work", re.compile(r"\b(papasok|magtatrabaho|mag-?work|work ako|trabaho|overtime|ot)\b", re.I)),
]
_SPLIT = re.compile(r"\s*(?:,\s*(?:tsaka|saka|at|and|tapos)\s+|,\s*|;\s*|\s+tsaka\s+|\s+saka\s+|\s+and then\s+|\s+at\s+(?=nag|na))", re.I)


def _strip_when(text: str) -> tuple[str, str | None]:
    span = find_when_span(text)
    if not span:
        return text.strip(), None
    when = text[span[0]:span[1]]
    rest = (text[: span[0]] + " " + text[span[1]:])
    return re.sub(r"\s+", " ", rest).strip(" ,.-"), when


class RuleParser:
    """Heuristic Taglish parser. Works offline; used when no LLM is configured or as fallback."""

    def parse(self, text: str, hint: str | None = None) -> Intent:
        raw = text.strip()
        low = raw.lower().strip(" .!")
        if hint == "checkin" and low in _SKIP:
            return Intent(intent="checkin_skip", confidence=0.98)
        if low in _SKIP and hint != "checkin":
            return Intent(intent="unknown", confidence=0.3)

        if _CANCEL.search(raw) and hint != "checkin":
            target = _CANCEL.sub("", raw)
            target = re.sub(r"\b(mo|yung|ung|ang|na|ko|ng|po)\b", " ", target, flags=re.I)
            return Intent(intent="cancel_or_edit", confidence=0.85, action="cancel",
                          target_ref=re.sub(r"\s+", " ", target).strip() or None)

        if _REPORT_VERB.search(raw):
            _, when = _strip_when(raw)
            return Intent(intent="generate_report", confidence=0.85, range_text=when or raw)

        past_start = re.match(r"\s*(nag|tapos|natapos|ginawa|nagawa|na-)", raw, re.I)
        for mode, rx in _MODE:
            if past_start or not rx.search(raw):
                continue
            if mode == "work" and not re.search(r"\b(papasok|magtatrabaho|work|overtime)\b", raw, re.I):
                continue
            _, when = _strip_when(raw)
            return Intent(intent="set_day_mode", confidence=0.9, mode=mode, date_text=when)

        if _QUERY.search(raw):
            _, when = _strip_when(raw)
            return Intent(intent="query_agenda", confidence=0.85, when_text=when)

        if _REMIND.search(raw):
            rest, when = _strip_when(raw)
            title = re.sub(r"\b(paalala|ipaalala|pakipaalala|paalalahanan|remind|reminder)\b( mo| me| ako| ko| kay| na)*", "", rest, flags=re.I)
            title = re.sub(r"^(?:\s*(?:mo|me|ko|ako|na|to|ng|yung|the)\b)+", "", title, flags=re.I)
            title = re.sub(r"\s+", " ", title).strip(" ,.-")
            return Intent(intent="set_reminder", confidence=0.9 if when else 0.6, title=title or None, when_text=when)

        if _MEETING.search(raw) and find_when_span(raw) and not _PAST.search(raw.split(",")[0]):
            rest, when = _strip_when(raw)
            title = re.sub(r"^(may|mayroong|meron|schedule|sched)\s+", "", rest, flags=re.I).strip(" ,.-")
            att = re.findall(r"\bwith\s+(?:the\s+)?([A-Za-z][\w .&-]*)$", title, flags=re.I)
            return Intent(intent="add_meeting", confidence=0.88, title=title[:1].upper() + title[1:],
                          when_text=when, attendees=[a.strip() for a in att])

        if _TASK.search(raw) and not _PAST.search(raw):
            rest, when = _strip_when(raw)
            title = re.sub(_TASK, "", rest).strip(" ,.-") or rest
            return Intent(intent="add_task", confidence=0.8, title=title, due_text=when)

        if _PAST.search(raw) or hint == "checkin":
            entries = self._entries(raw)
            if entries:
                name = "checkin_answer" if hint == "checkin" else "log_activity"
                return Intent(intent=name, confidence=0.8, entries=entries)

        return Intent(intent="unknown", confidence=0.3)

    @staticmethod
    def _entries(raw: str) -> list[Entry]:
        parts = [p for p in _SPLIT.split(raw) if p and p.strip()]
        out: list[Entry] = []
        for p in parts:
            p = p.strip(" .,;")
            if not p or len(p) < 3:
                continue
            if p.lower() in ("tsaka", "saka", "at", "and", "tapos"):
                continue
            status = "not_done" if _NOT_DONE.search(p) else "done"
            if raw.find(p) < 0:
                continue
            out.append(Entry(raw=p, status=status))
        return out


# ------------------------------------------------------------------ LLM parser

SYSTEM_PROMPT = """You turn one casual Taglish (Filipino-English) chat message into JSON for a personal \
assistant. Output ONLY JSON matching the schema.
Rules:
- Never invent tasks, accomplishments, people or times. Use only words the user wrote.
- `when_text`, `due_text`, `date_text`, `range_text`, `duration_text` are time expressions COPIED EXACTLY \
from the message (e.g. "bukas 3pm", "sa Huwebes 10am", "ngayong Nobyembre"). Never compute dates.
- `entries[].raw` must be an exact substring of the message, one per distinct activity.
- intents: set_reminder (paalala/remind), add_meeting, add_task (something to do, maybe with deadline), \
log_activity (things the user already did), checkin_answer / checkin_skip (only when context=checkin; \
"wala"/"skip" = skip), set_day_mode (work/aral/off/leave for a day), complete_task, cancel_or_edit, \
snooze, query_agenda, generate_report, unknown.
- confidence: 0..1; use low values when unsure."""

FORMAL_PROMPT = """You rewrite a Filipino government IT officer's raw work-log lines as formal English \
accomplishment statements (past tense, one sentence each, no embellishment).
STRICT RULES: use only facts in the line; do not add systems, numbers, people, purposes or results that are \
not written; keep one output per input id; if a line is unclear, restate it plainly.
Return JSON: {"items":[{"id":<id>,"text":"..."}]}"""


class LLMParser:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def _chat(self, system: str, user: str, schema: dict, model: str | None = None) -> dict:
        body = {
            "model": model or self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0,
            "response_format": {"type": "json_schema", "json_schema": {"name": "out", "schema": schema}},
            # turn thinking off where supported (ignored otherwise)
            "reasoning_effort": "none",
            "think": False,
        }
        r = httpx.post(f"{self.base_url}/chat/completions", json=body, timeout=self.timeout,
                       headers={"Authorization": f"Bearer {self.api_key}"})
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
        return json.loads(content)

    def parse(self, text: str, hint: str | None = None) -> Intent:
        user = f"context={hint or 'none'}\nmessage: {text}"
        data = self._chat(SYSTEM_PROMPT, user, Intent.model_json_schema())
        intent = Intent.model_validate(data)
        return validate_grounded(intent, text)

    def formalize(self, lines: list[tuple[int, str]], model: str) -> dict[int, str]:
        user = json.dumps([{"id": i, "raw": t} for i, t in lines], ensure_ascii=False)
        data = self._chat(FORMAL_PROMPT, user, FormalReport.model_json_schema(), model=model)
        rep = FormalReport.model_validate(data)
        valid = {i for i, _ in lines}
        return {it.id: it.text.strip() for it in rep.items if it.id in valid and it.text.strip()}


def validate_grounded(intent: Intent, text: str) -> Intent:
    """Drop anything the model made up: entries must be verbatim; time phrases must appear in the text."""
    low = text.lower()
    kept = [e for e in intent.entries if e.raw and e.raw.lower() in low]
    if len(kept) != len(intent.entries):
        log.warning("LLM entries not grounded in message; dropped %d", len(intent.entries) - len(kept))
        intent.confidence = min(intent.confidence, 0.5)
    intent.entries = kept
    for f in ("when_text", "due_text", "date_text", "range_text", "duration_text"):
        v = getattr(intent, f)
        if v and v.lower() not in low:
            setattr(intent, f, None)
            intent.confidence = min(intent.confidence, 0.5)
    if intent.intent in ("log_activity", "checkin_answer") and not intent.entries:
        intent.confidence = min(intent.confidence, 0.4)
    return intent


class CompositeParser:
    """LLM first; falls back to rules if the LLM is unreachable or returns garbage."""

    def __init__(self, llm: LLMParser | None, rules: RuleParser | None = None):
        self.llm = llm
        self.rules = rules or RuleParser()

    def parse(self, text: str, hint: str | None = None) -> Intent:
        if self.llm is not None:
            try:
                return self.llm.parse(text, hint)
            except Exception as e:  # noqa: BLE001
                log.warning("LLM parse failed (%s); using rule parser", e)
        return self.rules.parse(text, hint)


def build_parser(settings) -> CompositeParser:  # noqa: ANN001
    mode = settings.parser
    if mode == "rule" or (mode == "auto" and not settings.llm_base_url):
        return CompositeParser(None)
    return CompositeParser(LLMParser(settings.llm_base_url, settings.llm_api_key,
                                     settings.llm_model, settings.llm_timeout_s))
