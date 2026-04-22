"""Motor de alertas técnicas para usuarios premium.

Ejecutar como worker independiente:
    python services/alerts_engine.py

Usa APScheduler con SQLAlchemy job store apuntando a DATABASE_URL.
Evalúa todas las alertas activas cada día a las 17:35 Madrid
(configurable via ALERTS_TIMEZONE y ALERTS_HOUR/ALERTS_MINUTE).
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone

# Asegurar que el raíz del proyecto esté en sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger("bolsa.alerts_engine")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)


def _check_env() -> None:
    missing = []
    if not os.environ.get("DATABASE_URL"):
        missing.append("DATABASE_URL")
    if not os.environ.get("SENDGRID_API_KEY"):
        missing.append("SENDGRID_API_KEY")
    if missing:
        raise EnvironmentError(
            f"Variables de entorno requeridas no definidas: {', '.join(missing)}. "
            "Configúralas antes de arrancar el motor de alertas."
        )


def _evaluate_alerts() -> None:
    """Evalúa todas las alertas activas y notifica si se cumplen las condiciones."""
    from db.models import SessionLocal, Alert, User
    from services.technical_analyzer import analyze
    from services.email_sender import send_bulk_newsletter
    from services.email_formatter import format_newsletter_html

    logger.info("[ALERTS] Iniciando evaluación de alertas...")
    db = SessionLocal()
    try:
        alerts = (
            db.query(Alert)
            .filter(Alert.active == True)  # noqa: E712
            .all()
        )
        logger.info(f"[ALERTS] {len(alerts)} alerta(s) activa(s) a evaluar.")

        symbols_cache: dict[str, dict] = {}

        for alert in alerts:
            symbol = alert.symbol
            if symbol not in symbols_cache:
                try:
                    symbols_cache[symbol] = analyze(symbol)
                except Exception as e:
                    logger.warning(f"[ALERTS] No se pudo analizar {symbol}: {e}")
                    continue

            data = symbols_cache[symbol]
            condition_met = False

            ct = alert.condition_type
            cv = alert.condition_value

            if ct == "price_above" and data["current_price"] is not None:
                condition_met = data["current_price"] > cv
            elif ct == "price_below" and data["current_price"] is not None:
                condition_met = data["current_price"] < cv
            elif ct == "rsi_above" and data["rsi14"] is not None:
                condition_met = data["rsi14"] > cv
            elif ct == "rsi_below" and data["rsi14"] is not None:
                condition_met = data["rsi14"] < cv

            if not condition_met:
                continue

            user = db.query(User).filter(User.id == alert.user_id).first()
            if not user:
                continue

            logger.info(
                f"[ALERTS] Condición cumplida para alerta {alert.id} "
                f"({symbol} {ct} {cv}) — usuario {user.email}"
            )

            # Enviar email de alerta usando email_sender
            subject_map = {
                "price_above": f"{symbol} supera {cv}",
                "price_below": f"{symbol} cae bajo {cv}",
                "rsi_above": f"RSI de {symbol} supera {cv}",
                "rsi_below": f"RSI de {symbol} cae bajo {cv}",
            }
            subject = subject_map.get(ct, f"Alerta {symbol}")
            html = _build_alert_html(alert, data, subject)

            try:
                from services.email_sender import send_bulk_newsletter
                result = send_bulk_newsletter(
                    recipients=[user.email],
                    html_content=html,
                    fecha=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                )
                if result["success"]:
                    logger.info(f"[ALERTS] Email enviado a {user.email} para alerta {alert.id}")
                else:
                    logger.error(f"[ALERTS] Error enviando email: {result['error']}")
            except Exception as e:
                logger.error(f"[ALERTS] Error enviando email para alerta {alert.id}: {e}")

            # Marcar alerta como disparada
            alert.active = False
            alert.triggered_at = datetime.now(timezone.utc)

        db.commit()
        logger.info("[ALERTS] Evaluación completada.")

    except Exception as e:
        logger.error(f"[ALERTS] Error durante evaluación: {e}", exc_info=True)
        db.rollback()
    finally:
        db.close()


def _build_alert_html(alert, data: dict, subject: str) -> str:
    price = data.get("current_price")
    rsi = data.get("rsi14")
    return f"""<!DOCTYPE html>
<html>
<body style="font-family:sans-serif;max-width:600px;margin:0 auto;padding:20px">
  <h2 style="color:#1a1a2e">🔔 Alerta IBEX 35: {subject}</h2>
  <p>Tu alerta para <strong>{alert.symbol}</strong> se ha activado.</p>
  <table style="width:100%;border-collapse:collapse">
    <tr><td style="padding:8px;border:1px solid #ddd"><strong>Símbolo</strong></td>
        <td style="padding:8px;border:1px solid #ddd">{alert.symbol}</td></tr>
    <tr><td style="padding:8px;border:1px solid #ddd"><strong>Condición</strong></td>
        <td style="padding:8px;border:1px solid #ddd">{alert.condition_type} {alert.condition_value}</td></tr>
    <tr><td style="padding:8px;border:1px solid #ddd"><strong>Precio actual</strong></td>
        <td style="padding:8px;border:1px solid #ddd">{price:.4f if price else 'N/A'}</td></tr>
    <tr><td style="padding:8px;border:1px solid #ddd"><strong>RSI(14)</strong></td>
        <td style="padding:8px;border:1px solid #ddd">{f'{rsi:.2f}' if rsi else 'N/A'}</td></tr>
  </table>
  <p style="color:#666;font-size:12px;margin-top:20px">IBEX 35 Análisis — alerta automática</p>
</body>
</html>"""


def create_scheduler():
    """Crea y configura el APScheduler. No lo inicia (para testing)."""
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

    db_url = os.environ.get("DATABASE_URL", "")
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)

    tz_name = os.environ.get("ALERTS_TIMEZONE", "Europe/Madrid")
    hour = int(os.environ.get("ALERTS_HOUR", "17"))
    minute = int(os.environ.get("ALERTS_MINUTE", "35"))

    jobstores = {}
    if db_url:
        try:
            jobstores["default"] = SQLAlchemyJobStore(url=db_url)
        except Exception as e:
            logger.warning(f"[ALERTS] No se pudo inicializar SQLAlchemy job store: {e}. Usando memoria.")

    import pytz
    tz = pytz.timezone(tz_name)

    scheduler = BlockingScheduler(jobstores=jobstores if jobstores else None, timezone=tz)
    scheduler.add_job(
        _evaluate_alerts,
        trigger="cron",
        hour=hour,
        minute=minute,
        id="evaluate_alerts",
        replace_existing=True,
        name=f"Evaluar alertas a las {hour:02d}:{minute:02d} {tz_name}",
    )
    return scheduler


if __name__ == "__main__":
    _check_env()
    logger.info("[ALERTS] Iniciando motor de alertas...")
    scheduler = create_scheduler()
    logger.info(f"[ALERTS] Jobs programados: {[j.name for j in scheduler.get_jobs()]}")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("[ALERTS] Motor de alertas detenido.")
