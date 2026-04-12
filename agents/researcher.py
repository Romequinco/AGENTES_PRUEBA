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
import yfinance as yf

logger = logging.getLogger("bolsa.researcher")

IBEX35_TICKERS = [
    "ACS.MC", "ACX.MC", "AENA.MC", "AMS.MC", "ANA.MC",
    "BBVA.MC", "BKT.MC", "CABK.MC", "CLNX.MC", "COL.MC",
    "ELE.MC", "ENG.MC", "FDR.MC", "FER.MC", "GRF.MC",
    "IAG.MC", "IBE.MC", "IDR.MC", "INDRA.MC", "INM.MC",
    "ITX.MC", "LOG.MC", "MAP.MC", "MEL.MC", "MRL.MC",
    "MTS.MC", "NTGY.MC", "RED.MC", "REE.MC", "REP.MC",
    "ROVI.MC", "SAB.MC", "SAN.MC", "SGRE.MC", "TEF.MC",
]

IBEX35_NAMES = {
    "ACS.MC": "ACS", "ACX.MC": "Acerinox", "AENA.MC": "Aena",
    "AMS.MC": "Amadeus", "ANA.MC": "Acciona", "BBVA.MC": "BBVA",
    "BKT.MC": "Bankinter", "CABK.MC": "CaixaBank", "CLNX.MC": "Cellnex",
    "COL.MC": "Inmocolonial", "ELE.MC": "Endesa", "ENG.MC": "Enagás",
    "FDR.MC": "Fluidra", "FER.MC": "Ferrovial", "GRF.MC": "Grifols",
    "IAG.MC": "IAG", "IBE.MC": "Iberdrola", "IDR.MC": "Indra",
    "INDRA.MC": "Indra A", "INM.MC": "Inmobiliaria Colonial", "ITX.MC": "Inditex",
    "LOG.MC": "Logista", "MAP.MC": "Mapfre", "MEL.MC": "Meliá",
    "MRL.MC": "Merlin Properties", "MTS.MC": "ArcelorMittal", "NTGY.MC": "Naturgy",
    "RED.MC": "Redeia", "REE.MC": "Red Eléctrica", "REP.MC": "Repsol",
    "ROVI.MC": "Rovi", "SAB.MC": "Sabadell", "SAN.MC": "Santander",
    "SGRE.MC": "Siemens Gamesa", "TEF.MC": "Telefónica",
}

IBEX35_SECTORS = {
    "ACS.MC": "Construcción", "FER.MC": "Construcción", "ANA.MC": "Construcción",
    "MRL.MC": "Inmobiliario", "COL.MC": "Inmobiliario", "INM.MC": "Inmobiliario",
    "SAN.MC": "Bancario", "BBVA.MC": "Bancario", "CABK.MC": "Bancario",
    "BKT.MC": "Bancario", "SAB.MC": "Bancario",
    "REP.MC": "Energía", "IBE.MC": "Energía", "ELE.MC": "Energía",
    "NTGY.MC": "Energía", "ENG.MC": "Energía", "RED.MC": "Energía",
    "REE.MC": "Energía",
    "TEF.MC": "Telecomunicaciones", "CLNX.MC": "Telecomunicaciones",
    "ITX.MC": "Consumo", "MEL.MC": "Consumo", "LOG.MC": "Consumo",
    "IAG.MC": "Transporte", "AENA.MC": "Transporte",
    "GRF.MC": "Salud", "ROVI.MC": "Salud",
    "AMS.MC": "Tecnología", "IDR.MC": "Tecnología", "INDRA.MC": "Tecnología",
    "ACX.MC": "Industria", "MTS.MC": "Industria", "FDR.MC": "Industria",
    "MAP.MC": "Seguros",
}

