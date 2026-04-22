"""Análisis fundamental de un símbolo usando yfinance.

Si un campo no está disponible para el símbolo, devuelve null en el dict.
Nunca lanza excepción por campos ausentes — el caller decide qué hacer con nulls.
"""

from __future__ import annotations

import logging
from typing import Optional

import yfinance as yf

logger = logging.getLogger("bolsa.fundamental_analyzer")

_FIELDS = ["pe_ratio", "dividend_yield", "roe", "revenue_growth", "debt_to_equity", "market_cap"]

_YFINANCE_KEY_MAP = {
    "pe_ratio": "trailingPE",
    "dividend_yield": "dividendYield",
    "roe": "returnOnEquity",
    "revenue_growth": "revenueGrowth",
    "debt_to_equity": "debtToEquity",
    "market_cap": "marketCap",
}


def fundamental_data(symbol: str) -> dict:
    """Descarga datos fundamentales de *symbol* via yfinance.

    Returns:
        dict con claves: pe_ratio, dividend_yield, roe, revenue_growth,
        debt_to_equity, market_cap. Los campos no disponibles son None.
    """
    result: dict = {"symbol": symbol}
    try:
        info = yf.Ticker(symbol).info or {}
    except Exception as e:
        logger.warning(f"Error descargando info de {symbol}: {e}")
        info = {}

    for field, yf_key in _YFINANCE_KEY_MAP.items():
        raw = info.get(yf_key)
        # yfinance a veces devuelve 0 para campos ausentes (ej: dividendYield=0)
        # Tratamos 0 como válido para market_cap y volúmenes, pero como None para ratios
        result[field] = _clean_value(field, raw)

    return result


def _clean_value(field: str, value) -> Optional[float]:
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    # NaN → None
    if v != v:
        return None
    return v


def data_quality_score(fundamental_dict: dict) -> float:
    """Devuelve el porcentaje de campos no-null en el diccionario fundamental.

    Args:
        fundamental_dict: dict devuelto por fundamental_data().

    Returns:
        float entre 0 y 100 (porcentaje de campos con datos).
    """
    values = [fundamental_dict.get(f) for f in _FIELDS]
    non_null = sum(1 for v in values if v is not None)
    return round(non_null / len(_FIELDS) * 100, 1)
