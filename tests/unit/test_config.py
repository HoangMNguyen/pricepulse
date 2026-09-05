from decimal import Decimal
from pathlib import Path

import pytest

from pricepulse.config import Settings


def test_recipients_from_comma_separated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALERT_RECIPIENTS", "a@example.com, b@example.com")
    monkeypatch.setenv("ALERT_MIN_DISCOUNT_PCT", "15")
    s = Settings(_env_file=None)
    assert s.alert_recipients == ["a@example.com", "b@example.com"]
    assert s.alert_min_discount_pct == Decimal("15")


def test_env_example_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    example = Path(__file__).resolve().parents[2] / ".env.example"
    monkeypatch.delenv("ALERT_RECIPIENTS", raising=False)
    s = Settings(_env_file=example)
    assert s.alert_recipients == ["you@example.com"]
    assert s.database_url and s.database_url.endswith("/pricepulse")
    assert s.user_agent == "pricepulse/0.1"
