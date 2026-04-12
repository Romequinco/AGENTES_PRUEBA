import os
import json
import time
import logging
import calendar
import random
import re
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

import pytz
import pandas as pd
import feedparser
import requests
import yfinance as yf
from bs4 import BeautifulSoup

logger = logging.getLogger("bolsa.researcher")

# =============================================================================
# Composición dinámica del IBEX 35
# Cadena de resolución:
#   1. Caché local válida (< ibex_cache_days días)
#   2. Wikipedia ES (scraping)
#   3. Slickcharts (scraping, fallback)
#   4. Caché local expirada (si existe)
#   5. Lista hardcodeada de emergencia
# =============================================================================

_IBEX_CACHE_FILENAME = "ibex_cache.json"

_EMERGENCY_FALLBACK = {
    "tickers": [
        "ACS.MC", "ACX.MC", "AENA.MC", "AMS.MC", "ANA.MC",
        "BBVA.MC", "BKT.MC", "CABK.MC", "CLNX.MC", "COL.MC",
        "ELE.MC", "ENG.MC", "FDR.MC", "FER.MC", "GRF.MC",
        "IAG.MC", "IBE.MC", "IDR.MC",
        "ITX.MC", "LOG.MC", "MAP.MC", "MEL.MC", "MRL.MC",
        "MTS.MC", "NTGY.MC", "RED.MC", "REP.MC",
        "ROVI.MC", "SAB.MC", "SAN.MC", "TEF.MC",
    ],
    "names": {
        "ACS.MC": "ACS", "ACX.MC": "Acerinox", "AENA.MC": "Aena",
        "AMS.MC": "Amadeus", "ANA.MC": "Acciona", "BBVA.MC": "BBVA",
        "BKT.MC": "Bankinter", "CABK.MC": "CaixaBank", "CLNX.MC": "Cellnex",
        "COL.MC": "Inmobiliaria Colonial", "ELE.MC": "Endesa", "ENG.MC": "Enagás",
        "FDR.MC": "Fluidra", "FER.MC": "Ferrovial", "GRF.MC": "Grifols",
        "IAG.MC": "IAG", "IBE.MC": "Iberdrola", "IDR.MC": "Indra",
        "ITX.MC": "Inditex", "LOG.MC": "Logista", "MAP.MC": "Mapfre",
        "MEL.MC": "Meliá", "MRL.MC": "Merlin Properties", "MTS.MC": "ArcelorMittal",
        "NTGY.MC": "Naturgy", "RED.MC": "Redeia", "REP.MC": "Repsol",
        "ROVI.MC": "Rovi", "SAB.MC": "Sabadell", "SAN.MC": "Santander",
        "TEF.MC": "Telefónica",
    },
    "sectors": {
        "ACS.MC": "Construcción", "FER.MC": "Construcción", "ANA.MC": "Construcción",
        "MRL.MC": "Inmobiliario", "COL.MC": "Inmobiliario",
        "SAN.MC": "Bancario", "BBVA.MC": "Bancario", "CABK.MC": "Bancario",
        "BKT.MC": "Bancario", "SAB.MC": "Bancario",
        "REP.MC": "Energía", "IBE.MC": "Energía", "ELE.MC": "Energía",
        "NTGY.MC": "Energía", "ENG.MC": "Energía", "RED.MC": "Energía",
        "TEF.MC": "Telecomunicaciones", "CLNX.MC": "Telecomunicaciones",
        "ITX.MC": "Consumo", "MEL.MC": "Consumo", "LOG.MC": "Consumo",
        "IAG.MC": "Transporte", "AENA.MC": "Transporte",
        "GRF.MC": "Salud", "ROVI.MC": "Salud",
        "AMS.MC": "Tecnología", "IDR.MC": "Tecnología",
        "ACX.MC": "Industria", "MTS.MC": "Industria", "FDR.MC": "Industria",
        "MAP.MC": "Seguros",
    },
    "aliases": {
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
        "ANA.MC": ["Acciona"],
        "ROVI.MC": ["Rovi"],
        "RED.MC": ["Redeia", "Red Eléctrica"],
        "LOG.MC": ["Logista"],
        "FDR.MC": ["Fluidra"],
        "MRL.MC": ["Merlin"],
        "COL.MC": ["Colonial", "Inmocolonial", "Inmobiliaria Colonial"],
    },
}

