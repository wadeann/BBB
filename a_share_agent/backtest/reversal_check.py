from __future__ import annotations


def run_reversal_check(provider, symbol: str, as_of: str) -> dict:
    """Fetch bars and run the 4-dimension reversal/rebound check.

    Parameters
    ----------
    provider : HistoricalDataProvider
        Data provider with a ``bars(symbol, count=…)`` method returning
        ``list[dict[str, Any]]`` in the format expected by
        ``four_dimension_reversal_check``.
    symbol : str
        Stock symbol / ticker.
    as_of : str
        As-of date string (YYYY-MM-DD).

    Returns
    -------
    dict
        The full result from ``four_dimension_reversal_check``.
    """
    from ..strategy.reversal import four_dimension_reversal_check

    bars = provider.bars(symbol, count=250)
    return four_dimension_reversal_check(bars, symbol, as_of)
