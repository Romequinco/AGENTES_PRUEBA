import os
import json
import time
import logging
from datetime import datetime

import pandas as pd
import anthropic

from agents.utils import MADRID_TZ, strip_markdown_fence, load_instructions

logger = logging.getLogger("bolsa.analyst")


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
        self.madrid = MADRID_TZ
        self.system_prompt = self._load_instructions()

    def _load_instructions(self) -> str:
        return load_instructions("analyst_instructions.md")

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
        macro_path = os.path.join(self.raw_dir, f"macro_{self.date}.json")

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

        macro = {}
        if os.path.exists(macro_path):
            with open(macro_path, encoding="utf-8") as f:
                macro = json.load(f)

        calendar_path = os.path.join(self.raw_dir, f"calendar_{self.date}.json")
        calendar = {}
        if os.path.exists(calendar_path):
            with open(calendar_path, encoding="utf-8") as f:
                calendar = json.load(f)

        return {"prices_df": df, "news": news, "indicators": indicators, "macro": macro, "calendar": calendar}

    def build_prompt(self, data: dict, correction_feedback: str = "") -> str:
        df = data["prices_df"]
        news = data["news"]
        indicators = data["indicators"]
        macro = data.get("macro", {})
        calendar = data.get("calendar", {})

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

        # Resumen de indicadores técnicos + volumen anómalo + posición 52W + contribución
        tech_summary = self._build_tech_summary(indicators.get("tickers", {}), valid)
        volume_alerts_summary = self._build_volume_alerts(indicators.get("tickers", {}))
        range_summary = self._build_range_summary(indicators.get("tickers", {}))
        attribution_summary = self._build_attribution_summary(indicators.get("tickers", {}), ibex_idx)

        news_items = news.get("news", [])[:25]
        news_text = json.dumps(news_items, ensure_ascii=False, indent=2)

        # Contexto macro europeo
        macro_text = self._build_macro_summary(macro)

        calendar_text = self._build_calendar_summary(calendar)
        correction = f"\n\nNOTA DE CORRECCIÓN: {correction_feedback}\n" if correction_feedback else ""

        return (
            f"Analiza los datos del mercado español del {self.date}.\n\n"
            f"=== CONTEXTO MACRO EUROPEO ===\n{macro_text}\n\n"
            f"=== DATOS DE PRECIOS Y MERCADO ===\n{prices_summary}\n\n"
            f"=== ATRIBUCIÓN DE MOVIMIENTO DEL IBEX (contribución en puntos) ===\n{attribution_summary}\n\n"
            f"=== INDICADORES TÉCNICOS (RSI, MA, MACD, ATR, Bollinger) ===\n{tech_summary}\n\n"
            f"=== ALERTAS DE VOLUMEN INUSUAL (ratio vs media 20 días) ===\n{volume_alerts_summary}\n\n"
            f"=== POSICIÓN EN RANGO 52 SEMANAS ===\n{range_summary}\n\n"
            f"=== CALENDARIO ECONÓMICO PRÓXIMOS 7 DÍAS ===\n{calendar_text}\n\n"
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

    def _build_macro_summary(self, macro: dict) -> str:
        """Construye resumen del contexto macro europeo."""
        if not macro or not macro.get("macro"):
            return "Datos macro no disponibles"
        lines = []
        for ticker, d in macro["macro"].items():
            name = d.get("name", ticker)
            chg = d.get("change_pct")
            close = d.get("close")
            ytd = d.get("ytd_pct")
            chg_str = f"{chg:+.2f}%" if chg is not None else "N/D"
            ytd_str = f" (YTD: {ytd:+.2f}%)" if ytd is not None else ""
            lines.append(f"{name}: {close} | {chg_str}{ytd_str}")
        return "\n".join(lines) if lines else "Sin datos macro"

    def _build_volume_alerts(self, indicators: dict) -> str:
        """Lista acciones con volumen inusual (ratio > 2.0x respecto a media 20 días)."""
        if not indicators:
            return "Sin datos de volumen"
        alerts = []
        for ticker, ind in indicators.items():
            ratio = ind.get("volume_ratio")
            signal = ind.get("volume_signal", "normal")
            if ratio and ratio > 2.0:
                chg = ind.get("change_pct", 0) or 0
                direction = "subida" if chg > 0 else ("bajada" if chg < 0 else "lateral")
                alerts.append(
                    f"{ticker.replace('.MC','')} — ratio={ratio}x ({signal}) | "
                    f"cambio={chg:+.2f}% {direction} | avg20d={ind.get('avg_volume_20d', 'N/D')}"
                )
        if not alerts:
            return "Ningún valor con volumen inusual (todos por debajo de 2x la media 20 días)"
        return "\n".join(sorted(alerts, key=lambda x: float(x.split("ratio=")[1].split("x")[0]), reverse=True))

    def _build_range_summary(self, indicators: dict) -> str:
        """Muestra la posición de cada ticker en su rango de 52 semanas."""
        if not indicators:
            return "Sin datos de rango 52W"
        near_high, near_low, low_range, mid_range = [], [], [], []
        for ticker, ind in indicators.items():
            pct = ind.get("range_52w_pct")
            flag = ind.get("range_52w_flag")
            if pct is None:
                continue
            short = ticker.replace(".MC", "")
            entry = f"{short} ({pct:.1f}%)"
            if flag == "near_high":
                near_high.append(entry)
            elif flag == "near_low":
                near_low.append(entry)
            elif flag == "low_range":
                low_range.append(entry)
            else:
                mid_range.append(entry)
        lines = []
        if near_high:
            lines.append(f"CERCA MÁXIMO ANUAL (>90%): {', '.join(near_high)}")
        if near_low:
            lines.append(f"CERCA MÍNIMO ANUAL (<10%): {', '.join(near_low)}")
        if low_range:
            lines.append(f"RANGO BAJO (10-30%): {', '.join(low_range)}")
        if mid_range:
            lines.append(f"RANGO MEDIO (30-90%): {', '.join(mid_range)}")
        return "\n".join(lines) if lines else "Sin datos de rango 52W"

    def _build_attribution_summary(self, indicators: dict, ibex_idx: dict) -> str:
        """Muestra la contribución en puntos de cada acción al movimiento del IBEX."""
        if not indicators or not ibex_idx.get("change_abs"):
            return "Datos de atribución no disponibles"
        contribs = []
        for ticker, ind in indicators.items():
            pts = ind.get("contribution_pts")
            weight = ind.get("market_cap_weight_pct")
            if pts is not None:
                name = ind.get("name", ticker.replace(".MC", ""))
                contribs.append((pts, ticker.replace(".MC", ""), name, weight or 0))
        if not contribs:
            return "Sin datos de contribución"
        contribs.sort(key=lambda x: x[0], reverse=True)
        ibex_total = ibex_idx.get("change_abs", 0)
        lines = [f"Movimiento total IBEX: {ibex_total:+.2f} pts"]
        lines.append("Top contribuyentes positivos:")
        for pts, short, name, w in contribs[:5]:
            if pts > 0:
                lines.append(f"  {short} ({name}): {pts:+.2f} pts | peso {w:.1f}%")
        lines.append("Top contribuyentes negativos:")
        for pts, short, name, w in reversed(contribs[-5:]):
            if pts < 0:
                lines.append(f"  {short} ({name}): {pts:+.2f} pts | peso {w:.1f}%")
        return "\n".join(lines)

    def _build_calendar_summary(self, calendar: dict) -> str:
        """Construye resumen del calendario económico para el prompt."""
        if not calendar or not calendar.get("events"):
            return "Calendario económico no disponible (configura FINNHUB_API_KEY para activarlo)"
        events = calendar["events"]
        lines = [f"Rango: {calendar.get('date_range', 'N/D')}"]
        for e in events[:15]:
            impact = e.get("impact", "").upper()
            impact_marker = "🔴" if impact == "HIGH" else ("🟡" if impact == "MEDIUM" else "⚪")
            actual = f" | Actual: {e['actual']}" if e.get("actual") is not None else ""
            estimate = f" | Est: {e['estimate']}" if e.get("estimate") is not None else ""
            lines.append(
                f"{e.get('date','')} {impact_marker} [{e.get('country','')}] "
                f"{e.get('event','')}{actual}{estimate}"
            )
        return "\n".join(lines)

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
                    timeout=300.0,
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
                raw = strip_markdown_fence(raw)
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
