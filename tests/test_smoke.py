"""
test_smoke.py
=============
Smoke test del sistema. Verifica que todos los módulos cargan, que los
indicadores técnicos se calculan correctamente y que el pipeline arranca
sin errores de importación ni configuración.

Uso:
    python test_smoke.py

No requiere API key real ni conexión a mercados (usa datos sintéticos).
"""

import sys
import json
import os
import traceback
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# ── Colores para output ──────────────────────────────────────────────────────
GREEN = "\033[92m"
RED   = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"

passed = 0
failed = 0


def ok(msg: str):
    global passed
    passed += 1
    print(f"  {GREEN}✓{RESET} {msg}")


def fail(msg: str, detail: str = ""):
    global failed
    failed += 1
    print(f"  {RED}✗{RESET} {msg}")
    if detail:
        print(f"    {YELLOW}{detail}{RESET}")


def section(title: str):
    print(f"\n{title}")
    print("─" * len(title))


# ── 1. Imports ───────────────────────────────────────────────────────────────
section("1. Imports de módulos")

try:
    from agents.ibex_data import get_ibex35_components, EMERGENCY_FALLBACK
    ok("agents.ibex_data importado")
except Exception as e:
    fail("agents.ibex_data", str(e))

try:
    from agents.researcher import ResearcherAgent, _rsi, _sma, _macd, _atr, _bollinger, _compute_indicators
    ok("agents.researcher importado")
except Exception as e:
    fail("agents.researcher", str(e))

try:
    from agents.analyst import AnalystAgent
    ok("agents.analyst importado")
except Exception as e:
    fail("agents.analyst", str(e))

try:
    from agents.writer import WriterAgent
    ok("agents.writer importado")
except Exception as e:
    fail("agents.writer", str(e))

try:
    from agents.leader import LeaderAgent
    ok("agents.leader importado")
except Exception as e:
    fail("agents.leader", str(e))


# ── 2. Indicadores técnicos ──────────────────────────────────────────────────
section("2. Indicadores técnicos (datos sintéticos)")

# Genera serie de precios sintética con tendencia alcista + ruido
np.random.seed(42)
n = 60
prices = pd.Series(100 + np.cumsum(np.random.randn(n) * 0.5 + 0.1))
highs  = prices + np.abs(np.random.randn(n) * 0.3)
lows   = prices - np.abs(np.random.randn(n) * 0.3)

# RSI
try:
    rsi = _rsi(prices, 14)
    assert rsi is not None, "RSI devolvió None"
    assert 0 <= rsi <= 100, f"RSI fuera de rango: {rsi}"
    ok(f"RSI(14) = {rsi}")
except Exception as e:
    fail("RSI(14)", str(e))

# RSI insuficiente
try:
    rsi_short = _rsi(prices[:5], 14)
    assert rsi_short is None, "RSI con datos insuficientes debería devolver None"
    ok("RSI con datos insuficientes devuelve None")
except Exception as e:
    fail("RSI datos insuficientes", str(e))

# MA
try:
    ma20 = _sma(prices, 20)
    ma50 = _sma(prices, 50)
    assert ma20 is not None and ma50 is not None
    ok(f"MA20={ma20}, MA50={ma50}")
except Exception as e:
    fail("SMA(20/50)", str(e))

# MACD
try:
    macd = _macd(prices)
    assert all(k in macd for k in ("macd", "signal", "histogram"))
    assert macd["macd"] is not None
    ok(f"MACD={macd['macd']}, signal={macd['signal']}, hist={macd['histogram']}")
except Exception as e:
    fail("MACD(12,26,9)", str(e))

# ATR
try:
    atr = _atr(highs, lows, prices, 14)
    assert atr is not None and atr > 0
    ok(f"ATR(14) = {atr}")
except Exception as e:
    fail("ATR(14)", str(e))

# Bollinger
try:
    bb = _bollinger(prices, 20)
    assert all(k in bb for k in ("upper", "middle", "lower", "bandwidth"))
    assert bb["upper"] > bb["middle"] > bb["lower"]
    ok(f"Bollinger upper={bb['upper']}, mid={bb['middle']}, lower={bb['lower']}, bw={bb['bandwidth']}%")
except Exception as e:
    fail("Bollinger(20,2)", str(e))

# _compute_indicators completo
try:
    hist_df = pd.DataFrame({
        "Close": prices.values,
        "High": highs.values,
        "Low": lows.values,
    })
    ind = _compute_indicators(hist_df)
    required = ["rsi_14", "ma_20", "ma_50", "macd", "atr_14", "bollinger_upper"]
    missing = [k for k in required if k not in ind]
    assert not missing, f"Faltan campos: {missing}"
    ok(f"_compute_indicators devuelve todos los campos ({len(ind)} campos)")
except Exception as e:
    fail("_compute_indicators", str(e))


# ── 3. ibex_data emergency fallback ─────────────────────────────────────────
section("3. ibex_data — fallback de emergencia")

