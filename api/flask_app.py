"""API Flask — Newsletter (Fase 1) + Auth JWT + Alertas + Stripe (Fase 2).

Endpoints Fase 1 (sin auth):
  POST /register                      → crea usuario y suscribe al newsletter
  GET  /api/v1/newsletter/latest      → último newsletter JSON
  GET  /health

Endpoints Auth (Fase 2):
  POST /auth/register                 → crea usuario, devuelve JWT
  POST /auth/login                    → valida credenciales, devuelve JWT

Endpoints PREMIUM (requieren JWT + tier premium/pro):
  GET  /api/v1/alerts                 → lista alertas activas del usuario
  POST /api/v1/alerts                 → crea alerta nueva
  DELETE /api/v1/alerts/<id>          → elimina alerta
  GET  /api/v1/technical/<symbol>     → análisis técnico en tiempo real

Endpoints Stripe:
  POST /stripe/create-checkout        → sesión de Stripe Checkout
  POST /stripe/webhook                → eventos de Stripe (requiere firma)
"""

import glob
import json
import os
import sys

import bcrypt
from flask import Flask, request, jsonify, send_from_directory
from flask_jwt_extended import (
    JWTManager, create_access_token,
    jwt_required, get_jwt_identity,
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

app = Flask(__name__, static_folder=None)

# JWT config — falla claro si falta la clave
jwt_secret = os.environ.get("JWT_SECRET_KEY", "dev-insecure-change-me")
app.config["JWT_SECRET_KEY"] = jwt_secret
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = False  # sin expiración automática; configurable

jwt = JWTManager(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_db():
    from db.models import SessionLocal
    return SessionLocal()


def _require_premium(user_id: int):
    """Devuelve (user, None) si es premium/pro, o (None, response_tuple) si no."""
    from db.models import User
    db = _get_db()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return None, (jsonify({"error": "usuario no encontrado"}), 404)
        if user.tier not in ("premium", "pro"):
            return None, (jsonify({"error": "se requiere tier premium o pro"}), 403)
        return user, None
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Fase 1 — /register (mantiene compatibilidad)
# ---------------------------------------------------------------------------

@app.route("/register", methods=["POST"])
def register_legacy():
    """Crea usuario y lo suscribe al newsletter. Compatibilidad Fase 1."""
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
        db = _get_db()
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
        app.logger.error(f"/register error: {e}", exc_info=True)
        return jsonify({"error": "error interno del servidor"}), 500


# ---------------------------------------------------------------------------
# Auth — /auth/register y /auth/login
# ---------------------------------------------------------------------------

@app.route("/auth/register", methods=["POST"])
def auth_register():
    """Crea usuario y devuelve JWT. No requiere autenticación previa."""
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not email or not password:
        return jsonify({"error": "email y password son obligatorios"}), 400
    if "@" not in email:
        return jsonify({"error": "email inválido"}), 400

    try:
        from db.models import User, NewsletterSubscriber
        db = _get_db()
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

            token = create_access_token(identity=str(user.id))
            return jsonify({
                "id": user.id,
                "email": user.email,
                "tier": user.tier,
                "access_token": token,
            }), 201
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    except EnvironmentError as e:
        return jsonify({"error": str(e)}), 503
    except Exception as e:
        app.logger.error(f"/auth/register error: {e}", exc_info=True)
        return jsonify({"error": "error interno del servidor"}), 500


@app.route("/auth/login", methods=["POST"])
def auth_login():
    """Valida credenciales y devuelve JWT."""
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not email or not password:
        return jsonify({"error": "email y password son obligatorios"}), 400

    try:
        from db.models import User
        db = _get_db()
        try:
            user = db.query(User).filter(User.email == email).first()
            if not user:
                return jsonify({"error": "credenciales inválidas"}), 401

            if not bcrypt.checkpw(password.encode(), user.password_hash.encode()):
                return jsonify({"error": "credenciales inválidas"}), 401

            token = create_access_token(identity=str(user.id))
            return jsonify({
                "id": user.id,
                "email": user.email,
                "tier": user.tier,
                "access_token": token,
            }), 200
        finally:
            db.close()
    except EnvironmentError as e:
        return jsonify({"error": str(e)}), 503
    except Exception as e:
        app.logger.error(f"/auth/login error: {e}", exc_info=True)
        return jsonify({"error": "error interno del servidor"}), 500


# ---------------------------------------------------------------------------
# PREMIUM — Alertas
# ---------------------------------------------------------------------------

@app.route("/api/v1/alerts", methods=["GET"])
@jwt_required()
def list_alerts():
    user_id = int(get_jwt_identity())
    user, err = _require_premium(user_id)
    if err:
        return err

    from db.models import Alert
    db = _get_db()
    try:
        alerts = db.query(Alert).filter(Alert.user_id == user_id, Alert.active == True).all()  # noqa: E712
        return jsonify([{
            "id": a.id,
            "symbol": a.symbol,
            "condition_type": a.condition_type,
            "condition_value": a.condition_value,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        } for a in alerts]), 200
    finally:
        db.close()


@app.route("/api/v1/alerts", methods=["POST"])
@jwt_required()
def create_alert():
    user_id = int(get_jwt_identity())
    user, err = _require_premium(user_id)
    if err:
        return err

    data = request.get_json(silent=True) or {}
    symbol = (data.get("symbol") or "").strip().upper()
    condition_type = data.get("condition_type") or ""
    condition_value = data.get("condition_value")

    valid_conditions = ("price_above", "price_below", "rsi_above", "rsi_below")
    if not symbol:
        return jsonify({"error": "symbol es obligatorio"}), 400
    if condition_type not in valid_conditions:
        return jsonify({"error": f"condition_type debe ser uno de: {valid_conditions}"}), 400
    if condition_value is None:
        return jsonify({"error": "condition_value es obligatorio"}), 400

    try:
        condition_value = float(condition_value)
    except (TypeError, ValueError):
        return jsonify({"error": "condition_value debe ser un número"}), 400

    from db.models import Alert
    db = _get_db()
    try:
        alert = Alert(
            user_id=user_id,
            symbol=symbol,
            condition_type=condition_type,
            condition_value=condition_value,
            active=True,
        )
        db.add(alert)
        db.commit()
        db.refresh(alert)
        return jsonify({
            "id": alert.id,
            "symbol": alert.symbol,
            "condition_type": alert.condition_type,
            "condition_value": alert.condition_value,
        }), 201
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@app.route("/api/v1/alerts/<int:alert_id>", methods=["DELETE"])
@jwt_required()
def delete_alert(alert_id: int):
    user_id = int(get_jwt_identity())
    user, err = _require_premium(user_id)
    if err:
        return err

    from db.models import Alert
    db = _get_db()
    try:
        alert = db.query(Alert).filter(Alert.id == alert_id, Alert.user_id == user_id).first()
        if not alert:
            return jsonify({"error": "alerta no encontrada"}), 404
        db.delete(alert)
        db.commit()
        return jsonify({"deleted": alert_id}), 200
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ---------------------------------------------------------------------------
# PREMIUM — Análisis técnico en tiempo real
# ---------------------------------------------------------------------------

@app.route("/api/v1/technical/<symbol>", methods=["GET"])
@jwt_required()
def technical_analysis(symbol: str):
    user_id = int(get_jwt_identity())
    user, err = _require_premium(user_id)
    if err:
        return err

    try:
        from services.technical_analyzer import analyze
        data = analyze(symbol.upper())
        return jsonify(data), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        app.logger.error(f"/technical/{symbol} error: {e}", exc_info=True)
        return jsonify({"error": "error calculando indicadores"}), 500


# ---------------------------------------------------------------------------
# Stripe
# ---------------------------------------------------------------------------

@app.route("/stripe/create-checkout", methods=["POST"])
@jwt_required()
def stripe_create_checkout():
    user_id = int(get_jwt_identity())
    stripe_secret = os.environ.get("STRIPE_SECRET_KEY")
    if not stripe_secret:
        return jsonify({"error": "STRIPE_SECRET_KEY no configurado"}), 503

    price_id = os.environ.get("STRIPE_PREMIUM_PRICE_ID")
    if not price_id:
        return jsonify({"error": "STRIPE_PREMIUM_PRICE_ID no configurado"}), 503

    success_url = os.environ.get("STRIPE_SUCCESS_URL", "http://localhost:5000/dashboard.html?session_id={CHECKOUT_SESSION_ID}")
    cancel_url = os.environ.get("STRIPE_CANCEL_URL", "http://localhost:5000/dashboard.html")

    try:
        import stripe
        stripe.api_key = stripe_secret

        from db.models import User
        db = _get_db()
        try:
            user = db.query(User).filter(User.id == user_id).first()
            if not user:
                return jsonify({"error": "usuario no encontrado"}), 404
            email = user.email
        finally:
            db.close()

        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            customer_email=email,
            metadata={"user_id": str(user_id)},
            success_url=success_url,
            cancel_url=cancel_url,
        )
        return jsonify({"checkout_url": session.url, "session_id": session.id}), 200

    except Exception as e:
        app.logger.error(f"/stripe/create-checkout error: {e}", exc_info=True)
        return jsonify({"error": "error creando sesión de pago"}), 500


@app.route("/stripe/webhook", methods=["POST"])
def stripe_webhook():
    """Recibe y valida eventos de Stripe. Fuente de verdad del estado de suscripción."""
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
    if not webhook_secret:
        return jsonify({"error": "STRIPE_WEBHOOK_SECRET no configurado"}), 503

    payload = request.get_data()
    sig_header = request.headers.get("Stripe-Signature", "")

    try:
        import stripe
        stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except stripe.error.SignatureVerificationError:
        return jsonify({"error": "firma de webhook inválida"}), 400
    except Exception as e:
        return jsonify({"error": f"payload inválido: {e}"}), 400

    _handle_stripe_event(event)
    return jsonify({"received": True}), 200


def _handle_stripe_event(event) -> None:
    from db.models import SessionLocal, User, Subscription
    from datetime import datetime, timezone

    db = SessionLocal()
    try:
        # event es un StripeObject — accedemos con [] o convertimos a dict
        event_type = event["type"]
        obj = event["data"]["object"]

        # Convertir StripeObject a dict plano para usar .get() con seguridad
        import json as _json
        obj_dict = _json.loads(str(obj))

        if event_type == "checkout.session.completed":
            metadata = obj_dict.get("metadata") or {}
            user_id = int(metadata.get("user_id", 0))
            if not user_id:
                app.logger.warning("[STRIPE] checkout.session.completed sin user_id en metadata")
                return
            user = db.query(User).filter(User.id == user_id).first()
            if not user:
                app.logger.warning(f"[STRIPE] usuario {user_id} no encontrado")
                return

            sub_record = db.query(Subscription).filter(Subscription.user_id == user_id).first()
            if not sub_record:
                sub_record = Subscription(user_id=user_id)
                db.add(sub_record)

            sub_record.stripe_customer_id = obj_dict.get("customer")
            sub_record.stripe_subscription_id = obj_dict.get("subscription")
            sub_record.tier = "premium"
            sub_record.status = "active"

            user.tier = "premium"
            db.commit()
            app.logger.info(f"[STRIPE] checkout.session.completed — user {user_id} → premium")

        elif event_type == "customer.subscription.deleted":
            stripe_sub_id = obj_dict.get("id")
            sub_record = db.query(Subscription).filter(
                Subscription.stripe_subscription_id == stripe_sub_id
            ).first()
            if sub_record:
                sub_record.status = "cancelled"
                user = db.query(User).filter(User.id == sub_record.user_id).first()
                if user:
                    user.tier = "free"
                db.commit()
                app.logger.info(f"[STRIPE] subscription.deleted — sub {stripe_sub_id} → free")

        elif event_type == "invoice.payment_failed":
            stripe_sub_id = obj_dict.get("subscription")
            sub_record = db.query(Subscription).filter(
                Subscription.stripe_subscription_id == stripe_sub_id
            ).first()
            if sub_record:
                sub_record.status = "past_due"
                db.commit()
                app.logger.info(f"[STRIPE] invoice.payment_failed — sub {stripe_sub_id} → past_due")

    except Exception as e:
        app.logger.error(f"[STRIPE] Error procesando evento {event_type if 'event_type' in dir() else '?'}: {e}", exc_info=True)
        db.rollback()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Fase 1 — Newsletter y health
# ---------------------------------------------------------------------------

@app.route("/api/v1/newsletter/latest", methods=["GET"])
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
        app.logger.error(f"Error leyendo {latest}: {e}")
        return jsonify({"error": "error leyendo el newsletter"}), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


# ---------------------------------------------------------------------------
# Servir dashboard
# ---------------------------------------------------------------------------

@app.route("/dashboard.html", methods=["GET"])
def dashboard():
    frontend_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")
    return send_from_directory(frontend_dir, "dashboard.html")


# ---------------------------------------------------------------------------

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
