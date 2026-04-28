"""Utilidades compartidas por todos los blueprints de la API.

No importar app aquí — usar current_app dentro de las funciones que lo necesiten.
"""

from flask import jsonify


def get_db():
    from db.models import get_db_session
    return get_db_session()


def require_premium(user_id: int):
    """Devuelve (user, None) si tier es premium o pro, o (None, response_tuple) si no."""
    from db.models import User
    db = get_db()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return None, (jsonify({"error": "usuario no encontrado"}), 404)
        if user.tier not in ("premium", "pro"):
            return None, (jsonify({"error": "se requiere tier premium o pro"}), 403)
        return user, None
    finally:
        db.close()


def require_pro(user_id: int):
    """Devuelve (user, None) si tier es pro, o (None, response_tuple) si no."""
    from db.models import User
    db = get_db()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return None, (jsonify({"error": "usuario no encontrado"}), 404)
        if user.tier != "pro":
            return None, (jsonify({"error": "se requiere tier pro"}), 403)
        return user, None
    finally:
        db.close()
