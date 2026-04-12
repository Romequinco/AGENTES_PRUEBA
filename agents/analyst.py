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
        errors = []
        try:
            data = self.load_data()
            analysis = self.analyze(data)
            analysis_file = self.save_analysis(analysis)
            return {"analysis_file": analysis_file, "status": "ok", "errors": errors}
        except Exception as e:
            errors.append(str(e))
            logger.error(f"Analyst fallido: {e}")
            return {"analysis_file": None, "status": "error", "errors": errors}

    def load_data(self) -> dict:
        prices_path = os.path.join(self.raw_dir, f"ibex35_prices_{self.date}.csv")
        news_path = os.path.join(self.raw_dir, f"ibex35_news_{self.date}.json")

        if not os.path.exists(prices_path):
            raise AnalystError(f"Fichero de precios no encontrado: {prices_path}")

        df = pd.read_csv(prices_path, encoding="utf-8")
        with open(news_path, encoding="utf-8") as f:
            news = json.load(f)

        return {"prices_df": df, "news": news}

    def build_prompt(self, data: dict, correction_feedback: str = "") -> str:
        df = data["prices_df"]
        news = data["news"]

        valid = df[df["error"].isna() | (df["error"] == "")]
        top5_up = valid.nlargest(5, "change_pct")[["ticker", "name", "change_pct", "close", "volume"]].to_string(index=False)
        top5_dn = valid.nsmallest(5, "change_pct")[["ticker", "name", "change_pct", "close", "volume"]].to_string(index=False)
        avg_change = valid["change_pct"].mean()
        total_volume = valid["volume"].sum()
        ibex_row = valid[valid["ticker"] == "^IBEX"] if "^IBEX" in valid["ticker"].values else None

        prices_summary = (
            f"Fecha: {self.date}\n"
            f"Variación media del índice: {avg_change:.2f}%\n"
            f"Volumen total: {total_volume:,.0f}\n"
            f"Tickers con datos: {len(valid)}/35\n\n"
            f"TOP 5 SUBIDAS:\n{top5_up}\n\n"
            f"TOP 5 BAJADAS:\n{top5_dn}\n\n"
            f"DATOS COMPLETOS (CSV):\n{valid[['ticker','name','open','high','low','close','change_pct','volume']].to_string(index=False)}"
        )

        news_items = news.get("news", [])[:30]
        news_text = json.dumps(news_items, ensure_ascii=False, indent=2)

        correction = f"\n\nNOTA DE CORRECCIÓN: {correction_feedback}\n" if correction_feedback else ""

        return (
            f"Analiza los siguientes datos del mercado español del {self.date}.\n\n"
            f"=== DATOS DE PRECIOS ===\n{prices_summary}\n\n"
            f"=== NOTICIAS DEL DÍA ===\n{news_text}\n"
            f"{correction}"
            f"\nResponde ÚNICAMENTE con el JSON de análisis. Sin texto adicional ni bloques markdown."
        )

    def analyze(self, data: dict) -> dict:
        last_error = ""
        for attempt in range(self.max_retries):
            try:
                prompt = self.build_prompt(data, correction_feedback=last_error if attempt > 0 else "")
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=4096,
                    system=self.system_prompt,
                    messages=[{"role": "user", "content": prompt}],
                )
                raw = response.content[0].text.strip()
                raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
                return json.loads(raw)
            except json.JSONDecodeError as e:
                last_error = f"JSON inválido en intento {attempt + 1}: {e}. Asegúrate de responder solo con JSON válido."
                logger.warning(f"Analyst intento {attempt + 1}: JSON inválido, reintentando...")
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
