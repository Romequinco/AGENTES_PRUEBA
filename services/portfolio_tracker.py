"""Portfolio tracker global — multi-asset (stock, etf, crypto, commodity).

Usa get_quote() de services/market_data.py para precios en tiempo real.
Todas las funciones abren y cierran su propia sesión de DB.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Optional

logger = logging.getLogger("bolsa.portfolio_tracker")

VALID_ASSET_TYPES = {"stock", "etf", "crypto", "commodity"}

BENCHMARK_LABELS = {
    "^GSPC": "S&P 500",
    "^IBEX": "IBEX 35",
    "^IXIC": "Nasdaq",
}


def add_position(
    user_id: int,
    symbol: str,
    asset_type: str,
    quantity: float,
    entry_price: float,
    entry_date: date,
    exchange: Optional[str] = None,
) -> dict:
    """Añade una posición al portfolio del usuario.

    Valida que el símbolo existe en el mercado antes de guardar.
    Raises ValueError si el símbolo no se encuentra o asset_type es inválido.
    """
    if asset_type not in VALID_ASSET_TYPES:
        raise ValueError(f"asset_type inválido. Debe ser uno de: {', '.join(sorted(VALID_ASSET_TYPES))}")

    from services.market_data import get_quote
    quote = get_quote(symbol.upper(), asset_type)
    if quote is None:
        raise ValueError(f"Símbolo '{symbol}' no encontrado en los mercados")

    from db.models import PortfolioPosition, get_db_session
    db = get_db_session()
    try:
        pos = PortfolioPosition(
            user_id=user_id,
            symbol=symbol.upper(),
            asset_type=asset_type,
            exchange=exchange,
            quantity=float(quantity),
            entry_price=float(entry_price),
            entry_date=entry_date,
        )
        db.add(pos)
        db.commit()
        db.refresh(pos)
        return _position_to_dict(pos)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_positions(user_id: int) -> list[dict]:
    """Retorna todas las posiciones abiertas del usuario con precio actual."""
    from db.models import PortfolioPosition, get_db_session
    from services.market_data import get_quote

    db = get_db_session()
    try:
        positions = (
            db.query(PortfolioPosition)
            .filter(
                PortfolioPosition.user_id == user_id,
                PortfolioPosition.exit_price.is_(None),
            )
            .order_by(PortfolioPosition.entry_date.desc())
            .all()
        )

        result = []
        for pos in positions:
            current_price = None
            try:
                quote = get_quote(pos.symbol, pos.asset_type)
                if quote:
                    current_price = quote["price"]
            except Exception as e:
                logger.warning(f"No se pudo obtener precio de {pos.symbol}: {e}")
            result.append(_position_to_dict(pos, current_price))
        return result
    finally:
        db.close()


def update_position(
    position_id: int,
    user_id: int,
    quantity: Optional[float] = None,
    entry_price: Optional[float] = None,
    entry_date: Optional[date] = None,
    exchange: Optional[str] = None,
) -> dict:
    """Edita campos de una posición existente. Valida ownership."""
    from db.models import PortfolioPosition, get_db_session

    db = get_db_session()
    try:
        pos = db.query(PortfolioPosition).filter(
            PortfolioPosition.id == position_id,
            PortfolioPosition.user_id == user_id,
        ).first()
        if not pos:
            raise ValueError(f"Posición {position_id} no encontrada")

        if quantity is not None:
            pos.quantity = float(quantity)
        if entry_price is not None:
            pos.entry_price = float(entry_price)
        if entry_date is not None:
            pos.entry_date = entry_date
        if exchange is not None:
            pos.exchange = exchange

        db.commit()
        db.refresh(pos)
        return _position_to_dict(pos)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def delete_position(position_id: int, user_id: int) -> bool:
    """Elimina una posición. Valida ownership. Retorna True si se eliminó."""
    from db.models import PortfolioPosition, get_db_session

    db = get_db_session()
    try:
        pos = db.query(PortfolioPosition).filter(
            PortfolioPosition.id == position_id,
            PortfolioPosition.user_id == user_id,
        ).first()
        if not pos:
            return False
        db.delete(pos)
        db.commit()
        return True
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def portfolio_summary(user_id: int, benchmark: str = "^GSPC") -> dict:
    """Calcula el resumen completo del portfolio con P&L y benchmark configurable.

    Returns dict con: positions, pnl_by_asset_type, allocation, totales y benchmark.
    """
    from db.models import PortfolioPosition, get_db_session
    from services.market_data import get_quote, get_historical

    db = get_db_session()
    try:
        positions = (
            db.query(PortfolioPosition)
            .filter(
                PortfolioPosition.user_id == user_id,
                PortfolioPosition.exit_price.is_(None),
            )
            .all()
        )

        if not positions:
            return {
                "positions": [],
                "pnl_by_asset_type": {},
                "allocation": {},
                "total_cost": 0.0,
                "total_value": 0.0,
                "total_pnl": 0.0,
                "total_pnl_pct": None,
                "benchmark": benchmark,
                "benchmark_label": BENCHMARK_LABELS.get(benchmark, benchmark),
                "benchmark_return_pct": None,
            }

        position_details = []
        total_cost = 0.0
        total_value = 0.0
        pnl_by_type: dict[str, dict] = {}
        value_by_type: dict[str, float] = {}

        for pos in positions:
            cost = pos.quantity * pos.entry_price
            current_price = None
            try:
                quote = get_quote(pos.symbol, pos.asset_type)
                if quote:
                    current_price = quote["price"]
            except Exception as e:
                logger.warning(f"No se pudo obtener precio de {pos.symbol}: {e}")

            effective_price = current_price if current_price is not None else pos.entry_price
            current_val = pos.quantity * effective_price
            pnl = current_val - cost
            pnl_pct = (pnl / cost * 100) if cost > 0 else None

            total_cost += cost
            total_value += current_val

            atype = pos.asset_type
            if atype not in pnl_by_type:
                pnl_by_type[atype] = {"pnl": 0.0, "cost": 0.0}
                value_by_type[atype] = 0.0
            pnl_by_type[atype]["pnl"] += pnl
            pnl_by_type[atype]["cost"] += cost
            value_by_type[atype] += current_val

            position_details.append(_position_to_dict(pos, current_price))

        total_pnl = total_value - total_cost
        total_pnl_pct = round(total_pnl / total_cost * 100, 2) if total_cost > 0 else None

        pnl_by_asset_type = {}
        for atype, data in pnl_by_type.items():
            pnl_by_asset_type[atype] = {
                "pnl": round(data["pnl"], 4),
                "pct": round(data["pnl"] / data["cost"] * 100, 2) if data["cost"] > 0 else None,
            }

        allocation = {}
        if total_value > 0:
            for atype, val in value_by_type.items():
                allocation[atype] = round(val / total_value * 100, 2)

        benchmark_return_pct = _benchmark_return(benchmark, get_historical)

        return {
            "positions": position_details,
            "pnl_by_asset_type": pnl_by_asset_type,
            "allocation": allocation,
            "total_cost": round(total_cost, 4),
            "total_value": round(total_value, 4),
            "total_pnl": round(total_pnl, 4),
            "total_pnl_pct": total_pnl_pct,
            "benchmark": benchmark,
            "benchmark_label": BENCHMARK_LABELS.get(benchmark, benchmark),
            "benchmark_return_pct": benchmark_return_pct,
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _position_to_dict(pos, current_price: Optional[float] = None) -> dict:
    cost = pos.quantity * pos.entry_price
    pnl = None
    pnl_pct = None
    if current_price is not None:
        pnl = round((current_price - pos.entry_price) * pos.quantity, 4)
        pnl_pct = round((current_price - pos.entry_price) / pos.entry_price * 100, 2) if pos.entry_price else None

    return {
        "id": pos.id,
        "user_id": pos.user_id,
        "symbol": pos.symbol,
        "asset_type": pos.asset_type,
        "exchange": pos.exchange,
        "quantity": pos.quantity,
        "entry_price": pos.entry_price,
        "entry_date": str(pos.entry_date),
        "current_price": current_price,
        "cost": round(cost, 4),
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "created_at": pos.created_at.isoformat() if pos.created_at else None,
    }


def _benchmark_return(symbol: str, get_historical_fn) -> Optional[float]:
    """Calcula el retorno del benchmark en el último mes."""
    try:
        bars = get_historical_fn(symbol, period="1m")
        if not bars or len(bars) < 2:
            return None
        first = bars[0].get("close") or bars[0].get("open")
        last = bars[-1].get("close")
        if not first or not last or first <= 0:
            return None
        return round((last - first) / first * 100, 2)
    except Exception as e:
        logger.warning(f"Error calculando benchmark {symbol}: {e}")
        return None
