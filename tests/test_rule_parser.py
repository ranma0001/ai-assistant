from app.llm.parsers import RuleParser, validate_grounded
from app.llm.schemas import Entry, Intent

P = RuleParser()


def test_reminder():
    i = P.parse("paalala mo ko bukas 3pm magpasa ng report")
    assert i.intent == "set_reminder"
    assert i.when_text == "bukas 3pm"
    assert i.title == "magpasa ng report"


def test_meeting():
    i = P.parse("may meeting sa Huwebes 10am with the director")
    assert i.intent == "add_meeting"
    assert i.when_text == "sa Huwebes 10am"
    assert "director" in i.title.lower()


def test_log_two_entries():
    i = P.parse("tapos ko na yung login bug, tsaka nag-assist ako sa printer sa admin")
    assert i.intent == "log_activity"
    assert [e.raw for e in i.entries] == ["tapos ko na yung login bug", "nag-assist ako sa printer sa admin"]


def test_day_mode_aral():
    i = P.parse("mag-aaral ako ngayong Sabado")
    assert i.intent == "set_day_mode" and i.mode == "aral" and i.date_text == "ngayong Sabado"


def test_leave():
    i = P.parse("naka-leave ako bukas")
    assert i.intent == "set_day_mode" and i.mode == "leave" and i.date_text == "bukas"


def test_task_month():
    i = P.parse("gawa AR ngayong Nobyembre")
    assert i.intent == "add_task" and i.title == "AR" or i.title.lower().startswith("ar")
    assert i.due_text == "ngayong Nobyembre"


def test_skip_only_in_checkin():
    assert P.parse("wala", hint="checkin").intent == "checkin_skip"
    assert P.parse("wala").intent == "unknown"


def test_checkin_answer_with_not_done():
    i = P.parse("natapos ko yung backup, di pa yung network audit", hint="checkin")
    assert i.intent == "checkin_answer"
    st = {e.raw: e.status for e in i.entries}
    assert st["natapos ko yung backup"] == "done"
    assert st["di pa yung network audit"] == "not_done"


def test_query_agenda():
    assert P.parse("ano sched ko bukas?").intent == "query_agenda"


def test_unknown():
    assert P.parse("kumusta?").intent == "unknown"


def test_report_request():
    i = P.parse("gawa mo report ng Oktubre")
    assert i.intent == "generate_report"


def test_llm_output_is_grounded():
    text = "tapos ko na yung login bug"
    bad = Intent(intent="log_activity", confidence=0.9, entries=[
        Entry(raw="tapos ko na yung login bug"), Entry(raw="nag-deploy ng server")])
    out = validate_grounded(bad, text)
    assert [e.raw for e in out.entries] == ["tapos ko na yung login bug"]
    assert out.confidence <= 0.5


def test_llm_invented_time_dropped():
    i = Intent(intent="set_reminder", confidence=0.9, title="x", when_text="bukas 3pm")
    out = validate_grounded(i, "paalala mo ko magpasa ng report")
    assert out.when_text is None
