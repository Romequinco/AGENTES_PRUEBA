"""Tests unitarios para la Fase 3 — backtester, fundamental_analyzer, portfolio_tracker."""

import pytest
import pandas as pd
import numpy as np
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Backtester — validate_strategy
# ---------------------------------------------------------------------------

def test_validate_strategy_valid():
    from services.backtester import validate_strategy
    validate_strategy({
        "buy": {"indicator": "rsi", "operator": "below", "value": 30},
        "sell": {"indicator": "rsi", "operator": "above", "value": 70},
    })  # no debe lanzar


def test_validate_strategy_invalid_indicator():
    from services.backtester import validate_strategy
    with pytest.raises(ValueError) as exc_info:
        validate_strategy({
            "buy": {"indicator": "magia", "operator": "below", "value": 5},
            "sell": {"indicator": "rsi", "operator": "above", "value": 70},
        })
    assert "magia" in str(exc_info.value)
    # El mensaje debe listar los indicadores válidos
    assert "rsi" in str(exc_info.value)


def test_validate_strategy_invalid_operator():
    from services.backtester import validate_strategy
    with pytest.raises(ValueError):
        validate_strategy({
            "buy": {"indicator": "rsi", "operator": "equals", "value": 30},
            "sell": {"indicator": "rsi", "operator": "above", "value": 70},
        })


def test_validate_strategy_missing_keys():
    from services.backtester import validate_strategy
    with pytest.raises(ValueError):
        validate_strategy({"buy": {"indicator": "rsi", "operator": "below", "value": 30}})


def test_validate_strategy_all_valid_indicators():
    from services.backtester import validate_strategy, VALID_INDICATORS
    for ind in VALID_INDICATORS:
        validate_strategy({
            "buy": {"indicator": ind, "operator": "below", "value": 50},
            "sell": {"indicator": ind, "operator": "above", "value": 60},
        })


# ---------------------------------------------------------------------------
# Backtester — indicator series (con datos sintéticos, sin red)
# ---------------------------------------------------------------------------

@pytest.fixture
def synthetic_close():
    np.random.seed(99)
    n = 100
    return pd.Series(100 + np.cumsum(np.random.randn(n) * 0.5 + 0.05))


def test_rsi_series_range(synthetic_close):
    from services.backtester import _rsi_series
    rsi = _rsi_series(synthetic_close)
    valid = rsi.dropna()
    assert (valid >= 0).all() and (valid <= 100).all()


def test_sma20_series(synthetic_close):
    from services.backtester import _indicator_series
    sma = _indicator_series(synthetic_close, "sma20")
    # Los primeros 19 deben ser NaN
    assert sma.iloc[:19].isna().all()
    assert not pd.isna(sma.iloc[19])


def test_sma50_series(synthetic_close):
    from services.backtester import _indicator_series
    sma = _indicator_series(synthetic_close, "sma50")
    assert sma.iloc[:49].isna().all()
    assert not pd.isna(sma.iloc[49])


def test_macd_histogram_series(synthetic_close):
    from services.backtester import _indicator_series
    hist = _indicator_series(synthetic_close, "macd_histogram")
    assert len(hist) == len(synthetic_close)
    # Algunos valores deben ser no-nulos después de la inicialización
    assert hist.dropna().__len__() > 0


def test_price_series(synthetic_close):
    from services.backtester import _indicator_series
    price = _indicator_series(synthetic_close, "price")
    pd.testing.assert_series_equal(price, synthetic_close)


def test_invalid_indicator_raises(synthetic_close):
    from services.backtester import _indicator_series
    with pytest.raises(ValueError):
        _indicator_series(synthetic_close, "unknown_indicator")


# ---------------------------------------------------------------------------
# Backtester — apply_operator
# ---------------------------------------------------------------------------

def test_operator_above():
    from services.backtester import _apply_operator
    s = pd.Series([10.0, 50.0, 80.0, 20.0])
    result = _apply_operator(s, "above", 30.0)
    assert list(result) == [False, True, True, False]


def test_operator_below():
    from services.backtester import _apply_operator
    s = pd.Series([10.0, 50.0, 80.0, 20.0])
    result = _apply_operator(s, "below", 30.0)
    assert list(result) == [True, False, False, True]


def test_operator_crosses_above():
    from services.backtester import _apply_operator
    # 25 → 35: crosses 30
    s = pd.Series([25.0, 35.0, 40.0, 28.0, 32.0])
    result = _apply_operator(s, "crosses_above", 30.0)
    assert list(result) == [False, True, False, False, True]


