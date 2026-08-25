"""Macro strategy facade; calculation bodies remain in the preserved production core."""
from ..production_core import (
    calc_mtf,
    compute_composite,
    bias_from_score,
    _calc_currency_score_only,
)
