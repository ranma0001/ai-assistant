import httpx
from docx import Document
from openpyxl import load_workbook

from app.config import Settings
from app.db.models import LogEntry
from app.harness import Harness
from app.llm.parsers import CompositeParser, LLMParser
from conftest import dt


def log_entry(h, text, day=(11, 9), ctx="work"):
    from app.timeutil import TZ
    from datetime import date, datetime
    with h.sessions() as s:
        s.add(LogEntry(raw_text=text, summary=text, log_date=date(2026, *day), context=ctx,
                       created_at=datetime(2026, 11, 9, 10, tzinfo=TZ)))
        s.commit()


def cfg(tmp_path):
    return Settings(_env_file=None, parser="rule", owner_psid="u1", report_dir=str(tmp_path),
                    report_author="Juan Dela Cruz", report_office="MIS Division")


def test_report_only_includes_work_entries_in_range(make, tmp_path):
    h = make(dt(11, 30, 10, 0), settings=cfg(tmp_path))
    log_entry(h, "tapos ko na yung login bug", (11, 9))
    log_entry(h, "nag-assist ako sa printer sa admin", (11, 10))
    log_entry(h, "natapos ko yung reviewer chapter 3", (11, 14), ctx="school")
    log_entry(h, "nag-backup ng server", (10, 20))  # October: out of range
    h.say("hi")
    out = h.say("gawa mo report ng Nobyembre")
    assert "2 entry" in out[-1] and "Draft" in out[-1]
    doc = Document(h.channel.files[0]["path"])
    cells = [c.text for row in doc.tables[0].rows for c in row.cells]
    assert any("login bug" in c for c in cells) and any("printer" in c for c in cells)
    assert not any("reviewer" in c or "backup" in c for c in cells)
    assert "DRAFT" in "\n".join(p.text for p in doc.paragraphs)


def test_report_keeps_raw_text_column_and_xlsx(make, tmp_path):
    h = make(dt(11, 30, 10, 0), settings=cfg(tmp_path))
    log_entry(h, "tapos ko na yung login bug", (11, 9))
    h.say("hi")
    h.say("gawa mo report ng Nobyembre in excel")
    ws = load_workbook(h.channel.files[0]["path"]).active
    vals = [c.value for r in ws.iter_rows() for c in r]
    assert "tapos ko na yung login bug" in vals          # raw kept
    assert "Tapos ko na yung login bug." in vals          # fallback: tidied, nothing invented


def test_report_with_no_entries_says_so(make, tmp_path):
    h = make(dt(11, 30, 10, 0), settings=cfg(tmp_path))
    out = h.say("gawa mo report ng Nobyembre")
    assert "Walang work log" in out[-1]
    assert h.channel.files == []


class FakeFormalizer:
    def __init__(self, mapping):
        self.mapping = mapping

    def formalize(self, lines, model):
        return {i: self.mapping[i] for i, _ in lines if i in self.mapping}


def test_report_rejects_invented_numbers_from_llm(make, tmp_path):
    f = FakeFormalizer({1: "Resolved the login defect.", 2: "Assisted 15 users with printer configuration."})
    h = make(dt(11, 30, 10, 0), settings=cfg(tmp_path), formalizer=f)
    log_entry(h, "tapos ko na yung login bug", (11, 9))
    log_entry(h, "nag-assist ako sa printer sa admin", (11, 10))
    h.say("hi")
    h.say("gawa mo report ng Nobyembre")
    doc = Document(h.channel.files[0]["path"])
    cells = [c.text for row in doc.tables[0].rows for c in row.cells]
    assert "Resolved the login defect." in cells
    assert not any("15 users" in c for c in cells)
    assert "Nag-assist ako sa printer sa admin." in cells


# ------------------------------------------------------------------ LLM client (HTTP mocked)

def _fake_post(content):
    def post(url, json=None, timeout=None, headers=None):
        assert url.endswith("/chat/completions")
        assert json["response_format"]["type"] == "json_schema"
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]},
                              request=httpx.Request("POST", url))
    return post


def test_llm_parser_validates_and_grounds(monkeypatch):
    monkeypatch.setattr(httpx, "post", _fake_post(
        '{"intent":"log_activity","confidence":0.95,"entries":[{"raw":"tapos ko na yung login bug","status":"done"},'
        '{"raw":"nag-deploy ng server","status":"done"}]}'))
    p = LLMParser("http://x/v1", "k", "m")
    out = p.parse("tapos ko na yung login bug")
    assert [e.raw for e in out.entries] == ["tapos ko na yung login bug"]


def test_llm_strips_think_tags(monkeypatch):
    monkeypatch.setattr(httpx, "post", _fake_post('<think>hmm</think>{"intent":"unknown","confidence":0.2}'))
    assert LLMParser("http://x/v1", "k", "m").parse("kumusta").intent == "unknown"


def test_composite_falls_back_to_rules_when_llm_down(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "post", boom)
    c = CompositeParser(LLMParser("http://x/v1", "k", "m"))
    assert c.parse("paalala mo ko bukas 3pm magpasa ng report").intent == "set_reminder"


def test_garbage_json_falls_back(monkeypatch):
    monkeypatch.setattr(httpx, "post", _fake_post("not json"))
    c = CompositeParser(LLMParser("http://x/v1", "k", "m"))
    assert c.parse("naka-leave ako bukas").intent == "set_day_mode"