_SCRAPER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


def _ibex_cache_path(cache_dir: str) -> str:
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, _IBEX_CACHE_FILENAME)


def _load_ibex_cache(cache_dir: str) -> dict | None:
    path = _ibex_cache_path(cache_dir)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save_ibex_cache(cache_dir: str, data: dict) -> None:
    try:
        with open(_ibex_cache_path(cache_dir), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"No se pudo guardar caché IBEX: {e}")


def _ibex_cache_is_fresh(cache: dict, max_age_days: int) -> bool:
    last = cache.get("last_updated", "")
    if not last:
        return False
    try:
        updated = datetime.strptime(last, "%Y-%m-%d").date()
        return (datetime.now().date() - updated) < timedelta(days=max_age_days)
    except ValueError:
        return False


def _normalize_ticker(raw: str) -> str | None:
    t = re.sub(r"^(BME:|MC:)", "", raw.strip().upper())
    if not t.endswith(".MC"):
        t += ".MC"
    return t if re.match(r"^[A-Z0-9]{1,6}\.MC$", t) else None


def _ticker_aliases(ticker: str, name: str) -> list[str]:
    known = _EMERGENCY_FALLBACK["aliases"].get(ticker)
    return known if known else ([name] if name else [ticker.replace(".MC", "")])


def _validate_ibex_tickers(tickers: list[str], prev_tickers: list[str]) -> list[str]:
    new = [t for t in tickers if t not in prev_tickers]
    sample = list(set(new + random.sample(tickers, min(5, len(tickers)))))
    failed = []
    for t in sample:
        try:
            if yf.Ticker(t).fast_info.last_price is None:
                failed.append(t)
        except Exception:
            failed.append(t)
    if sample and len(failed) / len(sample) > 0.2:
        logger.warning(f"Validación IBEX: {len(failed)}/{len(sample)} tickers fallaron. Fuente descartada.")
        return []
    if failed:
        logger.warning(f"Tickers excluidos tras validación: {failed}")
    return [t for t in tickers if t not in failed]


