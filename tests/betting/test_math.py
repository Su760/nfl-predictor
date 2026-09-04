from decimal import Decimal

import pytest

from nfl_predictor.betting.math import displayed_ev, quarter_kelly, same_book_clv


def test_push_has_zero_payoff_in_ev_and_kelly() -> None:
    assert displayed_ev(Decimal("0.55"), Decimal("0.44"), Decimal("2.00")) == Decimal("0.11")
    assert quarter_kelly(Decimal("0.55"), Decimal("0.44"), Decimal("2.00")) == Decimal(
        "0.02777777777777777777777777778"
    )


def test_negative_ev_is_never_assigned_a_negative_kelly_fraction() -> None:
    assert quarter_kelly(Decimal("0.40"), Decimal("0.60"), Decimal("2.00")) == 0


def test_same_book_clv_is_log_decision_price_over_close_price() -> None:
    assert same_book_clv(Decimal("2.10"), Decimal("2.00")) == pytest.approx(0.04879016416943205)


@pytest.mark.parametrize("price", [Decimal(1), Decimal(0)])
def test_money_math_rejects_non_profit_decimal_prices(price: Decimal) -> None:
    with pytest.raises(ValueError, match="greater than one"):
        displayed_ev(Decimal("0.5"), Decimal("0.5"), price)
    with pytest.raises(ValueError, match="greater than one"):
        quarter_kelly(Decimal("0.5"), Decimal("0.5"), price)


@pytest.mark.parametrize(
    ("p_win", "p_loss", "price"),
    [
        (Decimal("NaN"), Decimal("0.4"), Decimal(2)),
        (Decimal("0.5"), Decimal("Infinity"), Decimal(2)),
        (Decimal("-0.1"), Decimal("0.5"), Decimal(2)),
        (Decimal("0.7"), Decimal("0.4"), Decimal(2)),
        (Decimal("0.5"), Decimal("0.5"), Decimal("NaN")),
    ],
)
def test_ev_and_kelly_reject_nonfinite_or_invalid_domains(
    p_win: Decimal, p_loss: Decimal, price: Decimal
) -> None:
    with pytest.raises(ValueError):
        displayed_ev(p_win, p_loss, price)
    with pytest.raises(ValueError):
        quarter_kelly(p_win, p_loss, price)


@pytest.mark.parametrize(
    ("decision_price", "close_price"),
    [(Decimal("NaN"), Decimal(2)), (Decimal(2), Decimal("Infinity"))],
)
def test_clv_rejects_nonfinite_prices(decision_price: Decimal, close_price: Decimal) -> None:
    with pytest.raises(ValueError):
        same_book_clv(decision_price, close_price)
