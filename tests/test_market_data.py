"""Tests para services/market_data.py — sin llamadas reales a APIs ni a la DB."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Sample data fixtures
# ---------------------------------------------------------------------------

_AV_QUOTE_DATA = {
    "price":      5195.30,
    "change_pct": 0.75,
    "high":       5215.0,
    "low":        5175.0,
    "prev_close": 5156.5,
    "source":     "alphavantage",
}

_AV_HISTORICAL_DATA = [
    {"date": "2026-03-01", "open": 5100.0, "high": 5120.0, "low": 5090.0, "close": 5110.0, "volume": 1_000_000},
    {"date": "2026-03-02", "open": 5110.0, "high": 5130.0, "low": 5100.0, "close": 5125.0, "volume": 1_100_000},
    {"date": "2026-03-03", "open": 5125.0, "high": 5140.0, "low": 5115.0, "close": 5135.0, "volume": 900_000},
]

_YFINANCE_HISTORICAL_DATA = [
    {"date": "2026-03-01", "open": 5100.0, "high": 5120.0, "low": 5090.0, "close": 5110.0, "volume": 1_000_000},
    {"date": "2026-03-02", "open": 5110.0, "high": 5130.0, "low": 5100.0, "close": 5125.0, "volume": 1_100_000},
]

_FINNHUB_DATA = {
    "price":      5200.10,
    "change_pct": 0.85,
    "high":       5220.0,
    "low":        5180.0,
    "prev_close": 5156.0,
    "source":     "finnhub",
}

_YFINANCE_DATA = {
    "price":      5190.50,
    "change_pct": 0.65,
    "high":       5210.0,
    "low":        5170.0,
    "prev_close": 5156.0,
    "source":     "yfinance",
}

_COINGECKO_DATA = {
    "bitcoin":     {"usd": 67000, "usd_24h_change": 1.5},
    "ethereum":    {"usd": 3500,  "usd_24h_change": -0.3},
    "tether":      {"usd": 1.0,   "usd_24h_change": 0.01},
    "binancecoin": {"usd": 580,   "usd_24h_change": 2.1},
    "solana":      {"usd": 160,   "usd_24h_change": -1.2},
}


# ---------------------------------------------------------------------------
# TestFinnhubFallback
# ---------------------------------------------------------------------------

class TestFinnhubFallback:

    @patch("services.market_data._set_cached")
    @patch("services.market_data._get_cached", return_value=None)
    @patch("services.market_data._quote_from_yfinance")
    @patch("services.market_data._quote_from_finnhub", return_value=_FINNHUB_DATA)
    def test_quote_returns_finnhub_data_when_api_succeeds(
        self, mock_finnhub, mock_yfinance, mock_get_cache, mock_set_cache
    ):
        from services.market_data import get_quote

        result = get_quote("^GSPC", "index")

        assert result is not None
        assert result["price"] == 5200.10
        assert result["source"] == "finnhub"
        mock_yfinance.assert_not_called()
        mock_set_cache.assert_called_once_with("^GSPC", "index", _FINNHUB_DATA)

    @patch("services.market_data._set_cached")
    @patch("services.market_data._get_cached", return_value=None)
    @patch("services.market_data._quote_from_yfinance", return_value=_YFINANCE_DATA)
    @patch("services.market_data._quote_from_finnhub", return_value=None)
    def test_quote_falls_back_to_yfinance_when_finnhub_returns_none(
        self, mock_finnhub, mock_yfinance, mock_get_cache, mock_set_cache
    ):
        from services.market_data import get_quote

        result = get_quote("^GSPC", "index")

        assert result["source"] == "yfinance"
        mock_finnhub.assert_called_once_with("^GSPC")
        mock_yfinance.assert_called_once_with("^GSPC")

    @patch("services.market_data._set_cached")
    @patch("services.market_data._get_cached", return_value=None)
    @patch("services.market_data._quote_from_yfinance", return_value=_YFINANCE_DATA)
    @patch("services.market_data._quote_from_finnhub", side_effect=Exception("connection timeout"))
    def test_quote_falls_back_to_yfinance_when_finnhub_raises(
        self, mock_finnhub, mock_yfinance, mock_get_cache, mock_set_cache
    ):
        from services.market_data import get_quote

        result = get_quote("^GSPC", "index")

        assert result is not None
        assert result["source"] == "yfinance"


# ---------------------------------------------------------------------------
# TestCache
# ---------------------------------------------------------------------------

class TestCache:

    @patch("services.market_data._set_cached")
    @patch("services.market_data._quote_from_yfinance")
    @patch("services.market_data._quote_from_finnhub")
    @patch("services.market_data._get_cached", return_value=_FINNHUB_DATA)
    def test_cache_hit_skips_api_calls(
        self, mock_get_cache, mock_finnhub, mock_yfinance, mock_set_cache
    ):
        from services.market_data import get_quote

        result = get_quote("^GSPC", "index")

        assert result["price"] == _FINNHUB_DATA["price"]
        mock_finnhub.assert_not_called()
        mock_yfinance.assert_not_called()
        mock_set_cache.assert_not_called()

    @patch("services.market_data._set_cached")
    @patch("services.market_data._get_cached", return_value=None)
    @patch("services.market_data._quote_from_yfinance", return_value=_YFINANCE_DATA)
    @patch("services.market_data._quote_from_finnhub", return_value=None)
    def test_cache_miss_triggers_api_call_and_stores_result(
        self, mock_finnhub, mock_yfinance, mock_get_cache, mock_set_cache
    ):
        from services.market_data import get_quote

        get_quote("^GSPC", "index")

        mock_get_cache.assert_called_once_with("^GSPC", "index")
        mock_set_cache.assert_called_once_with("^GSPC", "index", _YFINANCE_DATA)


# ---------------------------------------------------------------------------
# TestGlobalSnapshot
# ---------------------------------------------------------------------------

class TestGlobalSnapshot:

    @patch("services.market_data._set_cached")
    @patch("services.market_data._get_cached", return_value=None)
    @patch("services.market_data._crypto_from_coingecko", return_value=_COINGECKO_DATA)
    @patch("services.market_data._quote_from_yfinance", return_value=None)
    @patch("services.market_data._quote_from_finnhub", return_value=_FINNHUB_DATA)
    def test_global_snapshot_has_expected_keys(
        self, mock_finnhub, mock_yfinance, mock_coingecko, mock_get_cache, mock_set_cache
    ):
        from services.market_data import get_global_snapshot

        snap = get_global_snapshot()

        assert "indices"      in snap
        assert "crypto"       in snap
        assert "commodities"  in snap
        assert "vix"          in snap
        assert "fetched_at"   in snap
        assert "data_quality" in snap
        assert isinstance(snap["indices"], list)
        assert isinstance(snap["crypto"], list)

    @patch("services.market_data._set_cached")
    @patch("services.market_data._get_cached", return_value=None)
    @patch("services.market_data._crypto_from_coingecko", return_value={})
    @patch("services.market_data._quote_from_yfinance", return_value=None)
    @patch("services.market_data._quote_from_finnhub", return_value=None)
    def test_partial_failure_does_not_crash_and_reports_zero_quality(
        self, mock_finnhub, mock_yfinance, mock_coingecko, mock_get_cache, mock_set_cache
    ):
        from services.market_data import get_global_snapshot

        snap = get_global_snapshot()

        assert isinstance(snap, dict)
        assert snap["data_quality"]["indices"]     == 0
        assert snap["data_quality"]["crypto"]      == 0
        assert snap["data_quality"]["commodities"] == 0
        assert snap["vix"] is None

    @patch("services.market_data._set_cached")
    @patch("services.market_data._get_cached", return_value=None)
    @patch("services.market_data._crypto_from_coingecko", return_value=_COINGECKO_DATA)
    @patch("services.market_data._quote_from_yfinance", return_value=None)
    @patch("services.market_data._quote_from_finnhub", return_value=_FINNHUB_DATA)
    def test_global_snapshot_crypto_count(
        self, mock_finnhub, mock_yfinance, mock_coingecko, mock_get_cache, mock_set_cache
    ):
        from services.market_data import get_global_snapshot

        snap = get_global_snapshot()

        assert snap["data_quality"]["crypto"] == 5
        symbols = [c["symbol"] for c in snap["crypto"]]
        assert "BTC" in symbols
        assert "ETH" in symbols


# ---------------------------------------------------------------------------
# TestAlphaVantageFallback
# ---------------------------------------------------------------------------

class TestAlphaVantageFallback:

    @patch("services.market_data._set_cached")
    @patch("services.market_data._get_cached", return_value=None)
    @patch("services.market_data._quote_from_alphavantage")
    @patch("services.market_data._quote_from_yfinance", return_value=_YFINANCE_DATA)
    @patch("services.market_data._quote_from_finnhub", return_value=None)
    def test_alphavantage_only_called_as_last_resort(
        self, mock_finnhub, mock_yfinance, mock_av, mock_get_cache, mock_set_cache
    ):
        """Alpha Vantage must NOT be called when yfinance succeeds."""
        from services.market_data import get_quote

        result = get_quote("IBM", "stock")

        assert result["source"] == "yfinance"
        mock_av.assert_not_called()

    @patch("services.market_data._set_cached")
    @patch("services.market_data._get_cached", return_value=None)
    @patch("services.market_data._quote_from_alphavantage", return_value=_AV_QUOTE_DATA)
    @patch("services.market_data._quote_from_yfinance", return_value=None)
    @patch("services.market_data._quote_from_finnhub", return_value=None)
    def test_alphavantage_called_when_both_fail(
        self, mock_finnhub, mock_yfinance, mock_av, mock_get_cache, mock_set_cache
    ):
        """Alpha Vantage MUST be called when Finnhub and yfinance both return None."""
        from services.market_data import get_quote

        result = get_quote("IBM", "stock")

        assert result is not None
        assert result["source"] == "alphavantage"
        mock_av.assert_called_once_with("IBM")


# ---------------------------------------------------------------------------
# TestGetHistorical
# ---------------------------------------------------------------------------

class TestGetHistorical:

    @patch("services.market_data._set_cached")
    @patch("services.market_data._get_cached", return_value=None)
    @patch("services.market_data._historical_from_yfinance")
    @patch("services.market_data._historical_from_alphavantage", return_value=_AV_HISTORICAL_DATA)
    def test_get_historical_returns_list_with_correct_keys(
        self, mock_av, mock_yfinance, mock_get_cache, mock_set_cache
    ):
        """get_historical returns a list of OHLCV dicts with expected keys."""
        from services.market_data import get_historical

        result = get_historical("^GSPC", "1m")

        assert isinstance(result, list)
        assert len(result) > 0
        required_keys = {"date", "open", "high", "low", "close", "volume"}
        assert required_keys.issubset(result[0].keys())
        mock_yfinance.assert_not_called()

    @patch("services.market_data._set_cached")
    @patch("services.market_data._get_cached", return_value=None)
    @patch("services.market_data._historical_from_yfinance", return_value=_YFINANCE_HISTORICAL_DATA)
    @patch("services.market_data._historical_from_alphavantage", return_value=None)
    def test_get_historical_fallback_to_yfinance_when_av_fails(
        self, mock_av, mock_yfinance, mock_get_cache, mock_set_cache
    ):
        """When Alpha Vantage returns None, yfinance is used as fallback."""
        from services.market_data import get_historical

        result = get_historical("^GSPC", "1m")

        assert result is not None
        assert len(result) > 0
        mock_av.assert_called_once_with("^GSPC")
        mock_yfinance.assert_called_once_with("^GSPC", "1m")