def test_operator_crosses_below():
    from services.backtester import _apply_operator
    # 35 → 25: crosses 30
    s = pd.Series([35.0, 25.0, 20.0, 32.0, 28.0])
    result = _apply_operator(s, "crosses_below", 30.0)
    assert list(result) == [False, True, False, False, True]


# ---------------------------------------------------------------------------
# Backtester — backtest con datos mockeados (sin red)
# ---------------------------------------------------------------------------

def _make_mock_hist(n=100, seed=42):
    np.random.seed(seed)
    dates = pd.date_range("2024-01-02", periods=n, freq="B")
    close = pd.Series(100 + np.cumsum(np.random.randn(n) * 0.5), index=dates, name="Close")
    df = pd.DataFrame({"Close": close})
    df.columns = pd.MultiIndex.from_tuples([("Close", "SAN.MC")])
    return df


@patch("yfinance.download")
def test_backtest_deterministic(mock_download):
    mock_download.return_value = _make_mock_hist()
    from services.backtester import backtest

    strategy = {
        "buy": {"indicator": "rsi", "operator": "below", "value": 45},
        "sell": {"indicator": "rsi", "operator": "above", "value": 55},
    }
    r1 = backtest("SAN.MC", strategy, days=80)
    r2 = backtest("SAN.MC", strategy, days=80)
    assert r1 == r2, "El backtest no es determinista"


@patch("yfinance.download")
def test_backtest_returns_expected_keys(mock_download):
    mock_download.return_value = _make_mock_hist()
    from services.backtester import backtest

    strategy = {
        "buy": {"indicator": "rsi", "operator": "below", "value": 45},
        "sell": {"indicator": "rsi", "operator": "above", "value": 55},
    }
    result = backtest("SAN.MC", strategy, days=80)
    for key in ("symbol", "days_tested", "total_trades", "win_rate",
                "total_return_pct", "max_drawdown_pct", "trades"):
        assert key in result, f"Falta clave: {key}"


@patch("yfinance.download")
def test_backtest_invalid_indicator_raises(mock_download):
    mock_download.return_value = _make_mock_hist()
    from services.backtester import backtest

    with pytest.raises(ValueError) as exc_info:
        backtest("SAN.MC", {
            "buy": {"indicator": "magia", "operator": "below", "value": 5},
            "sell": {"indicator": "rsi", "operator": "above", "value": 70},
        }, days=30)
    assert "magia" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Fundamental analyzer
# ---------------------------------------------------------------------------

def test_fundamental_data_structure():
    from services.fundamental_analyzer import fundamental_data, _FIELDS

    mock_info = {
        "trailingPE": 12.5,
        "dividendYield": 0.045,
        "returnOnEquity": 0.12,
        "revenueGrowth": 0.08,
        "debtToEquity": 1.5,
        "marketCap": 50_000_000_000,
    }
    with patch("yfinance.Ticker") as mock_ticker:
        mock_ticker.return_value.info = mock_info
        result = fundamental_data("SAN.MC")

    assert result["symbol"] == "SAN.MC"
    for field in _FIELDS:
        assert field in result


def test_fundamental_data_nulls_on_missing():
    from services.fundamental_analyzer import fundamental_data

    with patch("yfinance.Ticker") as mock_ticker:
        mock_ticker.return_value.info = {}
        result = fundamental_data("XYZ.MC")

    from services.fundamental_analyzer import _FIELDS
    for field in _FIELDS:
        assert result[field] is None


def test_fundamental_data_no_exception_on_error():
    from services.fundamental_analyzer import fundamental_data

    with patch("yfinance.Ticker") as mock_ticker:
        mock_ticker.side_effect = Exception("network error")
        result = fundamental_data("BROKEN.MC")

    from services.fundamental_analyzer import _FIELDS
    for field in _FIELDS:
        assert result[field] is None


def test_data_quality_score_full():
    from services.fundamental_analyzer import data_quality_score
    full = {
        "pe_ratio": 12.5, "dividend_yield": 0.04, "roe": 0.1,
        "revenue_growth": 0.05, "debt_to_equity": 1.2, "market_cap": 5e10,
    }
    assert data_quality_score(full) == 100.0


def test_data_quality_score_empty():
    from services.fundamental_analyzer import data_quality_score
    empty = {
        "pe_ratio": None, "dividend_yield": None, "roe": None,
        "revenue_growth": None, "debt_to_equity": None, "market_cap": None,
    }
    assert data_quality_score(empty) == 0.0


def test_data_quality_score_partial():
    from services.fundamental_analyzer import data_quality_score
    partial = {
        "pe_ratio": 12.5, "dividend_yield": None, "roe": 0.1,
        "revenue_growth": None, "debt_to_equity": None, "market_cap": 5e10,
    }
    score = data_quality_score(partial)
    assert 0 < score < 100
