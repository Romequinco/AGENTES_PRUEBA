"""Blueprint de newsletter y endpoints públicos.

Endpoints (sin prefijo):
  POST /register                    → crea usuario legacy (Fase 1, compatibilidad)
  GET  /api/v1/newsletter/latest    → último newsletter JSON
  GET  /health
  GET  /dashboard.html
"""

import glob
import json
import os
from datetime import datetime, timezone

import bcrypt
from flask import Blueprint, request, jsonify, send_from_directory, current_app

from api.helpers import get_db

newsletter_bp = Blueprint("newsletter", __name__)

# Directorio raíz del proyecto (un nivel arriba de api/)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@newsletter_bp.route("/register", methods=["POST"])
def register_legacy():
    """Crea usuario y lo suscribe al newsletter. Compatibilidad con Fase 1."""
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not email or not password:
        return jsonify({"error": "email y password son obligatorios"}), 400
    if "@" not in email:
        return jsonify({"error": "email inválido"}), 400
    if len(password) < 8:
        return jsonify({"error": "password debe tener al menos 8 caracteres"}), 400

    try:
        from db.models import User, NewsletterSubscriber
        db = get_db()
        try:
            if db.query(User).filter(User.email == email).first():
                return jsonify({"error": "email ya registrado"}), 409

            pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
            user = User(email=email, password_hash=pw_hash, tier="free")
            db.add(user)
            db.flush()
            db.add(NewsletterSubscriber(user_id=user.id, active=True))
            db.commit()
            db.refresh(user)
            return jsonify({"id": user.id, "email": user.email, "tier": user.tier, "subscribed": True}), 201
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    except EnvironmentError as e:
        return jsonify({"error": str(e)}), 503
    except Exception as e:
        current_app.logger.error(f"/register error: {e}", exc_info=True)
        return jsonify({"error": "error interno del servidor"}), 500


@newsletter_bp.route("/api/v1/newsletter/latest", methods=["GET"])
def newsletter_latest():
    analysis_dir = os.environ.get("DATA_ANALYSIS_DIR", "data/analysis")
    pattern = os.path.join(analysis_dir, "newsletter_*.json")
    files = sorted(glob.glob(pattern))

    if not files:
        return jsonify({"error": "No hay newsletters disponibles todavía"}), 404

    latest = files[-1]
    try:
        with open(latest, encoding="utf-8") as f:
            data = json.load(f)
        return jsonify(data), 200
    except Exception as e:
        current_app.logger.error(f"Error leyendo {latest}: {e}")
        return jsonify({"error": "error leyendo el newsletter"}), 500


@newsletter_bp.route("/health", methods=["GET"])
def health():
    result = {
        "status": "ok",
        "db": "connected",
        "sendgrid": "configured" if os.environ.get("SENDGRID_API_KEY") else "missing_key",
        "stripe": "configured" if os.environ.get("STRIPE_SECRET_KEY") else "missing_key",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    try:
        from api.helpers import get_db
        db = get_db()
        db.execute(__import__("sqlalchemy").text("SELECT 1"))
        db.close()
    except Exception as e:
        current_app.logger.warning(f"/health db check failed: {e}")
        result["db"] = "error"
        result["status"] = "degraded"
    return jsonify(result), 200


@newsletter_bp.route("/dashboard.html", methods=["GET"])
def dashboard():
    frontend_dir = os.path.join(_PROJECT_ROOT, "frontend")
    return send_from_directory(frontend_dir, "dashboard.html")
