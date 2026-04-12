"""
ibex_data.py
============
Módulo compartido: obtiene la composición actualizada del IBEX 35.

Usado por researcher.py, leader.py y cualquier agente que necesite
la lista de tickers actual. Nunca hardcodees tickers en otro módulo;
importa siempre get_ibex35_components() desde aquí.

Cadena de resolución:
  1. Caché local válida  (< max_cache_age_days días, sin peticiones HTTP)
  2. Wikipedia ES        (scraping, fuente primaria)
  3. Slickcharts         (scraping, fuente secundaria)
  4. Caché expirada      (si existe, aunque sea vieja)
  5. Lista de emergencia (último recurso, actualizar manualmente en revisiones trimestrales)
"""

import json
import logging
import os
import random
import re
from datetime import datetime, timedelta

import requests
import yfinance as yf
from bs4 import BeautifulSoup

logger = logging.getLogger("bolsa.ibex_data")

_CACHE_FILENAME = "ibex_cache.json"

# ---------------------------------------------------------------------------
# Lista de emergencia — solo se usa si TODAS las fuentes remotas fallan
# y no existe ninguna caché previa. Actualizar manualmente en cada revisión
# trimestral del IBEX (marzo, junio, septiembre, diciembre).
# ---------------------------------------------------------------------------
EMERGENCY_FALLBACK = {
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

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


# ---------------------------------------------------------------------------
# Caché
# ---------------------------------------------------------------------------

def _cache_path(cache_dir: str) -> str:
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, _CACHE_FILENAME)


def _load_cache(cache_dir: str) -> dict | None:
    path = _cache_path(cache_dir)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save_cache(cache_dir: str, data: dict) -> None:
    try:
        with open(_cache_path(cache_dir), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"No se pudo guardar caché IBEX: {e}")


def _is_fresh(cache: dict, max_age_days: int) -> bool:
    try:
        updated = datetime.strptime(cache.get("last_updated", ""), "%Y-%m-%d").date()
        return (datetime.now().date() - updated) < timedelta(days=max_age_days)
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Validación de tickers
# ---------------------------------------------------------------------------

def _validate(tickers: list[str], prev_tickers: list[str]) -> list[str]:
    """
    Valida los tickers nuevos + una muestra aleatoria de 5 contra Yahoo Finance.
    Si más del 20% falla, descarta la fuente entera (devuelve []).
    """
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
        logger.warning(f"Validación: {len(failed)}/{len(sample)} tickers fallaron (>20%). Fuente descartada.")
        return []
    if failed:
        logger.warning(f"Tickers excluidos tras validación: {failed}")
    return [t for t in tickers if t not in failed]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_ticker(raw: str) -> str | None:
    t = re.sub(r"^(BME:|MC:)", "", raw.strip().upper())
    if not t.endswith(".MC"):
        t += ".MC"
    return t if re.match(r"^[A-Z0-9]{1,6}\.MC$", t) else None


def _aliases_for(ticker: str, name: str) -> list[str]:
    known = EMERGENCY_FALLBACK["aliases"].get(ticker)
    return known if known else ([name] if name else [ticker.replace(".MC", "")])


# ---------------------------------------------------------------------------
# Scrapers
# ---------------------------------------------------------------------------

def _scrape_wikipedia(prev_tickers: list[str]) -> dict | None:
    try:
        resp = requests.get("https://es.wikipedia.org/wiki/IBEX_35", headers=_HEADERS, timeout=15)
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
        aliases[ticker] = _aliases_for(ticker, raw_name)

    if len(tickers) < 20:
        logger.warning(f"Wikipedia: solo {len(tickers)} tickers, insuficiente")
        return None

    valid = _validate(tickers, prev_tickers)
    if not valid:
        return None

    logger.info(f"Wikipedia: {len(valid)} tickers válidos obtenidos")
    return {
        "source": "wikipedia",
        "tickers": valid,
        "names": {t: names[t] for t in valid},
        "sectors": {t: sectors.get(t, "") for t in valid},
        "aliases": {t: aliases[t] for t in valid},
    }


def _scrape_slickcharts(prev_tickers: list[str]) -> dict | None:
    try:
        resp = requests.get("https://www.slickcharts.com/ibex35", headers=_HEADERS, timeout=15)
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
        aliases[ticker] = _aliases_for(ticker, raw_name)

    if len(tickers) < 20:
        logger.warning(f"Slickcharts: solo {len(tickers)} tickers, insuficiente")
        return None

    valid = _validate(tickers, prev_tickers)
    if not valid:
        return None

    logger.info(f"Slickcharts: {len(valid)} tickers válidos obtenidos")
    return {
        "source": "slickcharts",
        "tickers": valid,
        "names": {t: names[t] for t in valid},
        "sectors": {},
        "aliases": {t: aliases[t] for t in valid},
    }


# ---------------------------------------------------------------------------
# Función pública principal
# ---------------------------------------------------------------------------

def get_ibex35_components(cache_dir: str = "data", max_cache_age_days: int = 7) -> dict:
    """
    Retorna dict con claves: tickers, names, sectors, aliases, source, last_updated.

    Punto de entrada único para todos los agentes. Nunca uses listas de tickers
    hardcodeadas en otros módulos: llama siempre a esta función.
    """
    cache = _load_cache(cache_dir)

    # 1. Caché fresca — sin peticiones HTTP
    if cache and _is_fresh(cache, max_cache_age_days):
        logger.info(f"IBEX 35: caché válida ({cache.get('last_updated')}, fuente: {cache.get('source')})")
        return cache

    prev_tickers = cache.get("tickers", []) if cache else []

    # 2. Wikipedia
    result = _scrape_wikipedia(prev_tickers)

    # 3. Slickcharts
    if result is None:
        logger.info("Wikipedia falló. Intentando Slickcharts...")
        result = _scrape_slickcharts(prev_tickers)

    # 4. Caché expirada
    if result is None and cache:
        logger.warning(
            f"Todas las fuentes remotas fallaron. "
            f"Usando caché expirada ({cache.get('last_updated')}). Puede estar desactualizada."
        )
        return cache

    # 5. Emergencia
    if result is None:
        logger.warning(
            "Todas las fuentes remotas fallaron y no hay caché. "
            "Usando lista de emergencia hardcodeada. Puede estar desactualizada."
        )
        result = {**EMERGENCY_FALLBACK, "source": "emergency_fallback"}

    result["last_updated"] = datetime.now().strftime("%Y-%m-%d")
    _save_cache(cache_dir, result)
    logger.info(f"IBEX 35: {len(result['tickers'])} componentes cargados (fuente: {result['source']})")
    return result


# ---------------------------------------------------------------------------
# Prueba directa: python agents/ibex_data.py
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    data = get_ibex35_components(cache_dir="data", max_cache_age_days=0)
    print(f"\nFuente:  {data['source']}")
    print(f"Fecha:   {data['last_updated']}")
    print(f"Tickers ({len(data['tickers'])}): {data['tickers']}")
