from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Task

_STOP = {
    "yung", "ang", "ng", "na", "ko", "ako", "sa", "si", "mga", "tapos", "natapos", "nag", "nagawa", "ginawa",
    "done", "finished", "at", "and", "the", "to", "ni", "kay", "pa", "di", "hindi", "na-", "ay", "para",
    "tsaka", "saka", "ung", "po", "lang", "yun", "iyong", "ito", "dito", "doon", "finish", "gawa", "mag",
}


def tokens(text: str) -> set[str]:
    out = set()
    for w in re.findall(r"[\w'-]+", text.lower()):
        w = re.sub(r"^(nag|na|mag|in|ni)-", "", w)
        if w in _STOP or len(w) < 2:
            continue
        out.add(w)
    return out


def match_task(s: Session, text: str, threshold: float = 0.6) -> Task | None:
    """Fuzzy match free text to ONE open task (token overlap). Returns None if unsure/ambiguous."""
    q = tokens(text)
    if not q:
        return None
    best: tuple[float, Task] | None = None
    second = 0.0
    for t in s.scalars(select(Task).where(Task.status == "open")):
        tt = tokens(t.title)
        if not tt:
            continue
        common = q & tt
        if not any(len(w) > 2 for w in common):
            continue
        score = len(common) / min(len(q), len(tt))
        if best is None or score > best[0]:
            second = best[0] if best else 0.0
            best = (score, t)
        elif score > second:
            second = score
    if best and best[0] >= threshold and best[0] > second:
        return best[1]
    return None
