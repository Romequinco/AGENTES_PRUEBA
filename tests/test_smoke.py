"""
test_smoke.py
=============
Smoke tests del sistema. Verifica que todos los módulos cargan, que los
indicadores técnicos se calculan correctamente y que el pipeline arranca
sin errores de importación.

No requiere API key real ni conexión a mercados (usa datos sintéticos).
"""

import sys
import json
import os
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── 1. Imports ───────────────────────────────────────────────────────────────

def test_import_ibex_data():
    from agents.ibex_data import get_ibex35_components, EMERGENCY_FALLBACK  # noqa: F401


def test_import_researcher():
    from agents.researcher import ResearcherAgent, _rsi, _sma, _macd, _atr, _bollinger, _compute_indicators  # noqa: F401


def test_import_analyst():
    from agents.analyst import AnalystAgent  # noqa: F401


def test_import_writer():
    from agents.writer import WriterAgent  # noqa: F401


def test_import_leader():
    from agents.leader import LeaderAgent  # noqa: F401


# ── 2. Indicadores técnicos ──────────────────────────────────────────────────

@pytest.fixture(scope="module")
def synthetic_prices():
    np.random.seed(42)
    n = 60
    prices = pd.Series(100 + np.cumsum(np.random.randn(n) * 0.5 + 0.1))
    highs = prices + np.abs(np.random.randn(n) * 0.3)
    lows = prices - np.abs(np.random.randn(n) * 0.3)
    return prices, highs, lows, n


def test_rsi(synthetic_prices):
    from agents.researcher import _rsi
    prices, _, _, _ = synthetic_prices
    rsi = _rsi(prices, 14)
    assert rsi is not None, "RSI devolvió None"
    assert 0 <= rsi <= 100, f"RSI fuera de rango: {rsi}"


def test_rsi_insufficient_data(synthetic_prices):
    from agents.researcher import _rsi
    prices, _, _, _ = synthetic_prices
    rsi_short = _rsi(prices[:5], 14)
    assert rsi_short is None, "RSI con datos insuficientes debería devolver None"


def test_sma(synthetic_prices):
    from agents.researcher import _sma
    prices, _, _, _ = synthetic_prices
    ma20 = _sma(prices, 20)
    ma50 = _sma(prices, 50)
    assert ma20 is not None
    assert ma50 is not None


def test_macd(synthetic_prices):
    from agents.researcher import _macd
    prices, _, _, _ = synthetic_prices
    macd = _macd(prices)
    assert all(k in macd for k in ("macd", "signal", "histogram"))
    assert macd["macd"] is not None


def test_atr(synthetic_prices):
    from agents.researcher import _atr
    prices, highs, lows, _ = synthetic_prices
    atr = _atr(highs, lows, prices, 14)
    assert atr is not None
    assert atr > 0


def test_bollinger(synthetic_prices):
    from agents.researcher import _bollinger
    prices, _, _, _ = synthetic_prices
    bb = _bollinger(prices, 20)
    assert all(k in bb for k in ("upper", "middle", "lower", "bandwidth"))
    assert bb["upper"] > bb["middle"] > bb["lower"]


def test_compute_indicators(synthetic_prices):
    from agents.researcher import _compute_indicators
    prices, highs, lows, n = synthetic_prices
    hist_df = pd.DataFrame({
        "Close": prices.values,
        "High": highs.values,
        "Low": lows.values,
        "Volume": np.random.randint(1_000_000, 5_000_000, n),
    })
    ind = _compute_indicators(hist_df)
    required = ["rsi_14", "ma_20", "ma_50", "macd", "atr_14", "bollinger_upper"]
    missing = [k for k in required if k not in ind]
    assert not missing, f"Faltan campos: {missing}"


# ── 3. ibex_data emergency fallback ─────────────────────────────────────────

def test_emergency_fallback():
    from agents.ibex_data import EMERGENCY_FALLBACK
    fb = EMERGENCY_FALLBACK
    assert len(fb["tickers"]) > 20, "Fallback tiene menos de 20 tickers"
    assert len(fb["names"]) > 0
    assert len(fb["aliases"]) > 0


def test_get_ibex35_from_cache():
    import tempfile
    from agents.ibex_data import EMERGENCY_FALLBACK, get_ibex35_components
    with tempfile.TemporaryDirectory() as tmpdir:
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


# ── 4. Analyst — build_prompt con datos sintéticos ──────────────────────────

def test_analyst_build_prompt():
    import pytz
    from agents.analyst import AnalystAgent

    np.random.seed(42)
    n = 60
    prices = pd.Series(100 + np.cumsum(np.random.randn(n) * 0.5 + 0.1))

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
    agent.madrid = pytz.timezone("Europe/Madrid")

    skills_dir = os.path.join(PROJECT_ROOT, ".claude", "skills")
    with open(os.path.join(skills_dir, "analyst_instructions.md"), encoding="utf-8") as f:
        agent.system_prompt = f.read()

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
    assert len(prompt) > 100


# ── 5. Ficheros de instrucciones ─────────────────────────────────────────────

@pytest.mark.parametrize("skill", [
    "analyst_instructions.md",
    "writer_instructions.md",
    "leader_instructions.md",
])
def test_skill_file_exists(skill):
    path = os.path.join(PROJECT_ROOT, ".claude", "skills", skill)
    assert os.path.exists(path), f"No existe: {path}"
    with open(path, encoding="utf-8") as f:
        content = f.read()
    assert len(content) > 100, f"{skill} demasiado corto"


# ── 6. Estructura de directorios ─────────────────────────────────────────────

@pytest.mark.parametrize("d", [
    "data/raw", "data/analysis", "output", "logs", "agents", ".claude/skills"
])
def test_directory_exists(d):
    path = os.path.join(PROJECT_ROOT, d)
    assert os.path.isdir(path), f"{d}/ no existe"
