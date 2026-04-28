import os
import json
import time
import logging
from datetime import datetime

import anthropic

from agents.researcher import ResearcherAgent, ResearcherError
from agents.analyst import AnalystAgent, AnalystError
from agents.writer import WriterAgent, WriterError
from agents.ibex_data import get_ibex35_components
from agents.utils import MADRID_TZ, strip_markdown_fence, load_instructions

logger = logging.getLogger("bolsa.leader")


class PipelineError(Exception):
    pass


class ValidationError(Exception):
    pass


class LeaderAgent:
    def __init__(self, config: dict, ext_logger=None):
        self.config = config
        self.client = anthropic.Anthropic(api_key=config["api_key"])
        self.model = config.get("model_leader", "claude-haiku-4-5-20251001")
        self.max_retries = int(config.get("max_retries", 3))
        self.retry_delay = int(config.get("retry_delay", 5))
        self.analysis_dir = config.get("data_analysis_dir", "data/analysis")
        self.output_dir = config.get("output_dir", "output")
        self.madrid = MADRID_TZ
        self.system_prompt = self._load_instructions()
        self.logger = ext_logger.getChild("leader") if ext_logger else logger

    def _load_instructions(self) -> str:
        return load_instructions("leader_instructions.md")

    def run(self, date: str | None = None) -> dict:
        date = date or datetime.now(self.madrid).strftime("%Y-%m-%d")
        self.logger.info(f"=== Líder iniciando pipeline para {date} ===")
        start = time.time()
        errors = []

        # Carga de componentes IBEX 35 — primer paso obligatorio antes de todo
        self.logger.info("[ 0/3 ] Cargando composición actual del IBEX 35...")
        cache_dir = os.path.dirname(self.config.get("data_raw_dir", "data/raw")) or "data"
        try:
            components = get_ibex35_components(
                cache_dir=cache_dir,
                max_cache_age_days=int(self.config.get("ibex_cache_days", 7)),
            )
            self.logger.info(
                f"[ 0/3 ] IBEX 35: {len(components['tickers'])} componentes "
                f"(fuente: {components.get('source')}, fecha: {components.get('last_updated')})"
            )
        except Exception as e:
            result = {"status": "failed", "pdf_path": None, "date": date, "errors": [f"Error cargando IBEX 35: {e}"]}
            self.log_run_summary(result, elapsed=time.time() - start)
            return result

        pipeline_retries = min(self.max_retries, 3)
        for attempt in range(pipeline_retries):
            try:
                result = self.execute_pipeline(date, components, skip_researcher=(attempt > 0))
                self.log_run_summary(result, elapsed=time.time() - start)
                return result
            except PipelineError as e:
                errors.append(str(e))
                self.logger.error(f"Pipeline intento {attempt + 1} fallido: {e}")
                if attempt < pipeline_retries - 1:
                    self.logger.info(f"Reintentando en {self.retry_delay}s...")
                    time.sleep(self.retry_delay)
            except Exception as e:
                errors.append(str(e))
                self.logger.error(f"Error inesperado en pipeline: {e}")
                break

        result = {"status": "failed", "pdf_path": None, "date": date, "errors": errors}
        self.log_run_summary(result, elapsed=time.time() - start)
        return result

    def execute_pipeline(self, date: str, components: dict, skip_researcher: bool = False) -> dict:
        # Fase 1: Researcher
        if not skip_researcher:
            self.logger.info("[ 1/3 ] Ejecutando Researcher...")
            researcher = ResearcherAgent(date, self.config, components=components)
            r_result = researcher.run()
            if r_result["status"] == "error":
                raise PipelineError(f"Researcher falló: {r_result['errors']}")
            self.logger.info(f"[ 1/3 ] Researcher completado ({r_result['status']})")
        else:
            self.logger.info("[ 1/3 ] Researcher omitido (datos ya existentes)")

        # Fase 2: Analyst
        self.logger.info("[ 2/3 ] Ejecutando Analyst...")
        analyst = AnalystAgent(date, self.config)
        a_result = analyst.run()
        if a_result["status"] == "error":
            raise PipelineError(f"Analyst falló: {a_result['errors']}")
        self.logger.info(f"[ 2/3 ] Analyst completado")

        # Fase 3: Writer
        self.logger.info("[ 3/3 ] Ejecutando Writer...")
        writer = WriterAgent(date, self.config)
        w_result = writer.run()
        if w_result["status"] == "error":
            raise PipelineError(f"Writer falló: {w_result['errors']}")
        pdf_path = w_result["pdf_file"]
        self.logger.info(f"[ 3/3 ] Writer completado: {pdf_path}")

        # Validación
        self.logger.info("[ QA ] Validando output...")
        validation = self.validate_output(date, pdf_path)
        self.logger.info(f"[ QA ] Score: {validation['score']}/100 — {validation['recommendation'].upper()}")

        if validation["recommendation"] == "abort":
            raise PipelineError(f"Validación abortó el pipeline. Issues: {validation['issues']}")
        if validation["recommendation"] == "retry":
            raise PipelineError(f"Validación requiere reintento. Issues: {validation['issues']}")

        self._mark_approved(date)
        return {"status": "success", "pdf_path": pdf_path, "date": date, "errors": [], "validation": validation}

    def validate_output(self, date: str, pdf_path: str) -> dict:
        analysis_path = os.path.join(self.analysis_dir, f"ibex35_analysis_{date}.json")

        try:
            with open(analysis_path, encoding="utf-8") as f:
                analysis = json.load(f)
        except Exception as e:
            return {"validation_passed": False, "score": 0, "issues": [f"No se pudo leer análisis: {e}"], "recommendation": "abort"}

        pdf_size_kb = 0
        pdf_exists = pdf_path and os.path.exists(pdf_path)
        if pdf_exists:
            pdf_size_kb = os.path.getsize(pdf_path) / 1024

        prompt = (
            f"Valida el siguiente análisis del IBEX 35 para la fecha {date}.\n\n"
            f"=== ANÁLISIS JSON ===\n{json.dumps(analysis, ensure_ascii=False, indent=2)}\n\n"
            f"=== MÉTRICAS DEL PDF ===\n"
            f"PDF existe: {pdf_exists}\n"
            f"Tamaño PDF: {pdf_size_kb:.0f} KB\n"
            f"Fecha esperada: {date}\n\n"
            f"Responde ÚNICAMENTE con el JSON de validación. Sin texto adicional."
        )

        last_error = ""
        for attempt in range(self.max_retries):
            try:
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=1024,
                    system=self.system_prompt,
                    messages=[{"role": "user", "content": prompt}],
                    timeout=300.0,
                )
                raw = response.content[0].text.strip()
                raw = strip_markdown_fence(raw)
                return json.loads(raw)
            except json.JSONDecodeError as e:
                last_error = str(e)
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay)
            except anthropic.APIError as e:
                last_error = str(e)
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay)

        self.logger.warning("Líder: validación LLM fallida, usando validación básica")
        return self._basic_validation(analysis, pdf_size_kb)

    def _basic_validation(self, analysis: dict, pdf_size_kb: float) -> dict:
        issues = []
        score = 0

        # Campos obligatorios de primer nivel (15 pts)
        required_fields = ["market_summary", "top_gainers", "top_losers", "sector_analysis", "ideas_vigilar"]
        missing = [f for f in required_fields if f not in analysis]
        if not missing:
            score += 15
        else:
            issues.append(f"Campos faltantes: {missing}")

        # Cantidad de gainers/losers con subcampos mínimos (15 pts)
        gainers = analysis.get("top_gainers", [])
        losers = analysis.get("top_losers", [])
        required_subfields = {"ticker", "name", "change_pct"}
        gainers_ok = len(gainers) >= 3 and all(required_subfields.issubset(g) for g in gainers)
        losers_ok = len(losers) >= 3 and all(required_subfields.issubset(l) for l in losers)
        if gainers_ok and losers_ok:
            score += 15
        else:
            issues.append(f"Gainers válidos: {len(gainers)}, Losers válidos: {len(losers)} (mínimo 3 con ticker/name/change_pct)")

        # Gainers y losers no son los mismos tickers (10 pts)
        gainer_tickers = {g.get("ticker") for g in gainers}
        loser_tickers = {l.get("ticker") for l in losers}
        if not gainer_tickers & loser_tickers:
            score += 10
        else:
            issues.append(f"Tickers repetidos entre gainers y losers: {gainer_tickers & loser_tickers}")

        # Sectores (mínimo 4) (10 pts)
        sectors = analysis.get("sector_analysis", [])
        if len(sectors) >= 4:
            score += 10
        else:
            issues.append(f"Sectores insuficientes: {len(sectors)} (mínimo 4)")

        # ideas_vigilar no vacío (10 pts)
        highlights = analysis.get("ideas_vigilar", analysis.get("actionable_ideas", []))
        if isinstance(highlights, list) and len(highlights) > 0:
            score += 10
        else:
            issues.append("ideas_vigilar vacío o ausente")

        # Nuevas secciones de valor (20 pts = 5+5+5+5)
        macro_ctx = analysis.get("macro_context", {})
        if macro_ctx and macro_ctx.get("overall_interpretation"):
            score += 5
        else:
            issues.append("macro_context ausente o sin overall_interpretation")

        attribution = analysis.get("movement_attribution", {})
        if attribution and attribution.get("top_positive_contributors"):
            score += 5
        else:
            issues.append("movement_attribution ausente o sin contributors")

        actionable = analysis.get("ideas_vigilar", analysis.get("actionable_ideas", []))
        if isinstance(actionable, list) and len(actionable) >= 1:
            score += 5
        else:
            issues.append("ideas_vigilar ausente o vacío")

        tech_signals = analysis.get("technical_signals", [])
        if len(tech_signals) <= 8:
            score += 5
        else:
            issues.append(f"technical_signals excede máximo de 8: {len(tech_signals)}")

        range_ext = analysis.get("range_extremes", {})
        if isinstance(range_ext, dict) and ("near_52w_high" in range_ext or "near_52w_low" in range_ext):
            score += 5
        else:
            issues.append("range_extremes ausente")

        # Tamaño del PDF (20 pts)
        if pdf_size_kb > 100:
            score += 20
        else:
            issues.append(f"PDF demasiado pequeño: {pdf_size_kb:.0f}KB (mínimo 100KB)")

        recommendation = "approved" if score >= 70 else ("retry" if score >= 40 else "abort")
        return {"validation_passed": score >= 70, "score": score, "issues": issues, "recommendation": recommendation}

    def _mark_approved(self, date: str):
        analysis_path = os.path.join(self.analysis_dir, f"ibex35_analysis_{date}.json")
        try:
            with open(analysis_path, encoding="utf-8") as f:
                analysis = json.load(f)
            analysis["validation_status"] = "approved"
            with open(analysis_path, "w", encoding="utf-8") as f:
                json.dump(analysis, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.logger.warning(f"No se pudo actualizar validation_status: {e}")

    def log_run_summary(self, result: dict, elapsed: float = 0):
        status = result.get("status", "unknown")
        pdf = result.get("pdf_path", "—")
        errors = result.get("errors", [])
        validation = result.get("validation", {})
        self.logger.info(
            f"=== Resumen del pipeline ===\n"
            f"  Estado:    {status.upper()}\n"
            f"  Fecha:     {result.get('date', '—')}\n"
            f"  PDF:       {pdf}\n"
            f"  QA Score:  {validation.get('score', '—')}/100\n"
            f"  Tiempo:    {elapsed:.0f}s\n"
            f"  Errores:   {errors if errors else 'ninguno'}"
        )
        self._save_last_run(result, elapsed)

    def _save_last_run(self, result: dict, elapsed: float):
        """Persiste el estado del último pipeline en output/last_run.json."""
        validation = result.get("validation", {})
        payload = {
            "status": result.get("status", "unknown"),
            "date": result.get("date", ""),
            "pdf_path": result.get("pdf_path"),
            "qa_score": validation.get("score"),
            "qa_recommendation": validation.get("recommendation"),
            "qa_issues": validation.get("issues", []),
            "errors": result.get("errors", []),
            "elapsed_seconds": round(elapsed, 1),
            "timestamp": datetime.now(self.madrid).isoformat(),
            "model_leader": self.model,
        }
        try:
            os.makedirs(self.output_dir, exist_ok=True)
            out_path = os.path.join(self.output_dir, "last_run.json")
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            self.logger.info(f"Estado guardado en {out_path}")
        except Exception as e:
            self.logger.warning(f"No se pudo guardar last_run.json: {e}")
