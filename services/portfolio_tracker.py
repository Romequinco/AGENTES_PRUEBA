"""Portfolio tracker — portfolios teóricos (sin dinero real ni brokers).

Todas las funciones abren y cierran su propia sesión de DB.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Optional

import yfinance as yf

logger = logging.getLogger("bolsa.portfolio_tracker")


def add_position(
    portfolio_id: int,
    symbol: str,
    quantity: float,
    entry_price: float,
    entry_date: date,
) -> dict:
    """Añade una posición abierta al portfolio.

    Returns:
        dict con los datos de la posición creada.
    """
    from db.models import Portfolio, PortfolioPosition, get_db_session

    db = get_db_session()
    try:
        portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
        if not portfolio:
            raise ValueError(f"Portfolio {portfolio_id} no encontrado")

        pos = PortfolioPosition(
            portfolio_id=portfolio_id,
            symbol=symbol.upper(),
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


def close_position(
    position_id: int,
    exit_price: float,
    exit_date: date,
) -> dict:
    """Cierra una posición registrando el precio y fecha de salida.

    Returns:
        dict con los datos actualizados de la posición.
    """
    from db.models import PortfolioPosition, get_db_session

    db = get_db_session()
    try:
        pos = db.query(PortfolioPosition).filter(PortfolioPosition.id == position_id).first()
        if not pos:
            raise ValueError(f"Posición {position_id} no encontrada")
        if pos.exit_price is not None:
            raise ValueError(f"La posición {position_id} ya está cerrada")

        pos.exit_price = float(exit_price)
        pos.exit_date = exit_date
        db.commit()
        db.refresh(pos)
        return _position_to_dict(pos)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def portfolio_summary(portfolio_id: int) -> dict:
    """Calcula el resumen del portfolio con P&L y benchmark IBEX.

    Returns:
        dict con: portfolio_id, name, positions (lista), total_value,
        total_pnl, total_pnl_pct, benchmark_ibex_pct.
    """
    from db.models import Portfolio, PortfolioPosition, get_db_session

    db = get_db_session()
    try:
        portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
        if not portfolio:
            raise ValueError(f"Portfolio {portfolio_id} no encontrado")

        positions = (
            db.query(PortfolioPosition)
            .filter(PortfolioPosition.portfolio_id == portfolio_id)
            .all()
        )

        if not positions:
            return {
                "portfolio_id": portfolio_id,
                "name": portfolio.name,
                "positions": [],
                "total_value": 0.0,
                "total_cost": 0.0,
                "total_pnl": 0.0,
                "total_pnl_pct": None,
                "benchmark_ibex_pct": None,
            }

        # Determine oldest entry date for benchmark comparison
        oldest_entry = min(p.entry_date for p in positions)

        # Fetch live prices for open positions
        open_symbols = list({p.symbol for p in positions if p.exit_price is None})
        live_prices: dict[str, float] = {}
        if open_symbols:
            live_prices = _fetch_live_prices(open_symbols)

        position_details = []
        total_cost = 0.0
        total_value = 0.0

        for pos in positions:
            cost = pos.quantity * pos.entry_price
            total_cost += cost

            if pos.exit_price is not None:
                current_price = pos.exit_price
                status = "closed"
            else:
                current_price = live_prices.get(pos.symbol, pos.entry_price)
                status = "open"

            current_value = pos.quantity * current_price
            total_value += current_value
            pnl = current_value - cost
            pnl_pct = (pnl / cost * 100) if cost > 0 else None

            position_details.append({
                **_position_to_dict(pos),
                "current_price": round(current_price, 4),
                "current_value": round(current_value, 4),
                "cost": round(cost, 4),
                "pnl": round(pnl, 4),
                "pnl_pct": round(pnl_pct, 2) if pnl_pct is not None else None,
                "status": status,
            })

        total_pnl = total_value - total_cost
        total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else None

        benchmark_pct = _ibex_return_since(oldest_entry)

        return {
            "portfolio_id": portfolio_id,
            "name": portfolio.name,
            "positions": position_details,
            "total_value": round(total_value, 4),
            "total_cost": round(total_cost, 4),
            "total_pnl": round(total_pnl, 4),
            "total_pnl_pct": round(total_pnl_pct, 2) if total_pnl_pct is not None else None,
            "benchmark_ibex_pct": benchmark_pct,
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _position_to_dict(pos) -> dict:
    return {
        "id": pos.id,
        "portfolio_id": pos.portfolio_id,
        "symbol": pos.symbol,
        "quantity": pos.quantity,
        "entry_price": pos.entry_price,
        "entry_date": str(pos.entry_date),
        "exit_price": pos.exit_price,
        "exit_date": str(pos.exit_date) if pos.exit_date else None,
    }


def _fetch_live_prices(symbols: list[str]) -> dict[str, float]:
    prices = {}
    for symbol in symbols:
        try:
            hist = yf.download(symbol, period="2d", progress=False, auto_adjust=True)
            if not hist.empty:
                close = hist["Close"].squeeze()
                prices[symbol] = float(close.iloc[-1])
        except Exception as e:
            logger.warning(f"No se pudo obtener precio de {symbol}: {e}")
    return prices


def _ibex_return_since(start_date: date) -> Optional[float]:
    """Calcula el retorno del IBEX 35 desde *start_date* hasta hoy."""
    try:
        start_str = start_date.strftime("%Y-%m-%d")
        hist = yf.download("^IBEX", start=start_str, progress=False, auto_adjust=True)
        if hist.empty or len(hist) < 2:
            return None
        close = hist["Close"].squeeze()
        first = float(close.iloc[0])
        last = float(close.iloc[-1])
        if first <= 0:
            return None
        return round((last - first) / first * 100, 2)
    except Exception as e:
        logger.warning(f"Error calculando benchmark IBEX: {e}")
        return None
