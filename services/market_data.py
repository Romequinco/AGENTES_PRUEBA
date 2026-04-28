"""Datos de mercado global con caché en PostgreSQL.

Fuentes por prioridad: Finnhub → yfinance → Alpha Vantage (stocks/índices/commodities),
CoinGecko (crypto, sin API key).

Funciones públicas:
    get_quote(symbol, asset_type) -> dict | None
    get_global_snapshot() -> dict
    get_historical(symbol, period) -> list | None
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger("bolsa.market_data")

CACHE_TTL_SECONDS: dict[str, int] = {
    "index":      900,    # 15 min
    "stock":      900,    # 15 min
    "commodity":  600,    # 10 min
    "crypto":     300,    # 5 min
    "historical": 86400,  # 24 h — datos diarios no cambian intradía
}

_PERIOD_TO_DAYS: dict[str, int] = {
    "1m": 22, "3m": 65, "6m": 130, "1y": 252,
}
_PERIOD_TO_YFINANCE: dict[str, str] = {
    "1m": "1mo", "3m": "3mo", "6m": "6mo", "1y": "1y",
}

INDICES = [
    ("^GSPC", "S&P 500"),
    ("^IXIC", "Nasdaq"),
    ("^GDAXI", "DAX"),
    ("^N225",  "Nikkei 225"),
]
COMMODITIES = [
    ("GC=F", "Gold"),
    ("CL=F", "Crude Oil"),
]
CRYPTO_ASSETS = [
    ("bitcoin",      "BTC",  "Bitcoin"),
    ("ethereum",     "ETH",  "Ethereum"),
    ("tether",       "USDT", "Tether"),
    ("binancecoin",  "BNB",  "BNB"),
    ("solana",       "SOL",  "Solana"),
]
VIX_SYMBOL = "^VIX"
_COINGECKO_CACHE_SYMBOL = "_coingecko_top5"


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _get_cached(symbol: str, asset_type: str) -> Optional[dict]:
    """Returns fresh cached data for symbol, or None if missing/expired."""
    try:
        from db.models import get_db_session, MarketDataCache

        ttl = CACHE_TTL_SECONDS.get(asset_type, 900)
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=ttl)

        db = get_db_session()
        try:
            row = (
                db.query(MarketDataCache)
                .filter(
                    MarketDataCache.symbol == symbol,
                    MarketDataCache.asset_type == asset_type,
                    MarketDataCache.fetched_at >= cutoff,
                )
                .order_by(MarketDataCache.fetched_at.desc())
                .first()
            )
            return row.data if row else None
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"[MARKET] Cache read error for {symbol}: {e}")
        return None


def _set_cached(symbol: str, asset_type: str, data: dict) -> None:
    """Replaces all cache entries for (symbol, asset_type) with fresh data."""
    try:
        from db.models import get_db_session, MarketDataCache

        db = get_db_session()
        try:
            db.query(MarketDataCache).filter(
                MarketDataCache.symbol == symbol,
                MarketDataCache.asset_type == asset_type,
            ).delete()
            db.add(MarketDataCache(symbol=symbol, asset_type=asset_type, data=data))
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"[MARKET] Cache write error for {symbol}: {e}")


# ---------------------------------------------------------------------------
# Data sources
# ---------------------------------------------------------------------------

def _quote_from_finnhub(symbol: str) -> Optional[dict]:
    """Fetches a real-time quote from Finnhub. Returns None if key missing or data invalid."""
    api_key = os.environ.get("FINNHUB_API_KEY")
    if not api_key:
        logger.warning("[MARKET] FINNHUB_API_KEY not set — skipping Finnhub")
        return None

    try:
        import requests

        resp = requests.get(
            "https://finnhub.io/api/v1/quote",
            params={"symbol": symbol, "token": api_key},
            timeout=5,
        )
        resp.raise_for_status()
        q = resp.json()

        if not q or q.get("c", 0) == 0:
            return None

        prev = q.get("pc") or q["c"]
        change_pct = ((q["c"] - prev) / prev * 100) if prev else 0.0

        return {
            "price":      round(q["c"], 4),
            "change_pct": round(change_pct, 2),
            "high":       q.get("h"),
            "low":        q.get("l"),
            "prev_close": round(prev, 4),
            "source":     "finnhub",
        }
    except Exception as e:
        logger.warning(f"[MARKET] Finnhub error for {symbol}: {e}")
        return None


def _quote_from_yfinance(symbol: str) -> Optional[dict]:
    """Fetches a quote from yfinance as fallback."""
    try:
        import yfinance as yf

        info = yf.Ticker(symbol).fast_info
        price = info.last_price
        prev  = info.previous_close

        if not price:
            return None

        change_pct = ((price - prev) / prev * 100) if prev else 0.0

        return {
            "price":      round(float(price), 4),
            "change_pct": round(float(change_pct), 2),
            "high":       round(float(info.day_high), 4) if info.day_high else None,
            "low":        round(float(info.day_low), 4) if info.day_low else None,
            "prev_close": round(float(prev), 4) if prev else None,
            "source":     "yfinance",
        }
    except Exception as e:
        logger.warning(f"[MARKET] yfinance error for {symbol}: {e}")
        return None


def _crypto_from_coingecko(coin_ids: list[str]) -> dict:
    """Fetches USD prices + 24h change for multiple coins from CoinGecko public API."""
    try:
        import requests

        resp = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={
                "ids":              ",".join(coin_ids),
                "vs_currencies":    "usd",
                "include_24hr_change": "true",
            },
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning(f"[MARKET] CoinGecko error: {e}")
        return {}


def _quote_from_alphavantage(symbol: str) -> Optional[dict]:
    """Fetches a real-time quote from Alpha Vantage GLOBAL_QUOTE endpoint.

    Used only as last resort (after Finnhub and yfinance both fail).
    Logs a WARNING every time it executes to monitor quota consumption
    (free tier: 25 req/day).
    Returns None if ALPHA_VANTAGE_API_KEY is not set or data is invalid.
    """
    api_key = os.environ.get("ALPHA_VANTAGE_API_KEY")
    if not api_key:
        return None

    try:
        import requests

        resp = requests.get(
            "https://www.alphavantage.co/query",
            params={"function": "GLOBAL_QUOTE", "symbol": symbol, "apikey": api_key},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        q = data.get("Global Quote", {})
        price_str = q.get("05. price")
        if not price_str:
            logger.warning(f"[MARKET] Alpha Vantage GLOBAL_QUOTE: no data for {symbol}")
            return None

        logger.warning(
            f"[MARKET] Alpha Vantage quota used for {symbol} — check daily limit (25 req/day)"
        )

        price = float(price_str)
        prev  = float(q.get("08. previous close") or price)
        change_pct = ((price - prev) / prev * 100) if prev else 0.0

        return {
            "price":      round(price, 4),
            "change_pct": round(change_pct, 2),
            "high":       round(float(q["03. high"]), 4) if q.get("03. high") else None,
            "low":        round(float(q["04. low"]), 4) if q.get("04. low") else None,
            "prev_close": round(prev, 4),
            "source":     "alphavantage",
        }
    except Exception as e:
        logger.warning(f"[MARKET] Alpha Vantage quote error for {symbol}: {e}")
        return None


def _historical_from_alphavantage(symbol: str) -> Optional[list]:
    """Fetches TIME_SERIES_DAILY (last 100 trading days) from Alpha Vantage.

    Returns a list sorted ascending by date, or None on any error/missing key.
    """
    api_key = os.environ.get("ALPHA_VANTAGE_API_KEY")
    if not api_key:
        return None

    try:
        import requests

        resp = requests.get(
            "https://www.alphavantage.co/query",
            params={
                "function":   "TIME_SERIES_DAILY",
                "symbol":     symbol,
                "outputsize": "compact",
                "apikey":     api_key,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        if "Error Message" in data or "Note" in data:
            msg = data.get("Error Message") or data.get("Note")
            logger.warning(f"[MARKET] Alpha Vantage TIME_SERIES_DAILY for {symbol}: {msg}")
            return None

        raw = data.get("Time Series (Daily)", {})
        if not raw:
            return None

        return [
            {
                "date":   d,
                "open":   round(float(v["1. open"]),  4),
                "high":   round(float(v["2. high"]),  4),
                "low":    round(float(v["3. low"]),   4),
                "close":  round(float(v["4. close"]), 4),
                "volume": int(float(v["5. volume"])),
            }
            for d, v in sorted(raw.items())  # ascending by date
        ]
    except Exception as e:
        logger.warning(f"[MARKET] Alpha Vantage historical error for {symbol}: {e}")
        return None


def _historical_from_yfinance(symbol: str, period: str) -> Optional[list]:
    """Fetches historical OHLCV data from yfinance as fallback for get_historical."""
    try:
        import yfinance as yf

        yf_period = _PERIOD_TO_YFINANCE.get(period, "1mo")
        hist = yf.Ticker(symbol).history(period=yf_period)

        if hist.empty:
            return None

        return [
            {
                "date":   dt.strftime("%Y-%m-%d"),
                "open":   round(float(row["Open"]),  4),
                "high":   round(float(row["High"]),  4),
                "low":    round(float(row["Low"]),   4),
                "close":  round(float(row["Close"]), 4),
                "volume": int(row["Volume"]),
            }
            for dt, row in hist.iterrows()
        ]
    except Exception as e:
        logger.warning(f"[MARKET] yfinance historical error for {symbol}: {e}")
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_quote(symbol: str, asset_type: str = "index") -> Optional[dict]:
    """Returns a quote using cache → Finnhub → yfinance → Alpha Vantage fallback chain."""
    cached = _get_cached(symbol, asset_type)
    if cached:
        return {**cached, "cached": True}

    data: Optional[dict] = None
    try:
        data = _quote_from_finnhub(symbol)
    except Exception as e:
        logger.warning(f"[MARKET] Finnhub raised for {symbol}: {e}")

    if not data:
        try:
            data = _quote_from_yfinance(symbol)
        except Exception as e:
            logger.warning(f"[MARKET] yfinance raised for {symbol}: {e}")

    if not data:
        try:
            data = _quote_from_alphavantage(symbol)
        except Exception as e:
            logger.warning(f"[MARKET] Alpha Vantage raised for {symbol}: {e}")

    if data:
        _set_cached(symbol, asset_type, data)
    return data


def get_historical(symbol: str, period: str = "1m") -> Optional[list]:
    """Returns OHLCV history for symbol using cache → Alpha Vantage → yfinance fallback.

    period: "1m" (default), "3m", "6m", "1y"
    Returns a list of {date, open, high, low, close, volume} sorted ascending, or None.
    Alpha Vantage returns the last 100 trading days; result is sliced to the requested period.
    TTL: 86400 s (24 h) — daily bars don't change intradía.
    """
    cached = _get_cached(symbol, "historical")
    if cached:
        days = _PERIOD_TO_DAYS.get(period, 22)
        return cached[-days:]

    data: Optional[list] = None
    try:
        data = _historical_from_alphavantage(symbol)
    except Exception as e:
        logger.warning(f"[MARKET] Alpha Vantage historical raised for {symbol}: {e}")

    if not data:
        try:
            data = _historical_from_yfinance(symbol, period)
        except Exception as e:
            logger.warning(f"[MARKET] yfinance historical raised for {symbol}: {e}")

    if data:
        _set_cached(symbol, "historical", data)
        days = _PERIOD_TO_DAYS.get(period, 22)
        return data[-days:]
    return None


def get_global_snapshot() -> dict:
    """Returns a snapshot of global markets: indices, crypto, commodities, VIX."""
    now = datetime.now(timezone.utc).isoformat()

    indices_result: list[dict] = []
    for sym, name in INDICES:
        try:
            q = get_quote(sym, "index")
            if q:
                indices_result.append({"symbol": sym, "name": name, **q})
        except Exception as e:
            logger.warning(f"[MARKET] Index {sym} failed: {e}")

    commodities_result: list[dict] = []
    for sym, name in COMMODITIES:
        try:
            q = get_quote(sym, "commodity")
            if q:
                commodities_result.append({"symbol": sym, "name": name, **q})
        except Exception as e:
            logger.warning(f"[MARKET] Commodity {sym} failed: {e}")

    vix_result: Optional[dict] = None
    try:
        q = get_quote(VIX_SYMBOL, "index")
        if q:
            vix_result = {"value": q["price"], "source": q.get("source")}
    except Exception as e:
        logger.warning(f"[MARKET] VIX failed: {e}")

    crypto_result: list[dict] = []
    try:
        raw = _get_cached(_COINGECKO_CACHE_SYMBOL, "crypto")
        if not raw:
            raw = _crypto_from_coingecko([c[0] for c in CRYPTO_ASSETS])
            if raw:
                _set_cached(_COINGECKO_CACHE_SYMBOL, "crypto", raw)

        for coin_id, ticker, name in CRYPTO_ASSETS:
            if coin_id in raw:
                d = raw[coin_id]
                crypto_result.append({
                    "symbol":        ticker,
                    "name":          name,
                    "price_usd":     d.get("usd"),
                    "change_24h_pct": round(d.get("usd_24h_change", 0.0), 2),
                })
    except Exception as e:
        logger.warning(f"[MARKET] Crypto batch failed: {e}")

    return {
        "indices":      indices_result,
        "crypto":       crypto_result,
        "commodities":  commodities_result,
        "vix":          vix_result,
        "fetched_at":   now,
        "data_quality": {
            "indices":     len(indices_result),
            "crypto":      len(crypto_result),
            "commodities": len(commodities_result),
        },
    }
