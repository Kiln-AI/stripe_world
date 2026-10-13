"""`demo.py`, the README's one-command demo: the charge it makes is in the state it prints."""

import demo
import pytest


def test_the_demo_charges_the_card_and_prints_the_charge_row(
    capsys: pytest.CaptureFixture[str],
) -> None:
    demo.main()
    printed = capsys.readouterr().out
    assert "Charged $20.00 USD to visa ending 4242: succeeded" in printed
    assert '"table": "charges"' in printed
    assert '"status": "succeeded"' in printed
