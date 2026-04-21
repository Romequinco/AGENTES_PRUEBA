"""API mínima para la capa de newsletter.

Endpoints:
  POST /register          → crea usuario y lo suscribe al newsletter
  GET  /api/v1/newsletter/latest → devuelve el JSON del newsletter más reciente

Ejecutar:
  python api/flask_app.py
  (o con gunicorn: gunicorn api.flask_app:app)
"""

import glob
import json
import os
import sys

from flask import Flask, request, jsonify

# Aseguramos que el raíz del proyecto esté en sys.path cuando se ejecuta directamente
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

app = Flask(__name__)


def _get_db():
    """Importación diferida para que el error de DATABASE_URL sea claro en arranque."""
    from db.models import SessionLocal
    return SessionLocal()


# ---------------------------------------------------------------------------
# POST /register
# ---------------------------------------------------------------------------

@app.route("/register", methods=["POST"])
def register():
    """Crea un usuario y lo suscribe al newsletter.

    Body JSON esperado:
        { "email": "user@example.com", "password": "plaintext" }

    Nota: el hash de contraseña usa werkzeug (incluido con Flask).
    Auth JWT se añade en Fase 2.
    """
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
        from werkzeug.security import generate_password_hash

        db = _get_db()
        try:
            existing = db.query(User).filter(User.email == email).first()
            if existing:
                return jsonify({"error": "email ya registrado"}), 409

            user = User(
                email=email,
                password_hash=generate_password_hash(password),
                tier="free",
            )
            db.add(user)
            db.flush()  # obtener user.id sin commit

            subscriber = NewsletterSubscriber(user_id=user.id, active=True)
            db.add(subscriber)
            db.commit()
            db.refresh(user)

            return jsonify({
                "id": user.id,
                "email": user.email,
                "tier": user.tier,
                "subscribed": True,
            }), 201

        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    except EnvironmentError as e:
        return jsonify({"error": str(e)}), 503
    except Exception as e:
        app.logger.error(f"/register error: {e}", exc_info=True)
        return jsonify({"error": "error interno del servidor"}), 500


# ---------------------------------------------------------------------------
# GET /api/v1/newsletter/latest
# ---------------------------------------------------------------------------

@app.route("/api/v1/newsletter/latest", methods=["GET"])
def newsletter_latest():
    """Devuelve el JSON del newsletter más reciente en data/analysis/."""
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
        app.logger.error(f"Error leyendo {latest}: {e}")
        return jsonify({"error": "error leyendo el newsletter"}), 500


# ---------------------------------------------------------------------------
# Healthcheck
# ---------------------------------------------------------------------------

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Validación de DATABASE_URL en arranque para fallo rápido y visible
    if not os.environ.get("DATABASE_URL"):
        print(
            "ERROR: DATABASE_URL no está definido. "
            "Configura una URL de PostgreSQL antes de arrancar la API.",
            file=sys.stderr,
        )
        sys.exit(1)

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG", "").lower() == "true")