TICKER_ALIASES = {
    "SAN.MC": ["Santander", "Banco Santander"],
    "BBVA.MC": ["BBVA", "Banco Bilbao"],
    "CABK.MC": ["CaixaBank", "Caixa"],
    "BKT.MC": ["Bankinter"],
    "SAB.MC": ["Sabadell", "Banco Sabadell"],
    "ITX.MC": ["Inditex", "Zara"],
    "TEF.MC": ["Telefónica", "Telefonica"],
    "IBE.MC": ["Iberdrola"],
    "REP.MC": ["Repsol"],
    "IAG.MC": ["IAG", "Iberia", "British Airways"],
    "AENA.MC": ["Aena"],
    "AMS.MC": ["Amadeus"],
    "CLNX.MC": ["Cellnex"],
    "GRF.MC": ["Grifols"],
    "ACS.MC": ["ACS"],
    "FER.MC": ["Ferrovial"],
    "ELE.MC": ["Endesa"],
    "ENG.MC": ["Enagás", "Enagas"],
    "NTGY.MC": ["Naturgy", "Gas Natural"],
    "MAP.MC": ["Mapfre"],
    "MEL.MC": ["Meliá", "Melia"],
    "MTS.MC": ["ArcelorMittal", "Arcelor"],
    "ACX.MC": ["Acerinox"],
    "IDR.MC": ["Indra"],
    "INDRA.MC": ["Indra"],
    "ANA.MC": ["Acciona"],
    "ROVI.MC": ["Rovi"],
    "RED.MC": ["Redeia", "Red Eléctrica"],
    "REE.MC": ["Red Eléctrica"],
    "LOG.MC": ["Logista"],
    "FDR.MC": ["Fluidra"],
    "MRL.MC": ["Merlin"],
    "COL.MC": ["Colonial", "Inmocolonial"],
    "INM.MC": ["Colonial"],
    "SGRE.MC": ["Siemens Gamesa", "Gamesa"],
}


class ResearcherError(Exception):
    pass


class ResearcherAgent:
    def __init__(self, date: str, config: dict):
        self.date = date
        self.config = config
        self.raw_dir = config.get("data_raw_dir", "data/raw")
        self.max_retries = int(config.get("max_retries", 3))
        self.retry_delay = int(config.get("retry_delay", 5))
        self.madrid = pytz.timezone("Europe/Madrid")

    def run(self) -> dict:
        errors = []
        prices_file = None
        news_file = None

        try:
            prices_file = self.fetch_prices()
        except ResearcherError as e:
            raise
        except Exception as e:
            errors.append(f"fetch_prices: {e}")

        try:
            news_file = self.fetch_news()
        except Exception as e:
            errors.append(f"fetch_news: {e}")

        status = "ok" if not errors else ("partial" if prices_file else "error")
        return {"prices_file": prices_file, "news_file": news_file, "status": status, "errors": errors}

    def fetch_prices(self) -> str:
        logger.info(f"Descargando precios IBEX 35 para {self.date}")
        rows = []

        for ticker in IBEX35_TICKERS:
            row = self._fetch_ticker(ticker)
            rows.append(row)

        df = pd.DataFrame(rows)
        valid = df[df["error"].isna() | (df["error"] == "")].shape[0]
        logger.info(f"Tickers con datos válidos: {valid}/{len(IBEX35_TICKERS)}")

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
            "name": IBEX35_NAMES.get(ticker, ticker),
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
                if hist.empty or len(hist) < 1:
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

        sources = []
        all_news = []
        item_counter = 0

        for feed_name, feed_url in feeds_config:
            items, source_info = self._fetch_feed(feed_name, feed_url, item_counter)
            sources.append(source_info)
            all_news.extend(items)
            item_counter += len(items)

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
                feed = feedparser.parse(feed_url)
                if feed.bozo and not feed.entries:
                    raise ValueError(f"Feed malformado: {feed.bozo_exception}")

                for i, entry in enumerate(feed.entries):
                    published = self._parse_date(entry)
                    summary = getattr(entry, "summary", "") or getattr(entry, "description", "") or ""
                    title = getattr(entry, "title", "")
                    text_search = f"{title} {summary}".lower()

                    tickers_found = []
                    for ticker, aliases in TICKER_ALIASES.items():
                        if any(alias.lower() in text_search for alias in aliases):
                            tickers_found.append(ticker)

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
