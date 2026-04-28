"""Blueprint de endpoints PRO (tier pro exclusivo).

Endpoints (sin prefijo, todos requieren JWT + tier pro):
  POST /api/v1/strategies                                    → crear estrategia
  GET  /api/v1/strategies                                    → listar estrategias
  POST /api/v1/backtest                                      → ejecutar backtest (límite 3/mes)
  GET  /api/v1/backtest/<id>                                 → resultado de un backtest
  GET  /api/v1/reports/weekly                                → genera y devuelve PDF semanal

Los endpoints de portfolio se han movido a api/portfolio.py (acceso gratuito).
"""

import datetime
import os

from flask import Blueprint, request, jsonify, send_file, current_app
from flask_jwt_extended import jwt_required, get_jwt_identity

from api.helpers import get_db, require_pro

pro_bp = Blueprint("pro", __name__)


# ---------------------------------------------------------------------------
# Estrategias
# ---------------------------------------------------------------------------

@pro_bp.route("/api/v1/strategies", methods=["POST"])
@jwt_required()
def create_strategy():
    user_id = int(get_jwt_identity())
    user, err = require_pro(user_id)
    if err:
        return err

    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    buy = data.get("buy")
    sell = data.get("sell")

    if not name:
        return jsonify({"error": "name es obligatorio"}), 400
    if not buy or not sell:
        return jsonify({"error": "buy y sell son obligatorios"}), 400

    try:
        from services.backtester import validate_strategy
        validate_strategy({"buy": buy, "sell": sell})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    from db.models import Strategy
    db = get_db()
    try:
        strategy = Strategy(user_id=user_id, name=name, buy_condition=buy, sell_condition=sell)
        db.add(strategy)
        db.commit()
        db.refresh(strategy)
        return jsonify(_strategy_to_dict(strategy)), 201
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@pro_bp.route("/api/v1/strategies", methods=["GET"])
@jwt_required()
def list_strategies():
    user_id = int(get_jwt_identity())
    user, err = require_pro(user_id)
    if err:
        return err

    from db.models import Strategy
    db = get_db()
    try:
        strategies = db.query(Strategy).filter(Strategy.user_id == user_id).all()
        return jsonify([_strategy_to_dict(s) for s in strategies]), 200
    finally:
        db.close()


def _strategy_to_dict(s) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "buy": s.buy_condition,
        "sell": s.sell_condition,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


# ---------------------------------------------------------------------------
# Backtests
# ---------------------------------------------------------------------------

@pro_bp.route("/api/v1/backtest", methods=["POST"])
@jwt_required()
def run_backtest():
    user_id = int(get_jwt_identity())
    user, err = require_pro(user_id)
    if err:
        return err

    data = request.get_json(silent=True) or {}
    symbol = (data.get("symbol") or "").strip().upper()
    strategy_id = data.get("strategy_id")
    days = int(data.get("days", 180))

    if not symbol:
        return jsonify({"error": "symbol es obligatorio"}), 400

    from db.models import BacktestResult, Strategy
    from datetime import datetime, timezone

    db = get_db()
    try:
        # Verificar límite de 3 backtests por mes
        now = datetime.now(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        count_this_month = (
            db.query(BacktestResult)
            .filter(BacktestResult.user_id == user_id, BacktestResult.ran_at >= month_start)
            .count()
        )
        if count_this_month >= 3:
            return jsonify({"error": "límite de 3 backtests por mes alcanzado para el tier PRO"}), 429

        # Resolver estrategia
        if strategy_id:
            strategy_obj = db.query(Strategy).filter(
                Strategy.id == strategy_id, Strategy.user_id == user_id,
            ).first()
            if not strategy_obj:
                return jsonify({"error": "estrategia no encontrada"}), 404
            strategy_dict = {"buy": strategy_obj.buy_condition, "sell": strategy_obj.sell_condition}
        else:
            buy = data.get("buy")
            sell = data.get("sell")
            if not buy or not sell:
                return jsonify({"error": "Proporciona strategy_id o bien buy y sell"}), 400
            strategy_dict = {"buy": buy, "sell": sell}

        try:
            from services.backtester import backtest
            result = backtest(symbol, strategy_dict, days=days)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            current_app.logger.error(f"/api/v1/backtest error: {e}", exc_info=True)
            return jsonify({"error": "error ejecutando backtest"}), 500

        bt = BacktestResult(
            user_id=user_id,
            strategy_id=strategy_id,
            symbol=symbol,
            days_tested=days,
            total_trades=result["total_trades"],
            win_rate=result.get("win_rate"),
            total_return_pct=result.get("total_return_pct"),
            max_drawdown_pct=result.get("max_drawdown_pct"),
        )
        db.add(bt)
        db.commit()
        db.refresh(bt)
        return jsonify({**result, "backtest_id": bt.id}), 200
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@pro_bp.route("/api/v1/backtest/<int:backtest_id>", methods=["GET"])
@jwt_required()
def get_backtest(backtest_id: int):
    user_id = int(get_jwt_identity())
    user, err = require_pro(user_id)
    if err:
        return err

    from db.models import BacktestResult
    db = get_db()
    try:
        bt = db.query(BacktestResult).filter(
            BacktestResult.id == backtest_id, BacktestResult.user_id == user_id,
        ).first()
        if not bt:
            return jsonify({"error": "backtest no encontrado"}), 404
        return jsonify({
            "id": bt.id,
            "symbol": bt.symbol,
            "strategy_id": bt.strategy_id,
            "days_tested": bt.days_tested,
            "total_trades": bt.total_trades,
            "win_rate": bt.win_rate,
            "total_return_pct": bt.total_return_pct,
            "max_drawdown_pct": bt.max_drawdown_pct,
            "ran_at": bt.ran_at.isoformat() if bt.ran_at else None,
        }), 200
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Reporte semanal PDF
# ---------------------------------------------------------------------------

@pro_bp.route("/api/v1/reports/weekly", methods=["GET"])
@jwt_required()
def weekly_report():
    user_id = int(get_jwt_identity())
    user, err = require_pro(user_id)
    if err:
        return err

    try:
        from services.reporter import generate_weekly_report
        pdf_path = generate_weekly_report(user_id)
        return send_file(
            pdf_path,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=os.path.basename(pdf_path),
        )
    except Exception as e:
        current_app.logger.error(f"/api/v1/reports/weekly error: {e}", exc_info=True)
        return jsonify({"error": "error generando reporte"}), 500
