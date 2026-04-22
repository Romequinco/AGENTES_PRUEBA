"""Blueprint de autenticación.

Endpoints (prefijo /auth):
  POST /auth/register   → crea usuario con bcrypt, devuelve JWT
  POST /auth/login      → valida credenciales, devuelve JWT
"""

import bcrypt
from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import create_access_token

from api.helpers import get_db

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


@auth_bp.route("/register", methods=["POST"])
def register():
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
        current_app.logger.error(f"/auth/register error: {e}", exc_info=True)
        return jsonify({"error": "error interno del servidor"}), 500


@auth_bp.route("/login", methods=["POST"])
def login():
    """Valida credenciales y devuelve JWT."""
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not email or not password:
        return jsonify({"error": "email y password son obligatorios"}), 400

    try:
        from db.models import User
        db = get_db()
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
        current_app.logger.error(f"/auth/login error: {e}", exc_info=True)
        return jsonify({"error": "error interno del servidor"}), 500
