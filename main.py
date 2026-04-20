import os
import sys
import logging
from datetime import datetime

import pytz
import yfinance as yf
from dotenv import load_dotenv

load_dotenv()


def ensure_directories():
    for d in ["data/raw", "data/analysis", "output", "logs"]:
        os.makedirs(d, exist_ok=True)


def setup_logging() -> logging.Logger:
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    date_str = datetime.now().strftime("%Y-%m-%d")
    log_file = os.path.join(os.getenv("LOGS_DIR", "logs"), f"run_{date_str}.log")

    logger = logging.getLogger("bolsa")
    logger.setLevel(getattr(logging, log_level, logging.INFO))

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(formatter)
    logger.addHandler(sh)

    return logger


def check_market_hours() -> bool:
    if os.getenv("FORCE_RUN", "").lower() == "true":
        return True
    madrid = pytz.timezone("Europe/Madrid")
    now = datetime.now(madrid)
    # Cubre 17:30–19:30 para absorber verano (18:30), invierno (17:30) y retrasos de Actions
    return (now.hour == 17 and now.minute >= 30) or now.hour == 18 or (now.hour == 19 and now.minute <= 30)


def get_last_market_date() -> str | None:
    """Devuelve la fecha del último día de mercado como 'YYYY-MM-DD', o None si no hay datos.

    Lógica:
    - Si hoy es día laborable y son ≥17:30 Madrid, asumimos que el mercado ya cerró hoy
      y devolvemos la fecha de hoy directamente, sin esperar a que yfinance actualice sus datos
      (yfinance puede tardar 30-60 min tras el cierre en reflejar el OHLCV del día).
    - En cualquier otro caso (festivo, fin de semana, antes del cierre) usamos el último
      dato disponible en yfinance como referencia.
    """
    madrid = pytz.timezone("Europe/Madrid")
    now = datetime.now(madrid)
    today_str = now.strftime("%Y-%m-%d")

    # Si es día laborable (lun–vie) y el mercado ya cerró (≥17:30), usar fecha de hoy
    market_closed_today = (
        now.weekday() < 5  # lunes=0 … viernes=4
        and (now.hour > 17 or (now.hour == 17 and now.minute >= 30))
    )
    if market_closed_today:
        return today_str

    # Fuera de ese caso: usar el último dato de yfinance (cubre festivos y fines de semana)
    try:
        data = yf.download("^IBEX", period="5d", progress=False, auto_adjust=True)
        if data.empty:
            return None
        return data.index[-1].strftime("%Y-%m-%d")
    except Exception:
        return None


def is_market_open_today() -> bool:
    """True solo si el último dato de ^IBEX corresponde a hoy."""
    madrid = pytz.timezone("Europe/Madrid")
    today = datetime.now(madrid).strftime("%Y-%m-%d")
    return get_last_market_date() == today


def clear_today_data(date_str: str, config: dict, logger: logging.Logger):
    """Elimina todos los ficheros del día para forzar una ejecución limpia."""
    patterns = [
        os.path.join(config.get("data_raw_dir", "data/raw"),      f"*{date_str}*"),
        os.path.join(config.get("data_analysis_dir", "data/analysis"), f"*{date_str}*"),
        os.path.join(config.get("output_dir", "output"),           f"*{date_str}*"),
    ]
    deleted = []
    for pattern in patterns:
        import glob as _glob
        for f in _glob.glob(pattern):
            try:
                os.remove(f)
                deleted.append(f)
            except Exception as e:
                logger.warning(f"No se pudo eliminar {f}: {e}")
    if deleted:
        logger.info(f"[CLEAN] {len(deleted)} fichero(s) del día {date_str} eliminados antes de ejecutar.")
    else:
        logger.info(f"[CLEAN] No había ficheros del día {date_str} que eliminar.")


def main():
    ensure_directories()
    logger = setup_logging()
    logger.info("=== Inicio del pipeline IBEX 35 ===")

    last_market_date = get_last_market_date()
    if not last_market_date:
        logger.info("No se pudo obtener datos del mercado. Sin informe.")
        sys.exit(0)

    force = os.getenv("FORCE_RUN", "").lower() == "true"
    today = datetime.now(pytz.timezone("Europe/Madrid")).strftime("%Y-%m-%d")
    if not force and last_market_date != today:
        logger.info(f"Mercado cerrado hoy ({today}). Último dato: {last_market_date}. Sin informe.")
        sys.exit(0)

    if not check_market_hours():
        logger.info("Fuera del horario de ejecución (17:35–19:00 Madrid). Sin informe.")
        sys.exit(0)

    # Guardia anti-doble-run: si el informe de hoy ya existe, no volver a generarlo
    output_dir = os.getenv("OUTPUT_DIR", "output")
    pdf_hoy = os.path.join(output_dir, f"informe_{last_market_date}.pdf")
    if not force and os.path.exists(pdf_hoy):
        logger.info(f"Informe del día ya generado ({pdf_hoy}). Sin acción.")
        sys.exit(0)

    from agents.leader import LeaderAgent
    from agents.ibex_data import get_ibex35_components

    config = {
        "api_key": os.environ["ANTHROPIC_API_KEY"],
        "model_leader": os.getenv("MODEL_LEADER", "claude-haiku-4-5-20251001"),
        "model_analyst": os.getenv("MODEL_ANALYST", "claude-haiku-4-5-20251001"),
        "model_writer": os.getenv("MODEL_WRITER", "claude-haiku-4-5-20251001"),
        "max_retries": int(os.getenv("MAX_RETRIES", "3")),
        "retry_delay": int(os.getenv("RETRY_DELAY_SECONDS", "5")),
        "data_raw_dir": os.getenv("DATA_RAW_DIR", "data/raw"),
        "data_analysis_dir": os.getenv("DATA_ANALYSIS_DIR", "data/analysis"),
        "output_dir": os.getenv("OUTPUT_DIR", "output"),
        "logs_dir": os.getenv("LOGS_DIR", "logs"),
        "ibex_cache_days": int(os.getenv("IBEX_CACHE_DAYS", "7")),
        "rss_expansion": os.getenv("RSS_EXPANSION", "https://www.expansion.com/rss/mercados.xml"),
        "rss_cinco_dias": os.getenv("RSS_CINCO_DIAS", "https://cincodias.elpais.com/seccion/rss/mercados/"),
        "finnhub_api_key": (os.getenv("FINNHUB_API_KEY") or "").strip(),
    }

    # Verificación temprana: obtener composición del IBEX antes de arrancar el pipeline
    logger.info("Verificando composición actual del IBEX 35...")
    try:
        components = get_ibex35_components(
            cache_dir=os.path.dirname(config["data_raw_dir"]) or "data",
            max_cache_age_days=config["ibex_cache_days"],
        )
        logger.info(
            f"IBEX 35 listo: {len(components['tickers'])} componentes "
            f"(fuente: {components.get('source')}, fecha: {components.get('last_updated')})"
        )
    except Exception as e:
        logger.error(f"No se pudo obtener la composición del IBEX 35: {e}. Abortando.")
        sys.exit(1)

    if force:
        clear_today_data(last_market_date, config, logger)

    leader = LeaderAgent(config, logger)
    result = leader.run(date=last_market_date)

    if result["status"] == "success":
        logger.info(f"Pipeline completado. PDF: {result['pdf_path']}")
        sys.exit(0)
    else:
        logger.error(f"Pipeline fallido: {result['errors']}")
        sys.exit(1)


if __name__ == "__main__":
    main()
