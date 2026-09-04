from __future__ import annotations

import math
from decimal import Decimal


def _require_price(decimal_price: Decimal) -> None:
    if not isinstance(decimal_price, Decimal):
        raise TypeError("decimal price must be a Decimal")
    if not decimal_price.is_finite() or decimal_price <= Decimal(1):
        raise ValueError("decimal price must be greater than one")


def _require_probabilities(p_win: Decimal, p_loss: Decimal) -> None:
    if not isinstance(p_win, Decimal) or not isinstance(p_loss, Decimal):
        raise TypeError("probabilities must be Decimal values")
    if not p_win.is_finite() or not p_loss.is_finite():
        raise ValueError("probabilities must be finite")
    if p_win < 0 or p_loss < 0 or p_win + p_loss > 1:
        raise ValueError("win/loss probabilities must be nonnegative and sum to at most one")


def displayed_ev(p_win: Decimal, p_loss: Decimal, decimal_price: Decimal) -> Decimal:
    _require_probabilities(p_win, p_loss)
    _require_price(decimal_price)
    return p_win * (decimal_price - Decimal(1)) - p_loss


def quarter_kelly(p_win: Decimal, p_loss: Decimal, decimal_price: Decimal) -> Decimal:
    _require_probabilities(p_win, p_loss)
    _require_price(decimal_price)
    b = decimal_price - Decimal(1)
    denominator = b * (p_win + p_loss)
    if denominator <= 0:
        return Decimal(0)
    return Decimal("0.25") * max(Decimal(0), (p_win * b - p_loss) / denominator)


def same_book_clv(decision_price: Decimal, close_price: Decimal) -> float:
    _require_price(decision_price)
    _require_price(close_price)
    return math.log(float(decision_price / close_price))