try:
    fb = EMERGENCY_FALLBACK
    assert len(fb["tickers"]) > 20, "Fallback tiene menos de 20 tickers"
    assert len(fb["names"]) > 0
    assert len(fb["aliases"]) > 0
    ok(f"EMERGENCY_FALLBACK tiene {len(fb['tickers'])} tickers")
except Exception as e:
    fail("EMERGENCY_FALLBACK", str(e))

try:
    # Con max_cache_age_days=999 para no hacer peticiones HTTP en el test
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        # Escribe una caché falsa fresca
        fake_cache = {
            **EMERGENCY_FALLBACK,
            "source": "test",
            "last_updated": datetime.now().strftime("%Y-%m-%d"),
        }
        cache_path = os.path.join(tmpdir, "ibex_cache.json")
        with open(cache_path, "w") as f:
            json.dump(fake_cache, f)

        result = get_ibex35_components(cache_dir=tmpdir, max_cache_age_days=7)
        assert result["source"] == "test"
        assert len(result["tickers"]) > 0
        ok(f"get_ibex35_components lee caché correctamente ({len(result['tickers'])} tickers)")
except Exception as e:
    fail("get_ibex35_components con caché", str(e))


# ── 4. Analyst — build_prompt con datos sintéticos ──────────────────────────
section("4. Analyst — build_prompt sin API")

try:
    config = {
        "api_key": "test-key",
        "model_analyst": "claude-haiku-4-5-20251001",
        "max_retries": "1",
        "retry_delay": "0",
        "data_raw_dir": "data/raw",
        "data_analysis_dir": "data/analysis",
    }
    agent = AnalystAgent.__new__(AnalystAgent)
    agent.date = "2026-01-01"
    agent.config = config
    agent.raw_dir = "data/raw"
    agent.analysis_dir = "data/analysis"
    agent.model = "claude-haiku-4-5-20251001"
    agent.max_retries = 1
    agent.retry_delay = 0
    agent.madrid = __import__("pytz").timezone("Europe/Madrid")

    # Cargar instrucciones reales
    skills_dir = os.path.join(os.path.dirname(__file__), "skills")
    with open(os.path.join(skills_dir, "analyst_instructions.md"), encoding="utf-8") as f:
        agent.system_prompt = f.read()

    # Datos sintéticos mínimos
    df = pd.DataFrame([{
        "ticker": "SAN.MC", "name": "Santander", "sector": "Bancario",
        "open": 4.1, "high": 4.2, "low": 4.0, "close": 4.15,
        "volume": 10000000, "prev_close": 4.1, "change_abs": 0.05,
        "change_pct": 1.22, "market_cap": None, "week_52_high": None,
        "week_52_low": None, "fetch_timestamp": "", "error": "",
    }])
    news = {"news": []}
    indicators = {
        "ibex_index": {"close": 10500.0, "change_pct": 0.5, "change_abs": 52.0},
        "tickers": {
            "SAN.MC": {
                "rsi_14": 55.0, "rsi_signal": "neutral",
                "ma_20": 4.10, "ma_50": 4.05, "close": 4.15,
                "macd_trend": "alcista", "macd_histogram": 0.02,
                "bollinger_bandwidth": 8.5, "atr_14": 0.12,
            }
        }
    }

    prompt = agent.build_prompt({"prices_df": df, "news": news, "indicators": indicators})
    assert "SAN" in prompt
    assert "RSI" in prompt
    assert "MACD" in prompt
    assert "Bollinger" in prompt or "BB" in prompt
    assert "^IBEX" in prompt or "10500" in prompt
    ok(f"build_prompt genera prompt válido ({len(prompt)} chars)")
except Exception as e:
    fail("build_prompt", traceback.format_exc())


# ── 5. Ficheros de skills/instrucciones ─────────────────────────────────────
section("5. Ficheros de instrucciones (skills/)")

skills = ["analyst_instructions.md", "writer_instructions.md", "leader_instructions.md"]
for skill in skills:
    path = os.path.join("skills", skill)
    try:
        assert os.path.exists(path), f"No existe: {path}"
        with open(path, encoding="utf-8") as f:
            content = f.read()
        assert len(content) > 100, "Fichero demasiado corto"
        ok(f"{skill} ({len(content)} chars)")
    except Exception as e:
        fail(skill, str(e))


# ── 6. Estructura de directorios ─────────────────────────────────────────────
section("6. Estructura de directorios")

dirs = ["data/raw", "data/analysis", "output", "logs", "agents", "skills"]
for d in dirs:
    if os.path.isdir(d):
        ok(f"{d}/")
    else:
        fail(f"{d}/ no existe")


# ── Resumen ──────────────────────────────────────────────────────────────────
print(f"\n{'═'*40}")
total = passed + failed
print(f"Resultado: {passed}/{total} tests pasaron")
if failed == 0:
    print(f"{GREEN}✓ Todos los tests pasaron{RESET}")
    sys.exit(0)
else:
    print(f"{RED}✗ {failed} test(s) fallaron{RESET}")
    sys.exit(1)
