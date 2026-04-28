"""Blueprint de datos de mercado global — endpoints públicos, sin autenticación.

Endpoints:
  GET /api/v1/market/global           → snapshot completo (índices, crypto, commodities, VIX)
  GET /api/v1/market/quote/<symbol>   → cotización individual
"""
from flask import Blueprint, jsonify, request, current_app

market_bp = Blueprint("market", __name__)

_VALID_ASSET_TYPES = {"index", "stock", "commodity", "crypto"}


@market_bp.route("/api/v1/market/global", methods=["GET"])
def market_global():
    try:
        from services.market_data import get_global_snapshot

        snapshot = get_global_snapshot()
        dq = snapshot.get("data_quality", {})
        if all(v == 0 for v in dq.values()):
            return jsonify({"error": "todas las fuentes de datos no están disponibles"}), 503

        return jsonify(snapshot), 200
    except EnvironmentError as e:
        return jsonify({"error": str(e)}), 503
    except Exception as e:
        current_app.logger.error(f"/api/v1/market/global error: {e}", exc_info=True)
        return jsonify({"error": "error interno del servidor"}), 500


@market_bp.route("/api/v1/market/quote/<symbol>", methods=["GET"])
def market_quote(symbol: str):
    asset_type = request.args.get("asset_type", "index")
    if asset_type not in _VALID_ASSET_TYPES:
        return jsonify({
            "error": f"asset_type inválido. Valores permitidos: {sorted(_VALID_ASSET_TYPES)}"
        }), 400

    try:
        from services.market_data import get_quote

        data = get_quote(symbol.upper(), asset_type)
        if data is None:
            return jsonify({"error": f"No hay datos disponibles para {symbol.upper()}"}), 404

        return jsonify({"symbol": symbol.upper(), "asset_type": asset_type, **data}), 200
    except EnvironmentError as e:
        return jsonify({"error": str(e)}), 503
    except Exception as e:
        current_app.logger.error(f"/api/v1/market/quote/{symbol} error: {e}", exc_info=True)
        return jsonify({"error": "error interno del servidor"}), 500
