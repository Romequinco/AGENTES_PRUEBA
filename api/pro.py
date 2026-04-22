"""Blueprint de endpoints PRO (tier pro exclusivo).

Endpoints (sin prefijo, todos requieren JWT + tier pro):
  POST /api/v1/strategies                                    → crear estrategia
  GET  /api/v1/strategies                                    → listar estrategias
  POST /api/v1/backtest                                      → ejecutar backtest (límite 3/mes)
  GET  /api/v1/backtest/<id>                                 → resultado de un backtest
  POST /api/v1/portfolios                                    → crear portfolio
  POST /api/v1/portfolios/<id>/positions                     → añadir posición
  PUT  /api/v1/portfolios/<id>/positions/<pos_id>/close      → cerrar posición
  GET  /api/v1/portfolios/<id>/summary                       → resumen con P&L y benchmark
  GET  /api/v1/reports/weekly                                → genera y devuelve PDF semanal
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
# Portfolios
# ---------------------------------------------------------------------------

@pro_bp.route("/api/v1/portfolios", methods=["POST"])
@jwt_required()
def create_portfolio():
    user_id = int(get_jwt_identity())
    user, err = require_pro(user_id)
    if err:
        return err

    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name es obligatorio"}), 400

    from db.models import Portfolio
    db = get_db()
    try:
        portfolio = Portfolio(user_id=user_id, name=name)
        db.add(portfolio)
        db.commit()
        db.refresh(portfolio)
        return jsonify({
            "id": portfolio.id,
            "name": portfolio.name,
            "created_at": portfolio.created_at.isoformat() if portfolio.created_at else None,
        }), 201
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@pro_bp.route("/api/v1/portfolios/<int:portfolio_id>/positions", methods=["POST"])
@jwt_required()
def add_position(portfolio_id: int):
    user_id = int(get_jwt_identity())
    user, err = require_pro(user_id)
    if err:
        return err

    from db.models import Portfolio
    db = get_db()
    try:
        p = db.query(Portfolio).filter(Portfolio.id == portfolio_id, Portfolio.user_id == user_id).first()
        if not p:
            return jsonify({"error": "portfolio no encontrado"}), 404
    finally:
        db.close()

    data = request.get_json(silent=True) or {}
    symbol = (data.get("symbol") or "").strip().upper()
    quantity = data.get("quantity")
    entry_price = data.get("entry_price")
    entry_date_str = data.get("entry_date")

    if not symbol:
        return jsonify({"error": "symbol es obligatorio"}), 400
    if quantity is None or entry_price is None or not entry_date_str:
        return jsonify({"error": "quantity, entry_price y entry_date son obligatorios"}), 400

    try:
        entry_date = datetime.date.fromisoformat(entry_date_str)
        from services.portfolio_tracker import add_position as _add_position
        pos = _add_position(portfolio_id, symbol, float(quantity), float(entry_price), entry_date)
        return jsonify(pos), 201
    except (ValueError, TypeError) as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        current_app.logger.error(f"/portfolios/{portfolio_id}/positions error: {e}", exc_info=True)
        return jsonify({"error": "error añadiendo posición"}), 500


@pro_bp.route("/api/v1/portfolios/<int:portfolio_id>/positions/<int:position_id>/close", methods=["PUT"])
@jwt_required()
def close_position(portfolio_id: int, position_id: int):
    user_id = int(get_jwt_identity())
    user, err = require_pro(user_id)
    if err:
        return err

    from db.models import Portfolio, PortfolioPosition
    db = get_db()
    try:
        p = db.query(Portfolio).filter(Portfolio.id == portfolio_id, Portfolio.user_id == user_id).first()
        if not p:
            return jsonify({"error": "portfolio no encontrado"}), 404
        pos = db.query(PortfolioPosition).filter(
            PortfolioPosition.id == position_id,
            PortfolioPosition.portfolio_id == portfolio_id,
        ).first()
        if not pos:
            return jsonify({"error": "posición no encontrada"}), 404
    finally:
        db.close()

    data = request.get_json(silent=True) or {}
    exit_price = data.get("exit_price")
    exit_date_str = data.get("exit_date")

    if exit_price is None or not exit_date_str:
        return jsonify({"error": "exit_price y exit_date son obligatorios"}), 400

    try:
        exit_date = datetime.date.fromisoformat(exit_date_str)
        from services.portfolio_tracker import close_position as _close_position
        result = _close_position(position_id, float(exit_price), exit_date)
        return jsonify(result), 200
    except (ValueError, TypeError) as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        current_app.logger.error(
            f"/portfolios/{portfolio_id}/positions/{position_id}/close error: {e}", exc_info=True
        )
        return jsonify({"error": "error cerrando posición"}), 500


@pro_bp.route("/api/v1/portfolios/<int:portfolio_id>/summary", methods=["GET"])
@jwt_required()
def portfolio_summary_endpoint(portfolio_id: int):
    user_id = int(get_jwt_identity())
    user, err = require_pro(user_id)
    if err:
        return err

    from db.models import Portfolio
    db = get_db()
    try:
        p = db.query(Portfolio).filter(Portfolio.id == portfolio_id, Portfolio.user_id == user_id).first()
        if not p:
            return jsonify({"error": "portfolio no encontrado"}), 404
    finally:
        db.close()

    try:
        from services.portfolio_tracker import portfolio_summary
        summary = portfolio_summary(portfolio_id)
        return jsonify(summary), 200
    except Exception as e:
        current_app.logger.error(f"/portfolios/{portfolio_id}/summary error: {e}", exc_info=True)
        return jsonify({"error": "error calculando resumen del portfolio"}), 500


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
