from datetime import datetime

import pytest

from app.harness import Harness
from app.timeutil import TZ


def dt(m, d, h=0, mi=0, y=2026):
    return datetime(y, m, d, h, mi, tzinfo=TZ)


@pytest.fixture
def make():
    """make(start) -> Harness at a given Manila time. 2026-11-13 is a Friday."""

    def _make(start, **kw):
        return Harness(start, **kw)

    return _make
