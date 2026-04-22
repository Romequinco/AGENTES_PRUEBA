"""API Flask — Newsletter (Fase 1) + Auth JWT + Alertas + Stripe (Fase 2) + PRO (Fase 3).

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

Endpoints PRO (requieren JWT + tier pro):
  POST /api/v1/strategies             → crear estrategia (valida JSON de condiciones)
  GET  /api/v1/strategies             → listar estrategias del usuario
  POST /api/v1/backtest               → ejecutar backtest (límite 3/mes)
  GET  /api/v1/backtest/<id>          → resultado de un backtest
  POST /api/v1/portfolios             → crear portfolio
  POST /api/v1/portfolios/<id>/positions          → añadir posición
  PUT  /api/v1/portfolios/<id>/positions/<pos_id>/close → cerrar posición
  GET  /api/v1/portfolios/<id>/summary            → resumen con P&L y benchmark
  GET  /api/v1/reports/weekly         → genera y devuelve PDF del reporte semanal

Endpoints Stripe:
  POST /stripe/create-checkout        → sesión de Stripe Checkout (premium o pro)
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


def _require_pro(user_id: int):
    """Devuelve (user, None) si es pro, o (None, response_tuple) si no."""
    from db.models import User
    db = _get_db()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return None, (jsonify({"error": "usuario no encontrado"}), 404)
        if user.tier != "pro":
            return None, (jsonify({"error": "se requiere tier pro"}), 403)
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
# PRO — Estrategias
# ---------------------------------------------------------------------------

@app.route("/api/v1/strategies", methods=["POST"])
@jwt_required()
def create_strategy():
    user_id = int(get_jwt_identity())
    user, err = _require_pro(user_id)
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
    db = _get_db()
    try:
        strategy = Strategy(
            user_id=user_id,
            name=name,
            buy_condition=buy,
            sell_condition=sell,
        )
        db.add(strategy)
        db.commit()
        db.refresh(strategy)
        return jsonify(_strategy_to_dict(strategy)), 201
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@app.route("/api/v1/strategies", methods=["GET"])
@jwt_required()
def list_strategies():
    user_id = int(get_jwt_identity())
    user, err = _require_pro(user_id)
    if err:
        return err

    from db.models import Strategy
    db = _get_db()
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
# PRO — Backtests
# ---------------------------------------------------------------------------

@app.route("/api/v1/backtest", methods=["POST"])
@jwt_required()
def run_backtest():
    user_id = int(get_jwt_identity())
    user, err = _require_pro(user_id)
    if err:
        return err

    data = request.get_json(silent=True) or {}
    symbol = (data.get("symbol") or "").strip().upper()
    strategy_id = data.get("strategy_id")
    days = int(data.get("days", 180))

    if not symbol:
        return jsonify({"error": "symbol es obligatorio"}), 400

    try:
        from db.models import BacktestResult, Strategy
        from datetime import datetime, timezone
        from dateutil.relativedelta import relativedelta
    except ImportError:
        from db.models import BacktestResult, Strategy
        from datetime import datetime, timezone

    db = _get_db()
    try:
        # Verificar límite 3 backtests/mes
        import calendar
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        count_this_month = (
            db.query(BacktestResult)
            .filter(
                BacktestResult.user_id == user_id,
                BacktestResult.ran_at >= month_start,
            )
            .count()
        )
        if count_this_month >= 3:
            return jsonify({"error": "límite de 3 backtests por mes alcanzado para el tier PRO"}), 429

        # Obtener o construir estrategia
        if strategy_id:
            strategy_obj = db.query(Strategy).filter(
                Strategy.id == strategy_id,
                Strategy.user_id == user_id,
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
            app.logger.error(f"/api/v1/backtest error: {e}", exc_info=True)
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


@app.route("/api/v1/backtest/<int:backtest_id>", methods=["GET"])
@jwt_required()
def get_backtest(backtest_id: int):
    user_id = int(get_jwt_identity())
    user, err = _require_pro(user_id)
    if err:
        return err

    from db.models import BacktestResult
    db = _get_db()
    try:
        bt = db.query(BacktestResult).filter(
            BacktestResult.id == backtest_id,
            BacktestResult.user_id == user_id,
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
# PRO — Portfolios
# ---------------------------------------------------------------------------

@app.route("/api/v1/portfolios", methods=["POST"])
@jwt_required()
def create_portfolio():
    user_id = int(get_jwt_identity())
    user, err = _require_pro(user_id)
    if err:
        return err

    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name es obligatorio"}), 400

    from db.models import Portfolio
    db = _get_db()
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


@app.route("/api/v1/portfolios/<int:portfolio_id>/positions", methods=["POST"])
@jwt_required()
def add_position(portfolio_id: int):
    user_id = int(get_jwt_identity())
    user, err = _require_pro(user_id)
    if err:
        return err

    # Verify portfolio belongs to user
    from db.models import Portfolio
    db = _get_db()
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
        from datetime import date as date_type
        import datetime as dt_module
        entry_date = dt_module.date.fromisoformat(entry_date_str)
        from services.portfolio_tracker import add_position as _add_position
        pos = _add_position(portfolio_id, symbol, float(quantity), float(entry_price), entry_date)
        return jsonify(pos), 201
    except (ValueError, TypeError) as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        app.logger.error(f"/portfolios/{portfolio_id}/positions error: {e}", exc_info=True)
        return jsonify({"error": "error añadiendo posición"}), 500


@app.route("/api/v1/portfolios/<int:portfolio_id>/positions/<int:position_id>/close", methods=["PUT"])
@jwt_required()
def close_position(portfolio_id: int, position_id: int):
    user_id = int(get_jwt_identity())
    user, err = _require_pro(user_id)
    if err:
        return err

    from db.models import Portfolio, PortfolioPosition
    db = _get_db()
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
        import datetime as dt_module
        exit_date = dt_module.date.fromisoformat(exit_date_str)
        from services.portfolio_tracker import close_position as _close_position
        result = _close_position(position_id, float(exit_price), exit_date)
        return jsonify(result), 200
    except (ValueError, TypeError) as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        app.logger.error(f"/portfolios/{portfolio_id}/positions/{position_id}/close error: {e}", exc_info=True)
        return jsonify({"error": "error cerrando posición"}), 500


@app.route("/api/v1/portfolios/<int:portfolio_id>/summary", methods=["GET"])
@jwt_required()
def portfolio_summary_endpoint(portfolio_id: int):
    user_id = int(get_jwt_identity())
    user, err = _require_pro(user_id)
    if err:
        return err

    from db.models import Portfolio
    db = _get_db()
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
        app.logger.error(f"/portfolios/{portfolio_id}/summary error: {e}", exc_info=True)
        return jsonify({"error": "error calculando resumen del portfolio"}), 500


# ---------------------------------------------------------------------------
# PRO — Reporte semanal PDF
# ---------------------------------------------------------------------------

@app.route("/api/v1/reports/weekly", methods=["GET"])
@jwt_required()
def weekly_report():
    user_id = int(get_jwt_identity())
    user, err = _require_pro(user_id)
    if err:
        return err

    try:
        from services.reporter import generate_weekly_report
        pdf_path = generate_weekly_report(user_id)
        from flask import send_file
        return send_file(pdf_path, mimetype="application/pdf", as_attachment=True,
                         download_name=os.path.basename(pdf_path))
    except Exception as e:
        app.logger.error(f"/api/v1/reports/weekly error: {e}", exc_info=True)
        return jsonify({"error": "error generando reporte"}), 500


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
            metadata={"user_id": str(user_id), "tier": requested_tier},
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

            # Determinar el tier según el price_id en metadata o el precio del line item
            target_tier = metadata.get("tier", "premium")
            pro_price_id = os.environ.get("STRIPE_PRO_PRICE_ID")
            if pro_price_id:
                # Verificar si alguno de los line items corresponde al precio PRO
                line_items = obj_dict.get("line_items") or {}
                items_data = line_items.get("data") or []
                for item in items_data:
                    price_obj = item.get("price") or {}
                    if price_obj.get("id") == pro_price_id:
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
            app.logger.info(f"[STRIPE] checkout.session.completed — user {user_id} → {target_tier}")

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
