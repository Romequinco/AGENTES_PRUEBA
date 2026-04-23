"""Blueprint de administración — métricas internas.

Endpoints:
  GET /admin/metrics  → KPIs del sistema (protegido por X-Admin-Key header)
"""

import os
from datetime import datetime, timezone, timedelta

from flask import Blueprint, request, jsonify, current_app

from api.helpers import get_db

admin_bp = Blueprint("admin", __name__)


def _require_admin_key():
    """Devuelve None si la key es válida, o una respuesta 401/503 si no."""
    admin_key = os.environ.get("ADMIN_API_KEY")
    if not admin_key:
        return jsonify({"error": "ADMIN_API_KEY no configurada en el servidor"}), 503
    provided = request.headers.get("X-Admin-Key", "")
    if provided != admin_key:
        return jsonify({"error": "Clave de administrador inválida o ausente"}), 401
    return None


@admin_bp.route("/admin/metrics", methods=["GET"])
def metrics():
    auth_error = _require_admin_key()
    if auth_error:
        return auth_error

    try:
        from db.models import (
            User, NewsletterSubscriber, Alert, BacktestResult, Portfolio,
        )
        db = get_db()
        try:
            total_users = db.query(User).count()
            free_users = db.query(User).filter(User.tier == "free").count()
            premium_users = db.query(User).filter(User.tier == "premium").count()
            pro_users = db.query(User).filter(User.tier == "pro").count()

            active_subscribers = (
                db.query(NewsletterSubscriber)
                .filter(NewsletterSubscriber.active == True)  # noqa: E712
                .count()
            )

            total_active_alerts = (
                db.query(Alert).filter(Alert.active == True).count()  # noqa: E712
            )
            week_ago = datetime.now(timezone.utc) - timedelta(days=7)
            triggered_last_7d = (
                db.query(Alert)
                .filter(Alert.triggered_at >= week_ago)
                .count()
            )

            month_ago = datetime.now(timezone.utc) - timedelta(days=30)
            backtests_last_30d = (
                db.query(BacktestResult)
                .filter(BacktestResult.ran_at >= month_ago)
                .count()
            )

            total_portfolios = db.query(Portfolio).count()

        finally:
            db.close()

        return jsonify({
            "users": {
                "total": total_users,
                "free": free_users,
                "premium": premium_users,
                "pro": pro_users,
            },
            "newsletter": {
                "active_subscribers": active_subscribers,
            },
            "alerts": {
                "total_active": total_active_alerts,
                "triggered_last_7d": triggered_last_7d,
            },
            "backtests": {
                "total_last_30d": backtests_last_30d,
            },
            "portfolios": {
                "total": total_portfolios,
            },
        }), 200

    except EnvironmentError as e:
        return jsonify({"error": str(e)}), 503
    except Exception as e:
        current_app.logger.error(f"/admin/metrics error: {e}", exc_info=True)
        return jsonify({"error": "error interno del servidor"}), 500
