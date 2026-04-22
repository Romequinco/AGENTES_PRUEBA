"""Backtester determinista para estrategias JSON sobre datos históricos de yfinance.

Formato de estrategia:
    {
        "buy":  {"indicator": "rsi", "operator": "below",  "value": 30},
        "sell": {"indicator": "rsi", "operator": "above",  "value": 70}
    }

Indicadores soportados: rsi, sma20, sma50, macd_histogram, price
Operadores soportados:  above, below, crosses_above, crosses_below
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd
import yfinance as yf

logger = logging.getLogger("bolsa.backtester")

VALID_INDICATORS = {"rsi", "sma20", "sma50", "macd_histogram", "price"}
VALID_OPERATORS = {"above", "below", "crosses_above", "crosses_below"}


# ---------------------------------------------------------------------------
# Strategy validation
# ---------------------------------------------------------------------------

def _validate_condition(cond: dict, side: str) -> None:
    ind = cond.get("indicator")
    op = cond.get("operator")
    val = cond.get("value")
    if ind not in VALID_INDICATORS:
        raise ValueError(
            f"Indicador '{ind}' no soportado en condición '{side}'. "
            f"Indicadores válidos: {sorted(VALID_INDICATORS)}"
        )
    if op not in VALID_OPERATORS:
        raise ValueError(
            f"Operador '{op}' no soportado. Operadores válidos: {sorted(VALID_OPERATORS)}"
        )
    if val is None:
        raise ValueError(f"Falta 'value' en la condición '{side}'")
    try:
        float(val)
    except (TypeError, ValueError):
        raise ValueError(f"'value' debe ser numérico en la condición '{side}'")


def validate_strategy(strategy: dict) -> None:
    """Lanza ValueError si el formato de estrategia no es válido."""
    if "buy" not in strategy or "sell" not in strategy:
        raise ValueError("La estrategia debe tener claves 'buy' y 'sell'")
    _validate_condition(strategy["buy"], "buy")
    _validate_condition(strategy["sell"], "sell")


# ---------------------------------------------------------------------------
# Indicator series computation (vectorized, deterministic)
# ---------------------------------------------------------------------------

def _rsi_series(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def _macd_histogram_series(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line - signal_line


def _indicator_series(close: pd.Series, indicator: str) -> pd.Series:
    if indicator == "rsi":
        return _rsi_series(close)
    elif indicator == "sma20":
        return close.rolling(20, min_periods=20).mean()
    elif indicator == "sma50":
        return close.rolling(50, min_periods=50).mean()
    elif indicator == "macd_histogram":
        return _macd_histogram_series(close)
    elif indicator == "price":
        return close
    raise ValueError(f"Indicador desconocido: {indicator}")


# ---------------------------------------------------------------------------
# Signal generation
# ---------------------------------------------------------------------------

def _apply_operator(series: pd.Series, operator: str, value: float) -> pd.Series:
    """Returns boolean Series with True where the condition fires."""
    if operator == "above":
        return series > value
    elif operator == "below":
        return series < value
    elif operator == "crosses_above":
        prev = series.shift(1)
        return (prev <= value) & (series > value)
    elif operator == "crosses_below":
        prev = series.shift(1)
        return (prev >= value) & (series < value)
    raise ValueError(f"Operador desconocido: {operator}")


# ---------------------------------------------------------------------------
# Core backtest engine
# ---------------------------------------------------------------------------

def backtest(symbol: str, strategy_dict: dict, days: int = 180) -> dict:
    """Ejecuta un backtest sobre datos históricos de yfinance.

    Args:
        symbol: Ticker (p.ej. 'SAN.MC').
        strategy_dict: Estrategia en formato JSON con claves 'buy' y 'sell'.
        days: Número de días de historial a utilizar.

    Returns:
        dict con: total_trades, win_rate, total_return_pct, max_drawdown_pct,
        trades (lista de dicts con entry_date, exit_date, entry_price,
        exit_price, pnl_pct).

    Raises:
        ValueError: Si la estrategia contiene indicadores u operadores inválidos.
    """
    validate_strategy(strategy_dict)

    buy_cond = strategy_dict["buy"]
    sell_cond = strategy_dict["sell"]

    # Download historical data — use a period string derived from days
    period_map = {30: "1mo", 60: "3mo", 90: "3mo", 120: "6mo", 180: "6mo", 365: "1y"}
    period = "1y"
    for threshold in sorted(period_map.keys()):
        if days <= threshold:
            period = period_map[threshold]
            break
    if days > 365:
        period = f"{(days // 365) + 1}y"

    try:
        hist = yf.download(symbol, period=period, progress=False, auto_adjust=True)
    except Exception as e:
        raise RuntimeError(f"Error descargando datos de {symbol}: {e}") from e

    if hist.empty:
        raise ValueError(f"No se obtuvieron datos para {symbol}")

    close = hist["Close"].squeeze()
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]

    # Restrict to the requested number of trading days
    if len(close) > days:
        close = close.iloc[-days:]

    # Compute indicator series
    buy_ind = _indicator_series(close, buy_cond["indicator"])
    sell_ind = _indicator_series(close, sell_cond["indicator"])

    buy_signal = _apply_operator(buy_ind, buy_cond["operator"], float(buy_cond["value"]))
    sell_signal = _apply_operator(sell_ind, sell_cond["operator"], float(sell_cond["value"]))

    # Simulate trades (use closing prices; enter/exit at close of signal day)
    trades: list[dict[str, Any]] = []
    in_position = False
    entry_price: float = 0.0
    entry_date: Any = None
    equity_curve: list[float] = [1.0]

    for i in range(len(close)):
        idx = close.index[i]
        price = float(close.iloc[i])

        if not in_position:
            if buy_signal.iloc[i]:
                in_position = True
                entry_price = price
                entry_date = idx
        else:
            if sell_signal.iloc[i]:
                pnl_pct = (price - entry_price) / entry_price * 100
                trades.append({
                    "entry_date": str(entry_date.date() if hasattr(entry_date, "date") else entry_date),
                    "exit_date": str(idx.date() if hasattr(idx, "date") else idx),
                    "entry_price": round(entry_price, 4),
                    "exit_price": round(price, 4),
                    "pnl_pct": round(pnl_pct, 4),
                })
                in_position = False
                equity_curve.append(equity_curve[-1] * (1 + pnl_pct / 100))

    total_trades = len(trades)
    if total_trades == 0:
        return {
            "symbol": symbol,
            "days_tested": days,
            "total_trades": 0,
            "win_rate": None,
            "total_return_pct": None,
            "max_drawdown_pct": None,
            "trades": [],
        }

    winning_trades = sum(1 for t in trades if t["pnl_pct"] > 0)
    win_rate = round(winning_trades / total_trades * 100, 2)

    total_return_pct = round((equity_curve[-1] - 1) * 100, 4)

    # Max drawdown from peak
    eq = np.array(equity_curve)
    peak = np.maximum.accumulate(eq)
    drawdown = (eq - peak) / peak
    max_drawdown_pct = round(float(drawdown.min()) * 100, 4)

    return {
        "symbol": symbol,
        "days_tested": days,
        "total_trades": total_trades,
        "win_rate": win_rate,
        "total_return_pct": total_return_pct,
        "max_drawdown_pct": max_drawdown_pct,
        "trades": trades,
    }