def _scrape_wikipedia(prev_tickers: list[str]) -> dict | None:
    try:
        resp = requests.get("https://es.wikipedia.org/wiki/IBEX_35", headers=_SCRAPER_HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        logger.warning(f"Wikipedia: error de red: {e}")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    target_table = None
    for table in soup.find_all("table", class_=re.compile(r"wikitable")):
        hdrs = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        if any("empresa" in h or "compañía" in h for h in hdrs) and any(
            "ticker" in h or "símbolo" in h or "bolsa" in h for h in hdrs
        ):
            target_table = table
            break

    if target_table is None:
        logger.warning("Wikipedia: tabla de componentes no encontrada")
        return None

    hdrs = [th.get_text(strip=True).lower() for th in target_table.find_all("th")]
    try:
        name_col = next(i for i, h in enumerate(hdrs) if "empresa" in h or "compañía" in h)
        ticker_col = next(i for i, h in enumerate(hdrs) if "ticker" in h or "símbolo" in h or "bolsa" in h)
        sector_col = next((i for i, h in enumerate(hdrs) if "sector" in h), None)
    except StopIteration:
        logger.warning("Wikipedia: columnas necesarias no identificadas")
        return None

    tickers, names, sectors, aliases = [], {}, {}, {}
    for row in target_table.find_all("tr")[1:]:
        cells = row.find_all(["td", "th"])
        if len(cells) <= max(name_col, ticker_col):
            continue
        ticker = _normalize_ticker(cells[ticker_col].get_text(strip=True))
        if not ticker:
            continue
        raw_name = cells[name_col].get_text(strip=True)
        tickers.append(ticker)
        names[ticker] = raw_name
        if sector_col and len(cells) > sector_col:
            sectors[ticker] = cells[sector_col].get_text(strip=True)
        aliases[ticker] = _ticker_aliases(ticker, raw_name)

    if len(tickers) < 20:
        logger.warning(f"Wikipedia: solo {len(tickers)} tickers encontrados, insuficiente")
        return None

    valid = _validate_ibex_tickers(tickers, prev_tickers)
    if not valid:
        return None

    logger.info(f"Wikipedia: {len(valid)} tickers válidos")
    return {
        "source": "wikipedia",
        "tickers": valid,
        "names": {t: names[t] for t in valid},
        "sectors": {t: sectors.get(t, "") for t in valid},
        "aliases": {t: aliases[t] for t in valid},
    }


def _scrape_slickcharts(prev_tickers: list[str]) -> dict | None:
    try:
        resp = requests.get("https://www.slickcharts.com/ibex35", headers=_SCRAPER_HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        logger.warning(f"Slickcharts: error de red: {e}")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table")
    if not table:
        logger.warning("Slickcharts: tabla no encontrada")
        return None

    tickers, names, aliases = [], {}, {}
    for row in table.find_all("tr")[1:]:
        cells = row.find_all("td")
        if len(cells) < 3:
            continue
        ticker = _normalize_ticker(cells[2].get_text(strip=True))
        if not ticker:
            continue
        raw_name = cells[1].get_text(strip=True)
        tickers.append(ticker)
        names[ticker] = raw_name
        aliases[ticker] = _ticker_aliases(ticker, raw_name)

    if len(tickers) < 20:
        logger.warning(f"Slickcharts: solo {len(tickers)} tickers encontrados")
        return None

    valid = _validate_ibex_tickers(tickers, prev_tickers)
    if not valid:
        return None

    logger.info(f"Slickcharts: {len(valid)} tickers válidos")
    return {
        "source": "slickcharts",
        "tickers": valid,
        "names": {t: names[t] for t in valid},
        "sectors": {},
        "aliases": {t: aliases[t] for t in valid},
    }


def _get_ibex35_components(cache_dir: str, max_cache_age_days: int) -> dict:
    cache = _load_ibex_cache(cache_dir)

    if cache and _ibex_cache_is_fresh(cache, max_cache_age_days):
        logger.info(f"Componentes IBEX 35: caché válida ({cache.get('last_updated')}, fuente: {cache.get('source')})")
        return cache

    prev_tickers = cache.get("tickers", []) if cache else []

    result = _scrape_wikipedia(prev_tickers)

    if result is None:
        logger.info("Intentando fuente secundaria: Slickcharts")
        result = _scrape_slickcharts(prev_tickers)

    if result is None and cache:
        logger.warning(f"Fuentes remotas fallidas. Usando caché expirada ({cache.get('last_updated')}). Puede estar desactualizada.")
        return cache

    if result is None:
        logger.warning("Fuentes remotas fallidas y sin caché. Usando lista hardcodeada de emergencia.")
        result = {**_EMERGENCY_FALLBACK, "source": "emergency_fallback"}

    result["last_updated"] = datetime.now().strftime("%Y-%m-%d")
    _save_ibex_cache(cache_dir, result)
    return result


# =============================================================================
# Agente Researcher
# =============================================================================

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

        cache_dir = os.path.dirname(self.raw_dir) or "data"
        components = _get_ibex35_components(
            cache_dir=cache_dir,
            max_cache_age_days=int(config.get("ibex_cache_days", 7)),
        )
        self.tickers = components["tickers"]
        self.names = components["names"]
        self.sectors = components["sectors"]
        self.aliases = components["aliases"]
        logger.info(f"Componentes IBEX 35 cargados: {len(self.tickers)} tickers (fuente: {components.get('source', '?')})")

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

        for ticker in self.tickers:
            row = self._fetch_ticker(ticker)
            rows.append(row)

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
                    for ticker, aliases in self.aliases.items():
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
