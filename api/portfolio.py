"""Blueprint de portfolio global (tier gratuito).

Endpoints (todos requieren JWT, sin restricción de tier):
  POST   /api/v1/portfolio/positions          → añadir posición
  GET    /api/v1/portfolio/positions          → listar posiciones del usuario
  PUT    /api/v1/portfolio/positions/<id>     → editar posición
  DELETE /api/v1/portfolio/positions/<id>     → eliminar posición
  GET    /api/v1/portfolio/summary            → resumen P&L + benchmark
"""

import datetime

from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import jwt_required, get_jwt_identity

from api.helpers import get_db

portfolio_bp = Blueprint("portfolio", __name__)

_VALID_ASSET_TYPES = {"stock", "etf", "crypto", "commodity"}
_VALID_BENCHMARKS = {"^GSPC", "^IBEX", "^IXIC"}


# ---------------------------------------------------------------------------
# POST /api/v1/portfolio/positions — añadir posición
# ---------------------------------------------------------------------------

@portfolio_bp.route("/api/v1/portfolio/positions", methods=["POST"])
@jwt_required()
def create_position():
    user_id = int(get_jwt_identity())
    data = request.get_json(silent=True) or {}

    symbol = (data.get("symbol") or "").strip().upper()
    asset_type = (data.get("asset_type") or "").strip().lower()
    quantity = data.get("quantity")
    entry_price = data.get("entry_price")
    entry_date_str = data.get("entry_date")
    exchange = (data.get("exchange") or "").strip() or None

    # Validaciones
    if not symbol:
        return jsonify({"error": "symbol es obligatorio"}), 400
    if asset_type not in _VALID_ASSET_TYPES:
        return jsonify({"error": f"asset_type inválido. Valores: {', '.join(sorted(_VALID_ASSET_TYPES))}"}), 400
    if quantity is None or entry_price is None or not entry_date_str:
        return jsonify({"error": "quantity, entry_price y entry_date son obligatorios"}), 400

    try:
        quantity = float(quantity)
        entry_price = float(entry_price)
        if quantity <= 0 or entry_price <= 0:
            raise ValueError("quantity y entry_price deben ser positivos")
        entry_date = datetime.date.fromisoformat(entry_date_str)
    except (ValueError, TypeError) as e:
        return jsonify({"error": str(e)}), 400

    try:
        from services.portfolio_tracker import add_position
        pos = add_position(user_id, symbol, asset_type, quantity, entry_price, entry_date, exchange)
        return jsonify(pos), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        current_app.logger.error(f"POST /portfolio/positions error: {e}", exc_info=True)
        return jsonify({"error": "error al guardar la posición"}), 500


# ---------------------------------------------------------------------------
# GET /api/v1/portfolio/positions — listar posiciones
# ---------------------------------------------------------------------------

@portfolio_bp.route("/api/v1/portfolio/positions", methods=["GET"])
@jwt_required()
def list_positions():
    user_id = int(get_jwt_identity())
    try:
        from services.portfolio_tracker import get_positions
        positions = get_positions(user_id)
        return jsonify(positions), 200
    except Exception as e:
        current_app.logger.error(f"GET /portfolio/positions error: {e}", exc_info=True)
        return jsonify({"error": "error obteniendo posiciones"}), 500


# ---------------------------------------------------------------------------
# PUT /api/v1/portfolio/positions/<id> — editar posición
# ---------------------------------------------------------------------------

@portfolio_bp.route("/api/v1/portfolio/positions/<int:pos_id>", methods=["PUT"])
@jwt_required()
def edit_position(pos_id: int):
    user_id = int(get_jwt_identity())
    data = request.get_json(silent=True) or {}

    kwargs = {}
    if "quantity" in data:
        try:
            kwargs["quantity"] = float(data["quantity"])
            if kwargs["quantity"] <= 0:
                raise ValueError("quantity debe ser positivo")
        except (ValueError, TypeError) as e:
            return jsonify({"error": str(e)}), 400

    if "entry_price" in data:
        try:
            kwargs["entry_price"] = float(data["entry_price"])
            if kwargs["entry_price"] <= 0:
                raise ValueError("entry_price debe ser positivo")
        except (ValueError, TypeError) as e:
            return jsonify({"error": str(e)}), 400

    if "entry_date" in data:
        try:
            kwargs["entry_date"] = datetime.date.fromisoformat(data["entry_date"])
        except (ValueError, TypeError):
            return jsonify({"error": "entry_date debe tener formato YYYY-MM-DD"}), 400

    if "exchange" in data:
        kwargs["exchange"] = (data["exchange"] or "").strip() or None

    if not kwargs:
        return jsonify({"error": "No se proporcionó ningún campo para actualizar"}), 400

    try:
        from services.portfolio_tracker import update_position
        pos = update_position(pos_id, user_id, **kwargs)
        return jsonify(pos), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        current_app.logger.error(f"PUT /portfolio/positions/{pos_id} error: {e}", exc_info=True)
        return jsonify({"error": "error actualizando la posición"}), 500


# ---------------------------------------------------------------------------
# DELETE /api/v1/portfolio/positions/<id> — eliminar posición
# ---------------------------------------------------------------------------

@portfolio_bp.route("/api/v1/portfolio/positions/<int:pos_id>", methods=["DELETE"])
@jwt_required()
def remove_position(pos_id: int):
    user_id = int(get_jwt_identity())
    try:
        from services.portfolio_tracker import delete_position
        deleted = delete_position(pos_id, user_id)
        if not deleted:
            return jsonify({"error": "Posición no encontrada"}), 404
        return "", 204
    except Exception as e:
        current_app.logger.error(f"DELETE /portfolio/positions/{pos_id} error: {e}", exc_info=True)
        return jsonify({"error": "error eliminando la posición"}), 500


# ---------------------------------------------------------------------------
# GET /api/v1/portfolio/summary — resumen P&L + benchmark
# ---------------------------------------------------------------------------

@portfolio_bp.route("/api/v1/portfolio/summary", methods=["GET"])
@jwt_required()
def get_summary():
    user_id = int(get_jwt_identity())
    benchmark = (request.args.get("benchmark") or "^GSPC").strip().upper()

    # Aceptar benchmarks predefinidos y cualquier símbolo válido (hasta 10 chars)
    if len(benchmark) > 10:
        return jsonify({"error": "benchmark inválido"}), 400

    try:
        from services.portfolio_tracker import portfolio_summary
        summary = portfolio_summary(user_id, benchmark)
        return jsonify(summary), 200
    except Exception as e:
        current_app.logger.error(f"GET /portfolio/summary error: {e}", exc_info=True)
        return jsonify({"error": "error calculando resumen del portfolio"}), 500
