"""Análisis técnico para un símbolo dado usando yfinance.

Función principal:
    analyze(symbol) -> dict con SMA20, SMA50, RSI14, MACD, soporte y resistencia.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

logger = logging.getLogger("bolsa.technical_analyzer")


def _sma(series: pd.Series, period: int) -> Optional[float]:
    if len(series) < period:
        return None
    return float(series.iloc[-period:].mean())


def _rsi(series: pd.Series, period: int = 14) -> Optional[float]:
    if len(series) < period + 1:
        return None
    delta = series.diff().dropna()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.iloc[:period].mean()
    avg_loss = loss.iloc[:period].mean()
    for i in range(period, len(delta)):
        avg_gain = (avg_gain * (period - 1) + gain.iloc[i]) / period
        avg_loss = (avg_loss * (period - 1) + loss.iloc[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100 - (100 / (1 + rs)))


def _macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    if len(series) < slow + signal:
        return {"macd_line": None, "signal_line": None, "histogram": None}
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return {
        "macd_line": float(macd_line.iloc[-1]),
        "signal_line": float(signal_line.iloc[-1]),
        "histogram": float(histogram.iloc[-1]),
    }


def analyze(symbol: str, period: str = "3mo") -> dict:
    """Descarga histórico de *symbol* y calcula indicadores técnicos.

    Args:
        symbol: Ticker de yfinance (p.ej. "SAN.MC").
        period: Período a descargar para cálculos (default "3mo").

    Returns:
        dict con claves: symbol, current_price, sma20, sma50, rsi14,
        macd_line, signal_line, macd_histogram, support, resistance.
    """
    try:
        hist = yf.download(symbol, period=period, progress=False, auto_adjust=True)
    except Exception as e:
        raise RuntimeError(f"Error descargando datos de {symbol}: {e}") from e

    if hist.empty:
        raise ValueError(f"No se obtuvieron datos para {symbol}")

    close = hist["Close"].squeeze()
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]

    result: dict = {"symbol": symbol}

    result["current_price"] = float(close.iloc[-1])
    result["sma20"] = _sma(close, 20)
    result["sma50"] = _sma(close, 50)
    result["rsi14"] = _rsi(close, 14)

    macd_data = _macd(close)
    result["macd_line"] = macd_data["macd_line"]
    result["signal_line"] = macd_data["signal_line"]
    result["macd_histogram"] = macd_data["histogram"]

    # Soporte y resistencia simples: mínimo y máximo de las últimas 20 velas
    window = close.iloc[-20:] if len(close) >= 20 else close
    result["support"] = float(window.min())
    result["resistance"] = float(window.max())

    return result
