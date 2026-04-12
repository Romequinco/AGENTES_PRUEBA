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
    return now.hour == 17 and now.minute >= 35 or now.hour == 18


def is_market_open_today() -> bool:
    try:
        data = yf.download("^IBEX", period="1d", progress=False, auto_adjust=True)
        return not data.empty
    except Exception:
        return False


def main():
    ensure_directories()
    logger = setup_logging()
    logger.info("=== Inicio del pipeline IBEX 35 ===")

    if not is_market_open_today():
        logger.info("Mercado cerrado hoy (festivo o fin de semana). Sin informe.")
        sys.exit(0)

    if not check_market_hours():
        logger.info("Fuera del horario de ejecución (17:35–19:00 Madrid). Sin informe.")
        sys.exit(0)

    from agents.leader import LeaderAgent

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
        "rss_expansion": os.getenv("RSS_EXPANSION", "https://www.expansion.com/rss/mercados.xml"),
        "rss_cinco_dias": os.getenv("RSS_CINCO_DIAS", "https://cincodias.elpais.com/seccion/rss/mercados/"),
    }

    leader = LeaderAgent(config, logger)
    result = leader.run()

    if result["status"] == "success":
        logger.info(f"Pipeline completado. PDF: {result['pdf_path']}")
        sys.exit(0)
    else:
        logger.error(f"Pipeline fallido: {result['errors']}")
        sys.exit(1)


if __name__ == "__main__":
    main()
