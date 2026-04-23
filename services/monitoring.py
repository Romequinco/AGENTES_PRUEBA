"""Módulo de error reporting para producción.

Expone:
  send_error_alert(error_message, context) → envía email a ADMIN_EMAIL via SendGrid
  @monitor_errors → decorator que captura excepciones, alerta y re-lanza

Rate limiting: máximo 1 email por el mismo error en menos de 1 hora (en memoria).
"""

from __future__ import annotations

import functools
import logging
import os
import time
from typing import Any

logger = logging.getLogger("bolsa.monitoring")

# Caché en memoria: error_key → timestamp del último email enviado
_last_sent: dict[str, float] = {}
_RATE_LIMIT_SECONDS = 3600  # 1 hora


def send_error_alert(error_message: str, context: str = "") -> bool:
    """Envía email de error crítico a ADMIN_EMAIL usando SendGrid.

    Aplica rate limiting: omite el envío si el mismo error fue enviado en la última hora.
    Returns True si el email fue enviado, False si fue suprimido por rate limiting o fallo.
    """
    admin_email = os.environ.get("ADMIN_EMAIL")
    sendgrid_key = os.environ.get("SENDGRID_API_KEY")
    from_email = os.environ.get("SENDGRID_FROM_EMAIL")

    if not admin_email or not sendgrid_key or not from_email:
        logger.warning(
            "[MONITORING] send_error_alert omitido: faltan ADMIN_EMAIL, "
            "SENDGRID_API_KEY o SENDGRID_FROM_EMAIL"
        )
        return False

    error_key = f"{error_message[:120]}:{context[:60]}"
    now = time.monotonic()
    last = _last_sent.get(error_key, 0)
    if now - last < _RATE_LIMIT_SECONDS:
        logger.debug(f"[MONITORING] Rate limit activo para: {error_key[:80]}")
        return False

    subject = f"[IBEX35] Error crítico: {error_message[:80]}"
    body_html = f"""<!DOCTYPE html>
<html>
<body style="font-family:sans-serif;max-width:600px;margin:0 auto;padding:20px">
  <h2 style="color:#c0392b">&#9888; Error crítico — IBEX 35 Sistema</h2>
  <p><strong>Error:</strong> {_html_escape(error_message)}</p>
  {"<p><strong>Contexto:</strong> " + _html_escape(context) + "</p>" if context else ""}
  <p style="color:#666;font-size:11px;margin-top:24px">
    Generado automáticamente por el sistema IBEX 35.
  </p>
</body>
</html>"""

    try:
        import sendgrid as sg_module
        from sendgrid.helpers.mail import Mail

        client = sg_module.SendGridAPIClient(api_key=sendgrid_key)
        message = Mail(
            from_email=from_email,
            to_emails=admin_email,
            subject=subject,
            html_content=body_html,
        )
        response = client.send(message)
        if response.status_code in (200, 202):
            _last_sent[error_key] = now
            logger.info(f"[MONITORING] Alert enviada a {admin_email}: {error_message[:80]}")
            return True
        else:
            logger.error(f"[MONITORING] SendGrid devolvió {response.status_code}")
            return False
    except Exception as e:
        logger.error(f"[MONITORING] Error enviando alert: {e}")
        return False


def monitor_errors(func):
    """Decorator que captura excepciones no manejadas, envía alerta y re-lanza.

    Compatible con funciones normales y con jobs de APScheduler.
    No silencia ninguna excepción.
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            context = f"función={func.__qualname__}"
            send_error_alert(str(exc), context)
            raise
    return wrapper


def _html_escape(text: str) -> str:
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
