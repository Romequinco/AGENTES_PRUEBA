"""Blueprint de endpoints PREMIUM (tier premium o pro).

Endpoints (sin prefijo, todos requieren JWT):
  GET    /api/v1/alerts              → lista alertas activas del usuario
  POST   /api/v1/alerts              → crea alerta nueva
  DELETE /api/v1/alerts/<id>         → elimina alerta
  GET    /api/v1/technical/<symbol>  → análisis técnico en tiempo real
"""

from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import jwt_required, get_jwt_identity

from api.helpers import get_db, require_premium

premium_bp = Blueprint("premium", __name__)


@premium_bp.route("/api/v1/alerts", methods=["GET"])
@jwt_required()
def list_alerts():
    user_id = int(get_jwt_identity())
    user, err = require_premium(user_id)
    if err:
        return err

    from db.models import Alert
    db = get_db()
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


@premium_bp.route("/api/v1/alerts", methods=["POST"])
@jwt_required()
def create_alert():
    user_id = int(get_jwt_identity())
    user, err = require_premium(user_id)
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
    db = get_db()
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


@premium_bp.route("/api/v1/alerts/<int:alert_id>", methods=["DELETE"])
@jwt_required()
def delete_alert(alert_id: int):
    user_id = int(get_jwt_identity())
    user, err = require_premium(user_id)
    if err:
        return err

    from db.models import Alert
    db = get_db()
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


@premium_bp.route("/api/v1/technical/<symbol>", methods=["GET"])
@jwt_required()
def technical_analysis(symbol: str):
    user_id = int(get_jwt_identity())
    user, err = require_premium(user_id)
    if err:
        return err

    try:
        from services.technical_analyzer import analyze
        data = analyze(symbol.upper())
        return jsonify(data), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        current_app.logger.error(f"/technical/{symbol} error: {e}", exc_info=True)
        return jsonify({"error": "error calculando indicadores"}), 500
