import os
import json
import time
import logging
import calendar
from datetime import datetime
from email.utils import parsedate_to_datetime

import pytz
import pandas as pd
import feedparser
import requests
import yfinance as yf

from agents.ibex_data import get_ibex35_components

logger = logging.getLogger("bolsa.researcher")

_SCRAPER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


class ResearcherError(Exception):
    pass


class ResearcherAgent:
    def __init__(self, date: str, config: dict, components: dict | None = None):
        self.date = date
        self.config = config
        self.raw_dir = config.get("data_raw_dir", "data/raw")
        self.max_retries = int(config.get("max_retries", 3))
        self.retry_delay = int(config.get("retry_delay", 5))
        self.madrid = pytz.timezone("Europe/Madrid")

        # Acepta componentes precargados (pasados por el leader) o los carga aquí
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
        news_file = os.path.join(self.raw_dir, f"ibex35_news_{self.date}.json")

        if os.path.exists(prices_file) and os.path.exists(news_file):
            logger.info(f"Datos de {self.date} ya existen, omitiendo descarga.")
            return {"prices_file": prices_file, "news_file": news_file, "status": "ok", "errors": []}

        errors = []

        try:
            prices_file = self.fetch_prices()
        except ResearcherError:
            raise
        except Exception as e:
            errors.append(f"fetch_prices: {e}")
            prices_file = None

        try:
            news_file = self.fetch_news()
        except Exception as e:
            errors.append(f"fetch_news: {e}")
            news_file = None

        status = "ok" if not errors else ("partial" if prices_file else "error")
        return {"prices_file": prices_file, "news_file": news_file, "status": status, "errors": errors}

    def fetch_prices(self) -> str:
        logger.info(f"Descargando precios IBEX 35 para {self.date} ({len(self.tickers)} tickers)")
        rows = [self._fetch_ticker(t) for t in self.tickers]

        df = pd.DataFrame(rows)
        valid = df[df["error"].isna() | (df["error"] == "")].shape[0]
        logger.info(f"Tickers con datos válidos: {valid}/{len(self.tickers)}")

        if valid == 0:
            raise ResearcherError("Ningún ticker devolvió datos válidos. Mercado posiblemente cerrado.")

        os.makedirs(self.raw_dir, exist_ok=True)
        out_path = os.path.join(self.raw_dir, f"ibex35_prices_{self.date}.csv")
        df.to_csv(out_path, index=False, encoding="utf-8")
        logger.info(f"Precios guardados en {out_path}")
        return out_path

    def _fetch_ticker(self, ticker: str) -> dict:
        base = {
            "ticker": ticker,
            "name": self.names.get(ticker, ticker),
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
                hist = t.history(period="2d", auto_adjust=True)
                if hist.empty:
                    base["error"] = "Sin datos"
                    return base

                today = hist.iloc[-1]
                base["open"] = round(float(today["Open"]), 3)
                base["high"] = round(float(today["High"]), 3)
                base["low"] = round(float(today["Low"]), 3)
                base["close"] = round(float(today["Close"]), 3)
                base["volume"] = int(today["Volume"])

                if len(hist) >= 2:
                    prev = hist.iloc[-2]
                    base["prev_close"] = round(float(prev["Close"]), 3)
                    base["change_abs"] = round(base["close"] - base["prev_close"], 3)
                    base["change_pct"] = round((base["change_abs"] / base["prev_close"]) * 100, 2)

                info = t.fast_info
                base["market_cap"] = getattr(info, "market_cap", None)
                base["week_52_high"] = getattr(info, "fifty_two_week_high", None)
                base["week_52_low"] = getattr(info, "fifty_two_week_low", None)
                return base

            except Exception as e:
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay)
                else:
                    base["error"] = str(e)
                    logger.warning(f"Ticker {ticker} fallido tras {self.max_retries} intentos: {e}")
        return base

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
                # Descarga manual para limpiar caracteres XML inválidos antes de parsear
                try:
                    raw_bytes = requests.get(feed_url, headers=_SCRAPER_HEADERS, timeout=15).content
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

                    tickers_found = [
                        ticker for ticker, aliases in self.aliases.items()
                        if any(alias.lower() in text_search for alias in aliases)
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
