import os
import json
import time
import logging
from datetime import datetime

import pytz
import pandas as pd
import anthropic
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("bolsa.analyst")

SKILLS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "skills")


def _strip_markdown_fence(text: str) -> str:
    """Extrae el contenido de un bloque ```json ... ``` o ``` ... ```."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]  # quitar línea de apertura (```json o ```)
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return text


class AnalystError(Exception):
    pass


class AnalystAgent:
    def __init__(self, date: str, config: dict):
        self.date = date
        self.config = config
        self.client = anthropic.Anthropic(api_key=config["api_key"])
        self.model = config.get("model_analyst", "claude-haiku-4-5-20251001")
        self.max_retries = int(config.get("max_retries", 3))
        self.retry_delay = int(config.get("retry_delay", 5))
        self.raw_dir = config.get("data_raw_dir", "data/raw")
        self.analysis_dir = config.get("data_analysis_dir", "data/analysis")
        self.madrid = pytz.timezone("Europe/Madrid")
        self.system_prompt = self._load_instructions()

    def _load_instructions(self) -> str:
        path = os.path.join(SKILLS_DIR, "analyst_instructions.md")
        with open(path, encoding="utf-8") as f:
            return f.read()

    def run(self) -> dict:
        # Caché: si el análisis del día ya existe y está aprobado, no repetir llamada LLM
        analysis_path = os.path.join(self.analysis_dir, f"ibex35_analysis_{self.date}.json")
        if os.path.exists(analysis_path):
            try:
                with open(analysis_path, encoding="utf-8") as f:
                    cached = json.load(f)
                if cached.get("validation_status") == "approved":
                    logger.info(f"Análisis de {self.date} ya existe y está aprobado. Omitiendo LLM.")
                    return {"analysis_file": analysis_path, "status": "ok", "errors": []}
            except Exception:
                pass  # Si hay error leyendo el caché, regenerar

        try:
            data = self.load_data()
            analysis = self.analyze(data)
            analysis_file = self.save_analysis(analysis)
            return {"analysis_file": analysis_file, "status": "ok", "errors": []}
        except Exception as e:
            logger.error(f"Analyst fallido: {e}")
            return {"analysis_file": None, "status": "error", "errors": [str(e)]}

    def load_data(self) -> dict:
        prices_path = os.path.join(self.raw_dir, f"ibex35_prices_{self.date}.csv")
        news_path = os.path.join(self.raw_dir, f"ibex35_news_{self.date}.json")
        indicators_path = os.path.join(self.raw_dir, f"ibex35_indicators_{self.date}.json")

        if not os.path.exists(prices_path):
            raise AnalystError(f"Fichero de precios no encontrado: {prices_path}")

        df = pd.read_csv(prices_path, encoding="utf-8")

        news = {}
        if os.path.exists(news_path):
            with open(news_path, encoding="utf-8") as f:
                news = json.load(f)

        indicators = {}
        if os.path.exists(indicators_path):
            with open(indicators_path, encoding="utf-8") as f:
                indicators = json.load(f)

        return {"prices_df": df, "news": news, "indicators": indicators}

    def build_prompt(self, data: dict, correction_feedback: str = "") -> str:
        df = data["prices_df"]
        news = data["news"]
        indicators = data["indicators"]

        valid = df[df["error"].isna() | (df["error"] == "")].copy()
        valid["change_pct"] = pd.to_numeric(valid["change_pct"], errors="coerce")
        valid["volume"] = pd.to_numeric(valid["volume"], errors="coerce")

        top5_up = valid.nlargest(5, "change_pct")[["ticker", "name", "change_pct", "close", "volume"]].to_string(index=False)
        top5_dn = valid.nsmallest(5, "change_pct")[["ticker", "name", "change_pct", "close", "volume"]].to_string(index=False)

        # Datos reales del índice ^IBEX
        ibex_idx = indicators.get("ibex_index", {})
        if ibex_idx.get("change_pct") is not None:
            ibex_line = (
                f"^IBEX (índice real): {ibex_idx['close']} pts | "
                f"Variación: {ibex_idx['change_pct']:+.2f}% ({ibex_idx['change_abs']:+.2f} pts)"
            )
        else:
            avg_change = valid["change_pct"].mean()
            ibex_line = f"Variación media componentes: {avg_change:.2f}% (^IBEX no disponible)"

        total_volume = valid["volume"].sum()

        # Sectores dinámicos calculados desde los datos reales
        sectors_data = self._compute_sectors(valid)

        prices_summary = (
            f"Fecha: {self.date}\n"
            f"{ibex_line}\n"
            f"Volumen total: {total_volume:,.0f}\n"
            f"Tickers con datos: {len(valid)}\n\n"
            f"TOP 5 SUBIDAS:\n{top5_up}\n\n"
            f"TOP 5 BAJADAS:\n{top5_dn}\n\n"
            f"SECTORES (calculado):\n{sectors_data}\n\n"
            f"DATOS COMPLETOS (CSV):\n"
            f"{valid[['ticker','name','sector','open','high','low','close','change_pct','volume']].to_string(index=False)}"
        )

        # Resumen de indicadores técnicos (solo los más relevantes para no saturar el prompt)
        tech_summary = self._build_tech_summary(indicators.get("tickers", {}), valid)

        news_items = news.get("news", [])[:25]
        news_text = json.dumps(news_items, ensure_ascii=False, indent=2)

        correction = f"\n\nNOTA DE CORRECCIÓN: {correction_feedback}\n" if correction_feedback else ""

        return (
            f"Analiza los datos del mercado español del {self.date}.\n\n"
            f"=== DATOS DE PRECIOS Y MERCADO ===\n{prices_summary}\n\n"
            f"=== INDICADORES TÉCNICOS (RSI, MA, MACD, ATR, Bollinger) ===\n{tech_summary}\n\n"
            f"=== NOTICIAS DEL DÍA ===\n{news_text}\n"
            f"{correction}"
            f"\nResponde ÚNICAMENTE con el JSON de análisis. Sin texto adicional ni bloques markdown."
        )

    def _compute_sectors(self, valid: pd.DataFrame) -> str:
        """Calcula variación media por sector directamente desde los datos."""
        if "sector" not in valid.columns:
            return "Sin datos de sector"
        sector_groups = valid.dropna(subset=["change_pct", "sector"])
        sector_groups = sector_groups[sector_groups["sector"] != ""]
        if sector_groups.empty:
            return "Sin datos de sector"
        summary = (
            sector_groups.groupby("sector")["change_pct"]
            .agg(["mean", "count"])
            .round(2)
            .rename(columns={"mean": "var_media_%", "count": "n_tickers"})
            .sort_values("var_media_%", ascending=False)
        )
        return summary.to_string()

    def _build_tech_summary(self, indicators: dict, valid: pd.DataFrame) -> str:
        """Construye resumen compacto de indicadores para el prompt."""
        if not indicators:
            return "Indicadores no disponibles"

        lines = []
        # Ordenar por variación para que el LLM vea los más relevantes primero
        tickers_sorted = valid.sort_values("change_pct", ascending=False)["ticker"].tolist()

        for ticker in tickers_sorted:
            ind = indicators.get(ticker)
            if not ind:
                continue
            parts = [f"{ticker.replace('.MC','')}"]
            if ind.get("rsi_14") is not None:
                parts.append(f"RSI={ind['rsi_14']} ({ind.get('rsi_signal','')})")
            if ind.get("ma_20") and ind.get("close"):
                rel = ">" if ind["close"] > ind["ma_20"] else "<"
                parts.append(f"precio {rel} MA20={ind['ma_20']}")
            if ind.get("ma_50") and ind.get("close"):
                rel = ">" if ind["close"] > ind["ma_50"] else "<"
                parts.append(f"MA50={ind['ma_50']}")
            if ind.get("macd_trend"):
                parts.append(f"MACD={ind['macd_trend']} (hist={ind.get('macd_histogram')})")
            if ind.get("bollinger_bandwidth"):
                bw = ind["bollinger_bandwidth"]
                bb_note = "compresión" if bw < 5 else ("expansión" if bw > 20 else "normal")
                parts.append(f"BB bw={bw}% ({bb_note})")
            if ind.get("atr_14"):
                parts.append(f"ATR={ind['atr_14']}")
            lines.append(" | ".join(parts))

        return "\n".join(lines) if lines else "Sin indicadores calculados"

    def analyze(self, data: dict) -> dict:
        last_error = ""
        for attempt in range(self.max_retries):
            try:
                prompt = self.build_prompt(data, correction_feedback=last_error if attempt > 0 else "")
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=16000,
                    system=self.system_prompt,
                    messages=[{"role": "user", "content": prompt}],
                )
                stop_reason = response.stop_reason
                content_len = len(response.content)
                raw = response.content[0].text.strip() if content_len > 0 else ""
                logger.info(f"Analyst respuesta: stop_reason={stop_reason}, content_blocks={content_len}, chars={len(raw)}")
                if not raw:
                    last_error = f"Respuesta vacía del modelo (stop_reason={stop_reason}). Responde SOLO con el JSON, sin texto previo."
                    logger.warning(f"Analyst intento {attempt + 1}: respuesta vacía (stop_reason={stop_reason})")
                    if attempt < self.max_retries - 1:
                        time.sleep(self.retry_delay)
                    continue
                raw = _strip_markdown_fence(raw)
                logger.info(f"Analyst primeros 300 chars: {raw[:300]}")
                return json.loads(raw)
            except json.JSONDecodeError as e:
                last_error = f"JSON inválido en intento {attempt + 1}: {e}. Responde solo con JSON válido sin bloques markdown."
                logger.warning(f"Analyst intento {attempt + 1}: JSON inválido ({e}), primeros 300 chars: {raw[:300] if 'raw' in locals() else 'N/A'}")
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay)
            except anthropic.APIError as e:
                last_error = str(e)
                logger.warning(f"Analyst intento {attempt + 1}: API error: {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay)

        raise AnalystError(f"Analyst agotó {self.max_retries} intentos. Último error: {last_error}")

    def save_analysis(self, analysis: dict) -> str:
        analysis["analysis_date"] = self.date
        analysis["analysis_timestamp"] = datetime.now(self.madrid).isoformat()
        analysis["model_used"] = self.model
        analysis["validation_status"] = "pending"

        os.makedirs(self.analysis_dir, exist_ok=True)
        out_path = os.path.join(self.analysis_dir, f"ibex35_analysis_{self.date}.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(analysis, f, ensure_ascii=False, indent=2)

        logger.info(f"Análisis guardado en {out_path}")
        return out_path
