"""Blueprint de integración con Stripe.

Endpoints (prefijo /stripe):
  POST /stripe/create-checkout   → crea sesión de Stripe Checkout (premium o pro)
  POST /stripe/webhook           → recibe y procesa eventos de Stripe (firma obligatoria)

Decisión de diseño: el webhook es la única fuente de verdad para actualizar el tier
del usuario. El redirect de éxito del checkout no tiene lógica de negocio. Ver decisions.md #012.
"""

import json
import os

from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import jwt_required, get_jwt_identity

from api.helpers import get_db

stripe_bp = Blueprint("stripe", __name__, url_prefix="/stripe")


@stripe_bp.route("/create-checkout", methods=["POST"])
@jwt_required()
def create_checkout():
    user_id = int(get_jwt_identity())
    stripe_secret = os.environ.get("STRIPE_SECRET_KEY")
    if not stripe_secret:
        return jsonify({"error": "STRIPE_SECRET_KEY no configurado"}), 503

    data = request.get_json(silent=True) or {}
    requested_tier = data.get("tier", "premium")
    if requested_tier == "pro":
        price_id = os.environ.get("STRIPE_PRO_PRICE_ID")
        if not price_id:
            return jsonify({"error": "STRIPE_PRO_PRICE_ID no configurado"}), 503
    else:
        price_id = os.environ.get("STRIPE_PREMIUM_PRICE_ID")
        if not price_id:
            return jsonify({"error": "STRIPE_PREMIUM_PRICE_ID no configurado"}), 503

    success_url = os.environ.get(
        "STRIPE_SUCCESS_URL",
        "http://localhost:5000/dashboard.html?session_id={CHECKOUT_SESSION_ID}",
    )
    cancel_url = os.environ.get("STRIPE_CANCEL_URL", "http://localhost:5000/dashboard.html")

    try:
        import stripe
        stripe.api_key = stripe_secret

        db = get_db()
        try:
            from db.models import User
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
            metadata={"user_id": str(user_id), "tier": requested_tier},
            success_url=success_url,
            cancel_url=cancel_url,
        )
        return jsonify({"checkout_url": session.url, "session_id": session.id}), 200

    except Exception as e:
        current_app.logger.error(f"/stripe/create-checkout error: {e}", exc_info=True)
        return jsonify({"error": "error creando sesión de pago"}), 500


@stripe_bp.route("/webhook", methods=["POST"])
def webhook():
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

    db = SessionLocal()
    try:
        event_type = event["type"]
        obj = event["data"]["object"]
        # StripeObject no es un dict plano — convertir para usar .get() con seguridad
        obj_dict = json.loads(str(obj))

        if event_type == "checkout.session.completed":
            metadata = obj_dict.get("metadata") or {}
            user_id = int(metadata.get("user_id", 0))
            if not user_id:
                current_app.logger.warning("[STRIPE] checkout.session.completed sin user_id en metadata")
                return

            user = db.query(User).filter(User.id == user_id).first()
            if not user:
                current_app.logger.warning(f"[STRIPE] usuario {user_id} no encontrado")
                return

            # Tier viene en metadata; se verifica también contra el price_id real del line item
            target_tier = metadata.get("tier", "premium")
            pro_price_id = os.environ.get("STRIPE_PRO_PRICE_ID")
            if pro_price_id:
                for item in (obj_dict.get("line_items") or {}).get("data") or []:
                    if (item.get("price") or {}).get("id") == pro_price_id:
                        target_tier = "pro"
                        break
            if target_tier not in ("premium", "pro"):
                target_tier = "premium"

            sub_record = db.query(Subscription).filter(Subscription.user_id == user_id).first()
            if not sub_record:
                sub_record = Subscription(user_id=user_id)
                db.add(sub_record)

            sub_record.stripe_customer_id = obj_dict.get("customer")
            sub_record.stripe_subscription_id = obj_dict.get("subscription")
            sub_record.tier = target_tier
            sub_record.status = "active"
            user.tier = target_tier
            db.commit()
            current_app.logger.info(f"[STRIPE] checkout.session.completed — user {user_id} → {target_tier}")

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
                current_app.logger.info(f"[STRIPE] subscription.deleted — sub {stripe_sub_id} → free")

        elif event_type == "invoice.payment_failed":
            stripe_sub_id = obj_dict.get("subscription")
            sub_record = db.query(Subscription).filter(
                Subscription.stripe_subscription_id == stripe_sub_id
            ).first()
            if sub_record:
                sub_record.status = "past_due"
                db.commit()
                current_app.logger.info(f"[STRIPE] invoice.payment_failed — sub {stripe_sub_id} → past_due")

    except Exception as e:
        current_app.logger.error(
            f"[STRIPE] Error procesando evento {event.get('type', '?')}: {e}", exc_info=True
        )
        db.rollback()
    finally:
        db.close()
