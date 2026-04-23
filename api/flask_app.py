"""Factory de la aplicación Flask.

Registra todos los blueprints y configura JWT. Punto de entrada para arrancar la API.

Blueprints registrados:
  api/auth.py        → /auth/register, /auth/login
  api/newsletter.py  → /register (legacy), /api/v1/newsletter/latest, /health, /dashboard.html
  api/premium.py     → /api/v1/alerts, /api/v1/technical/<symbol>       (tier premium/pro)
  api/pro.py         → /api/v1/strategies, /api/v1/backtest,             (tier pro)
                       /api/v1/portfolios, /api/v1/reports/weekly
  api/stripe.py      → /stripe/create-checkout, /stripe/webhook

Para arrancar:
    python api/flask_app.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from flask import Flask
from flask_jwt_extended import JWTManager

from api.auth import auth_bp
from api.newsletter import newsletter_bp
from api.premium import premium_bp
from api.pro import pro_bp
from api.stripe import stripe_bp
from api.admin import admin_bp


def create_app() -> Flask:
    """Crea y configura la aplicación Flask con todos sus blueprints."""
    app = Flask(__name__, static_folder=None)

    app.config["JWT_SECRET_KEY"] = os.environ.get("JWT_SECRET_KEY", "dev-insecure-change-me")
    app.config["JWT_ACCESS_TOKEN_EXPIRES"] = False

    JWTManager(app)

    app.register_blueprint(auth_bp)
    app.register_blueprint(newsletter_bp)
    app.register_blueprint(premium_bp)
    app.register_blueprint(pro_bp)
    app.register_blueprint(stripe_bp)
    app.register_blueprint(admin_bp)

    return app


app = create_app()


if __name__ == "__main__":
    if not os.environ.get("DATABASE_URL"):
        print(
            "ERROR: DATABASE_URL no está definido. "
            "Configura una URL de PostgreSQL antes de arrancar la API.",
            file=sys.stderr,
        )
        sys.exit(1)

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG", "").lower() == "true")
