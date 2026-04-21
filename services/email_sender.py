"""Envío masivo del newsletter usando SendGrid Personalizations API.

Usa un único request HTTP por envío (batch), no un loop por destinatario.
Docs: https://docs.sendgrid.com/api-reference/mail-send/mail-send
"""

from __future__ import annotations

import os
import logging
from typing import Sequence

import requests

logger = logging.getLogger("bolsa.email_sender")

SENDGRID_API_URL = "https://api.sendgrid.com/v3/mail/send"
_MAX_PERSONALIZATIONS = 1000  # límite hard de SendGrid por request


def send_bulk_newsletter(
    recipients: Sequence[str],
    html_content: str,
    fecha: str,
    from_email: str | None = None,
    from_name: str = "IBEX 35 Análisis",
) -> dict:
    """Envía el newsletter a todos los destinatarios en un único request.

    Args:
        recipients: Lista de emails activos obtenidos de la DB.
        html_content: HTML completo generado por email_formatter.
        fecha: Fecha del informe (YYYY-MM-DD), usada en el subject.
        from_email: Remitente. Si None, usa SENDGRID_FROM_EMAIL del entorno.
        from_name: Nombre visible del remitente.

    Returns:
        dict con keys 'success' (bool), 'sent' (int), 'status_code' (int), 'error' (str|None).
    """
    api_key = os.environ.get("SENDGRID_API_KEY", "").strip()
    if not api_key:
        raise EnvironmentError("SENDGRID_API_KEY no está definido en el entorno.")

    sender = from_email or os.environ.get("SENDGRID_FROM_EMAIL", "").strip()
    if not sender:
        raise EnvironmentError("SENDGRID_FROM_EMAIL no está definido en el entorno.")

    if not recipients:
        logger.info("[EMAIL] No hay suscriptores activos. Sin envío.")
        return {"success": True, "sent": 0, "status_code": None, "error": None}

    total_sent = 0
    errors = []

    # SendGrid limita a 1000 personalizations por request — enviamos en batches
    for batch_start in range(0, len(recipients), _MAX_PERSONALIZATIONS):
        batch = recipients[batch_start : batch_start + _MAX_PERSONALIZATIONS]
        personalizations = [{"to": [{"email": addr}]} for addr in batch]

        payload = {
            "personalizations": personalizations,
            "from": {"email": sender, "name": from_name},
            "subject": f"IBEX 35 · Cierre {fecha}",
            "content": [{"type": "text/html", "value": html_content}],
        }

        resp = requests.post(
            SENDGRID_API_URL,
            json=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=30,
        )

        if resp.status_code == 202:
            total_sent += len(batch)
            logger.info(f"[EMAIL] Batch enviado: {len(batch)} destinatarios (total acumulado: {total_sent})")
        else:
            error_msg = f"SendGrid error {resp.status_code}: {resp.text[:300]}"
            logger.error(f"[EMAIL] {error_msg}")
            errors.append(error_msg)

    success = len(errors) == 0
    return {
        "success": success,
        "sent": total_sent,
        "status_code": 202 if success else None,
        "error": "; ".join(errors) if errors else None,
    }
