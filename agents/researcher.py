import os
import json
import time
import logging
import calendar
import re
from datetime import datetime
from email.utils import parsedate_to_datetime

import numpy as np
import pytz
import pandas as pd
import feedparser
import requests
import yfinance as yf

from agents.ibex_data import get_ibex35_components
from agents.utils import MADRID_TZ

logger = logging.getLogger("bolsa.researcher")

_SCRAPER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

# Días de histórico para calcular indicadores técnicos (MA50 necesita mínimo 50)
_HISTORY_DAYS = 60


# =============================================================================
# Indicadores técnicos (pandas/numpy puro, sin dependencias extra)
# =============================================================================

def _rsi(close: pd.Series, period: int = 14) -> float | None:
    if len(close) < period + 1:
        return None
    delta = close.diff().dropna()
    gain = delta.clip(lower=0).ewm(com=period - 1, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(com=period - 1, min_periods=period).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    val = rsi.iloc[-1]
    return round(float(val), 2) if pd.notna(val) else None


def _sma(close: pd.Series, period: int) -> float | None:
    if len(close) < period:
        return None
    val = close.tail(period).mean()
    return round(float(val), 4) if pd.notna(val) else None



def _macd(close: pd.Series) -> dict:
    """Devuelve MACD(12,26), señal(9) e histograma."""
    if len(close) < 26:
        return {"macd": None, "signal": None, "histogram": None}
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    hist = macd_line - signal_line
    return {
        "macd": round(float(macd_line.iloc[-1]), 4) if pd.notna(macd_line.iloc[-1]) else None,
        "signal": round(float(signal_line.iloc[-1]), 4) if pd.notna(signal_line.iloc[-1]) else None,
        "histogram": round(float(hist.iloc[-1]), 4) if pd.notna(hist.iloc[-1]) else None,
    }


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> float | None:
    if len(close) < period + 1:
        return None
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(com=period - 1, min_periods=period).mean().iloc[-1]
    return round(float(atr), 4) if pd.notna(atr) else None


def _bollinger(close: pd.Series, period: int = 20, std_factor: float = 2.0) -> dict:
    if len(close) < period:
        return {"upper": None, "middle": None, "lower": None, "bandwidth": None}
    rolling = close.tail(period)
    mid = rolling.mean()
    std = rolling.std(ddof=1)
    upper = mid + std_factor * std
    lower = mid - std_factor * std
    bandwidth = round(float((upper - lower) / mid * 100), 2) if mid != 0 else None
    return {
        "upper": round(float(upper), 4),
        "middle": round(float(mid), 4),
        "lower": round(float(lower), 4),
        "bandwidth": bandwidth,
    }


def _compute_indicators(hist: pd.DataFrame) -> dict:
    """Calcula todos los indicadores a partir del DataFrame histórico (OHLCV)."""
    close = hist["Close"].dropna()
    high = hist["High"].dropna()
    low = hist["Low"].dropna()
    volume = hist["Volume"].dropna()

    macd_data = _macd(close)
    bb = _bollinger(close)

    # Señal cualitativa MACD
    macd_signal = None
    if macd_data["histogram"] is not None:
        macd_signal = "alcista" if macd_data["histogram"] > 0 else "bajista"

    # Posición precio respecto a medias
    price = float(close.iloc[-1]) if len(close) > 0 else None
    ma20 = _sma(close, 20)
    ma50 = _sma(close, 50)
    price_vs_ma = None
    if price and ma20 and ma50:
        if price > ma20 > ma50:
            price_vs_ma = "por encima de MA20 y MA50 (tendencia alcista)"
        elif price < ma20 < ma50:
            price_vs_ma = "por debajo de MA20 y MA50 (tendencia bajista)"
        elif price > ma50:
            price_vs_ma = "por encima de MA50, mixto respecto a MA20"
        else:
            price_vs_ma = "por debajo de MA50"

    rsi_val = _rsi(close)
    rsi_signal = None
    if rsi_val is not None:
        if rsi_val > 70:
            rsi_signal = "sobrecomprado"
        elif rsi_val < 30:
            rsi_signal = "sobrevendido"
        else:
            rsi_signal = "neutral"

    # Análisis de volumen: ratio vs media 20 días
    avg_vol_20d = None
    vol_ratio = None
    vol_signal = "normal"
    if len(volume) >= 21:
        avg_vol_20d = int(volume.iloc[-21:-1].mean())  # últimos 20 días excluyendo hoy
        today_vol = int(volume.iloc[-1]) if pd.notna(volume.iloc[-1]) else None
        if today_vol and avg_vol_20d > 0:
            vol_ratio = round(today_vol / avg_vol_20d, 2)
            if vol_ratio >= 2.0:
                vol_signal = "high"
            elif vol_ratio >= 1.5:
                vol_signal = "elevated"
            elif vol_ratio <= 0.5:
                vol_signal = "low"

    return {
        "rsi_14": rsi_val,
        "rsi_signal": rsi_signal,
        "ma_20": ma20,
        "ma_50": ma50,
        "price_vs_ma": price_vs_ma,
        "macd": macd_data["macd"],
        "macd_signal": macd_data["signal"],
        "macd_histogram": macd_data["histogram"],
        "macd_trend": macd_signal,
        "atr_14": _atr(high, low, close),
        "bollinger_upper": bb["upper"],
        "bollinger_middle": bb["middle"],
        "bollinger_lower": bb["lower"],
        "bollinger_bandwidth": bb["bandwidth"],
        "avg_volume_20d": avg_vol_20d,
        "volume_ratio": vol_ratio,
        "volume_signal": vol_signal,
    }


# =============================================================================
# Agente Researcher
# =============================================================================

class ResearcherError(Exception):
    pass


class ResearcherAgent:
    def __init__(self, date: str, config: dict, components: dict | None = None):
        self.date = date
        self.config = config
        self.raw_dir = config.get("data_raw_dir", "data/raw")
        self.max_retries = int(config.get("max_retries", 3))
        self.retry_delay = int(config.get("retry_delay", 5))
        self.madrid = MADRID_TZ

        if components is not None:
            self.components = components
        else:
            cache_dir = os.path.dirname(self.raw_dir) or "data"
            self.components = get_ibex35_components(
                cache_dir=cache_dir,
                max_cache_age_days=int(config.get("ibex_cache_days", 7)),
            )

        self.tickers = self.components["tickers"]
        self.names = self.components["names"]
        self.sectors = self.components["sectors"]
        self.aliases = self.components["aliases"]
        logger.info(
            f"Researcher listo: {len(self.tickers)} tickers "
            f"(fuente: {self.components.get('source', '?')}, fecha: {self.components.get('last_updated', '?')})"
        )

    def run(self) -> dict:
        prices_file = os.path.join(self.raw_dir, f"ibex35_prices_{self.date}.csv")
        indicators_file = os.path.join(self.raw_dir, f"ibex35_indicators_{self.date}.json")
        news_file = os.path.join(self.raw_dir, f"ibex35_news_{self.date}.json")
        macro_file = os.path.join(self.raw_dir, f"macro_{self.date}.json")

        calendar_file = os.path.join(self.raw_dir, f"calendar_{self.date}.json")
        if (os.path.exists(prices_file) and os.path.exists(news_file)
                and os.path.exists(indicators_file) and os.path.exists(macro_file)):
            logger.info(f"Datos de {self.date} ya existen, omitiendo descarga.")
            return {
                "prices_file": prices_file,
                "indicators_file": indicators_file,
                "news_file": news_file,
                "macro_file": macro_file,
                "calendar_file": calendar_file if os.path.exists(calendar_file) else None,
                "status": "ok",
                "errors": [],
            }

        errors = []

        try:
            prices_file, indicators_file = self.fetch_prices()
        except ResearcherError:
            raise
        except Exception as e:
            errors.append(f"fetch_prices: {e}")
            prices_file = None
            indicators_file = None

        try:
            news_file = self.fetch_news()
        except Exception as e:
            errors.append(f"fetch_news: {e}")
            news_file = None

        try:
            macro_file = self.collect_macro_data()
        except Exception as e:
            errors.append(f"collect_macro_data: {e}")
            macro_file = None

        try:
            calendar_file = self.collect_economic_calendar()
        except Exception as e:
            errors.append(f"collect_economic_calendar: {e}")
            calendar_file = None

        status = "ok" if not errors else ("partial" if prices_file else "error")
        return {
            "prices_file": prices_file,
            "indicators_file": indicators_file,
            "news_file": news_file,
            "macro_file": macro_file,
            "calendar_file": calendar_file,
            "status": status,
            "errors": errors,
        }

    def fetch_prices(self) -> tuple[str, str]:
        logger.info(f"Descargando precios e indicadores IBEX 35 para {self.date} ({len(self.tickers)} tickers, {_HISTORY_DAYS}d histórico)")
        rows = []
        indicators = {}

        # ^IBEX — valor real del índice
        ibex_index = self._fetch_index()

        for ticker in self.tickers:
            row, ind = self._fetch_ticker_full(ticker)
            rows.append(row)
            if ind:
                indicators[ticker] = ind

        df = pd.DataFrame(rows)
        valid = df[df["error"].isna() | (df["error"] == "")].shape[0]
        logger.info(f"Tickers con datos válidos: {valid}/{len(self.tickers)}")

        if valid == 0:
            raise ResearcherError("Ningún ticker devolvió datos válidos. Mercado posiblemente cerrado.")

        os.makedirs(self.raw_dir, exist_ok=True)

        prices_path = os.path.join(self.raw_dir, f"ibex35_prices_{self.date}.csv")
        df.to_csv(prices_path, index=False, encoding="utf-8")
        logger.info(f"Precios guardados en {prices_path}")

        # Contribución en puntos al movimiento del IBEX (Bloque B)
        ibex_prev = ibex_index.get("prev_close")
        if ibex_prev and ibex_prev > 0:
            total_mcap = sum(
                r.get("market_cap") or 0 for r in rows
                if r.get("market_cap") and pd.notna(r.get("market_cap"))
            )
            if total_mcap > 0:
                for r in rows:
                    ticker = r.get("ticker")
                    mcap = r.get("market_cap")
                    chg = r.get("change_pct")
                    if ticker and mcap and pd.notna(mcap) and chg and pd.notna(chg):
                        weight = mcap / total_mcap
                        contrib = round((chg / 100) * weight * ibex_prev, 2)
                        mcap_weight_pct = round(weight * 100, 2)
                        if ticker in indicators:
                            indicators[ticker]["contribution_pts"] = contrib
                            indicators[ticker]["market_cap_weight_pct"] = mcap_weight_pct

        indicators_path = os.path.join(self.raw_dir, f"ibex35_indicators_{self.date}.json")
        indicators_data = {
            "date": self.date,
            "ibex_index": ibex_index,
            "tickers": indicators,
        }
        with open(indicators_path, "w", encoding="utf-8") as f:
            json.dump(indicators_data, f, ensure_ascii=False, indent=2)
        logger.info(f"Indicadores guardados en {indicators_path} ({len(indicators)} tickers)")

        return prices_path, indicators_path

    def _fetch_index(self) -> dict:
        """Descarga el valor real del ^IBEX para tener la variación del índice."""
        try:
            t = yf.Ticker("^IBEX")
            hist = t.history(period="5d", auto_adjust=True)
            if hist.empty or len(hist) < 2:
                return {}
            today = hist.iloc[-1]
            prev = hist.iloc[-2]
            close = float(today["Close"])
            prev_close = float(prev["Close"])
            return {
                "close": round(close, 2),
                "prev_close": round(prev_close, 2),
                "change_abs": round(close - prev_close, 2),
                "change_pct": round((close - prev_close) / prev_close * 100, 2),
                "open": round(float(today["Open"]), 2),
                "high": round(float(today["High"]), 2),
                "low": round(float(today["Low"]), 2),
                "volume": int(today["Volume"]) if pd.notna(today["Volume"]) else None,
            }
        except Exception as e:
            logger.warning(f"^IBEX no disponible: {e}")
            return {}

    def _fetch_ticker_full(self, ticker: str) -> tuple[dict, dict | None]:
        base = {
            "ticker": ticker,
            "name": self.names.get(ticker, ticker),
            "sector": self.sectors.get(ticker, ""),
            "open": None, "high": None, "low": None, "close": None,
            "volume": None, "prev_close": None, "change_abs": None,
            "change_pct": None, "market_cap": None,
            "week_52_high": None, "week_52_low": None,
            "fetch_timestamp": datetime.now(self.madrid).isoformat(),
            "error": "",
        }

        for attempt in range(self.max_retries):
            try:
                t = yf.Ticker(ticker)
                hist = t.history(period=f"{_HISTORY_DAYS}d", auto_adjust=True)
                if hist.empty or len(hist) < 1:
                    base["error"] = "Sin datos"
                    return base, None

                # Datos del día
                today = hist.iloc[-1]
                base["open"] = round(float(today["Open"]), 3)
                base["high"] = round(float(today["High"]), 3)
                base["low"] = round(float(today["Low"]), 3)
                base["close"] = round(float(today["Close"]), 3)
                base["volume"] = int(today["Volume"]) if pd.notna(today["Volume"]) else None

                if len(hist) >= 2:
                    prev = hist.iloc[-2]
                    base["prev_close"] = round(float(prev["Close"]), 3)
                    base["change_abs"] = round(base["close"] - base["prev_close"], 3)
                    base["change_pct"] = round((base["change_abs"] / base["prev_close"]) * 100, 2)

                info = t.fast_info
                base["market_cap"] = getattr(info, "market_cap", None)
                base["week_52_high"] = getattr(info, "fifty_two_week_high", None)
                base["week_52_low"] = getattr(info, "fifty_two_week_low", None)

                # Indicadores técnicos con todo el histórico
                indicators = _compute_indicators(hist)
                indicators["ticker"] = ticker
                indicators["name"] = self.names.get(ticker, ticker)
                indicators["close"] = base["close"]
                indicators["change_pct"] = base["change_pct"]

                # Posición en rango 52 semanas
                w52h = base.get("week_52_high")
                w52l = base.get("week_52_low")
                close_val = base.get("close")
                if w52h and w52l and close_val and w52h > w52l:
                    range_pct = round((close_val - w52l) / (w52h - w52l) * 100, 1)
                    indicators["range_52w_pct"] = range_pct
                    if range_pct >= 90:
                        indicators["range_52w_flag"] = "near_high"
                    elif range_pct <= 10:
                        indicators["range_52w_flag"] = "near_low"
                    elif range_pct <= 30:
                        indicators["range_52w_flag"] = "low_range"
                    else:
                        indicators["range_52w_flag"] = "mid_range"
                else:
                    indicators["range_52w_pct"] = None
                    indicators["range_52w_flag"] = None

                return base, indicators

            except Exception as e:
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay)
                else:
                    base["error"] = str(e)
                    logger.warning(f"Ticker {ticker} fallido tras {self.max_retries} intentos: {e}")

        return base, None

    def collect_macro_data(self) -> str:
        """Recopila datos macro: índices europeos, divisas, materias primas y volatilidad."""
        macro_file = os.path.join(self.raw_dir, f"macro_{self.date}.json")
        if os.path.exists(macro_file):
            logger.info(f"Datos macro de {self.date} ya existen.")
            return macro_file

        MACRO_TICKERS = {
            "^GDAXI":    "DAX (Alemania)",
            "^FCHI":     "CAC 40 (Francia)",
            "^STOXX50E": "Euro Stoxx 50",
            "^FTSE":     "FTSE 100 (Reino Unido)",
            "EURUSD=X":  "EUR/USD",
            "EURGBP=X":  "EUR/GBP",
            "BZ=F":      "Brent Crude (USD/barril)",
            "GC=F":      "Oro (USD/oz)",
            "NG=F":      "Gas Natural (USD/MMBtu)",
            "^VIX":      "VIX (Volatilidad implícita S&P500)",
        }

        macro_data = {}
        for ticker, name in MACRO_TICKERS.items():
            try:
                t = yf.Ticker(ticker)
                hist = t.history(period="5d", auto_adjust=True)
                if hist.empty or len(hist) < 2:
                    logger.warning(f"Macro {ticker}: sin datos suficientes")
                    continue
                today = hist.iloc[-1]
                prev = hist.iloc[-2]
                close = float(today["Close"])
                prev_close = float(prev["Close"])
                entry = {
                    "name": name,
                    "close": round(close, 4),
                    "prev_close": round(prev_close, 4),
                    "change_abs": round(close - prev_close, 4),
                    "change_pct": round((close - prev_close) / prev_close * 100, 2),
                    "ytd_pct": None,
                }
                # YTD
                try:
                    hist_ytd = t.history(period="ytd", auto_adjust=True)
                    if not hist_ytd.empty and len(hist_ytd) >= 2:
                        first = float(hist_ytd.iloc[0]["Close"])
                        last = float(hist_ytd.iloc[-1]["Close"])
                        entry["ytd_pct"] = round((last - first) / first * 100, 2)
                except Exception:
                    pass
                macro_data[ticker] = entry
            except Exception as e:
                logger.warning(f"Macro ticker {ticker} fallido: {e}")

        result = {
            "date": self.date,
            "fetch_timestamp": datetime.now(self.madrid).isoformat(),
            "macro": macro_data,
        }
        os.makedirs(self.raw_dir, exist_ok=True)
        with open(macro_file, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        logger.info(f"Datos macro guardados en {macro_file} ({len(macro_data)} indicadores)")
        return macro_file

    def collect_economic_calendar(self) -> str:
        """Recopila el calendario económico de los próximos 5 días vía Finnhub API."""
        cal_file = os.path.join(self.raw_dir, f"calendar_{self.date}.json")
        if os.path.exists(cal_file):
            logger.info(f"Calendario económico de {self.date} ya existe.")
            return cal_file

        api_key = (self.config.get("finnhub_api_key") or os.environ.get("FINNHUB_API_KEY") or "").strip()
        if not api_key:
            logger.warning("FINNHUB_API_KEY no configurada — calendario económico omitido")
            return None

        try:
            from datetime import timedelta
            date_from = self.date
            date_to = (datetime.strptime(self.date, "%Y-%m-%d") + timedelta(days=7)).strftime("%Y-%m-%d")
            url = (
                f"https://finnhub.io/api/v1/calendar/economic"
                f"?from={date_from}&to={date_to}&token={api_key}"
            )
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            # Filtrar eventos relevantes para Europa/España
            RELEVANT_COUNTRIES = {"EU", "ES", "DE", "FR", "GB", "US"}
            RELEVANT_KEYWORDS = {
                "cpi", "pmi", "gdp", "pib", "inflation", "inflacion", "interest rate",
                "unemployment", "desempleo", "ecb", "bce", "fed", "central bank",
                "retail sales", "industrial production", "trade balance",
                "employment", "empleo", "deficit", "debt", "deuda",
            }
            events = data.get("economicCalendar", [])
            filtered = []
            for event in events:
                country = (event.get("country") or "").upper()
                name = (event.get("event") or "").lower()
                impact = (event.get("impact") or "").lower()
                if country in RELEVANT_COUNTRIES:
                    name_match = any(kw in name for kw in RELEVANT_KEYWORDS)
                    high_impact = impact in ("high", "medium")
                    if name_match or high_impact:
                        filtered.append({
                            "date": event.get("time", "")[:10],
                            "time": event.get("time", ""),
                            "country": country,
                            "event": event.get("event", ""),
                            "impact": impact,
                            "actual": event.get("actual"),
                            "estimate": event.get("estimate"),
                            "prev": event.get("prev"),
                        })

            result = {
                "date": self.date,
                "date_range": f"{date_from} → {date_to}",
                "fetch_timestamp": datetime.now(self.madrid).isoformat(),
                "events": filtered[:20],
                "total_events_found": len(filtered),
            }
            os.makedirs(self.raw_dir, exist_ok=True)
            with open(cal_file, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            logger.info(f"Calendario económico guardado: {cal_file} ({len(filtered)} eventos)")
            return cal_file
        except Exception as e:
            logger.warning(f"Error obteniendo calendario económico: {e}")
            return None

    def fetch_news(self) -> str:
        logger.info("Descargando noticias RSS")
        feeds_config = [
            ("Expansion Mercados", self.config.get("rss_expansion", "https://www.expansion.com/rss/mercados.xml")),
            ("Cinco Dias Mercados", self.config.get("rss_cinco_dias", "https://cincodias.elpais.com/seccion/rss/mercados/")),
        ]

        sources, all_news, counter = [], [], 0
        for feed_name, feed_url in feeds_config:
            items, source_info = self._fetch_feed(feed_name, feed_url, counter)
            sources.append(source_info)
            all_news.extend(items)
            counter += len(items)

        all_news.sort(key=lambda x: x["published"], reverse=True)
        all_news = all_news[:50]

        result = {
            "fetch_date": self.date,
            "fetch_timestamp": datetime.now(self.madrid).isoformat(),
            "sources": sources,
            "news": all_news,
            "total_news": len(all_news),
        }

        os.makedirs(self.raw_dir, exist_ok=True)
        out_path = os.path.join(self.raw_dir, f"ibex35_news_{self.date}.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        logger.info(f"Noticias guardadas en {out_path} ({len(all_news)} items)")
        return out_path

    def _fetch_feed(self, feed_name: str, feed_url: str, counter_start: int):
        items = []
        source_info = {"name": feed_name, "url": feed_url, "status": "error", "items_count": 0}

        for attempt in range(self.max_retries):
            try:
                try:
                    raw_bytes = requests.get(feed_url, headers=_SCRAPER_HEADERS, timeout=15).content
                    # Elimina caracteres de control XML inválidos (excepto \t \n \r)
                    clean = bytes(b for b in raw_bytes if b >= 0x20 or b in (0x09, 0x0A, 0x0D))
                    feed = feedparser.parse(clean)
                except Exception:
                    feed = feedparser.parse(feed_url)

                if feed.bozo and not feed.entries:
                    raise ValueError(f"Feed malformado: {feed.bozo_exception}")

                for i, entry in enumerate(feed.entries):
                    published = self._parse_date(entry)
                    summary = getattr(entry, "summary", "") or getattr(entry, "description", "") or ""
                    title = getattr(entry, "title", "")
                    text_search = f"{title} {summary}".lower()

                    # Detección con word boundaries para evitar falsos positivos
                    tickers_found = [
                        ticker for ticker, aliases in self.aliases.items()
                        if any(
                            re.search(r'\b' + re.escape(alias.lower()) + r'\b', text_search)
                            for alias in aliases
                        )
                    ]

                    items.append({
                        "id": f"{feed_name[:3].lower()}_{counter_start + i:03d}",
                        "source": feed_name,
                        "title": title,
                        "summary": summary[:500],
                        "url": getattr(entry, "link", ""),
                        "published": published,
                        "tickers_mentioned": tickers_found,
                    })

                source_info["status"] = "ok"
                source_info["items_count"] = len(items)
                logger.info(f"Feed '{feed_name}': {len(items)} noticias")
                return items, source_info

            except Exception as e:
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay)
                else:
                    logger.warning(f"Feed '{feed_name}' fallido: {e}")
                    source_info["status"] = "error"

        return items, source_info

    def _parse_date(self, entry) -> str:
        try:
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                ts = calendar.timegm(entry.published_parsed)
                return datetime.fromtimestamp(ts, tz=pytz.utc).isoformat()
        except Exception:
            pass
        return datetime.now(pytz.utc).isoformat()
