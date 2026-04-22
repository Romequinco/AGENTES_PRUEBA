"""Generador de reportes semanales PDF para usuarios PRO.

Genera el PDF bajo demanda (no automáticamente — la automatización se añade en Fase 4).
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta, timezone

import yfinance as yf
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable,
)

logger = logging.getLogger("bolsa.reporter")

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "output")

# Top 5 IBEX por capitalización aproximada (actualizable)
TOP5_IBEX = ["ITX.MC", "SAN.MC", "IBE.MC", "BBVA.MC", "REP.MC"]


def generate_weekly_report(user_id: int) -> str:
    """Genera el reporte semanal PDF para *user_id*.

    Returns:
        Ruta absoluta al archivo PDF generado.
    """
    today = date.today()
    filename = f"weekly_{user_id}_{today.strftime('%Y-%m-%d')}.pdf"
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output_path = os.path.join(OUTPUT_DIR, filename)

    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "Title",
        parent=styles["Heading1"],
        fontSize=18,
        leading=22,
        alignment=TA_CENTER,
        spaceAfter=6,
    )
    subtitle_style = ParagraphStyle(
        "Subtitle",
        parent=styles["Normal"],
        fontSize=10,
        alignment=TA_CENTER,
        spaceAfter=12,
        textColor=colors.grey,
    )
    section_style = ParagraphStyle(
        "Section",
        parent=styles["Heading2"],
        fontSize=13,
        leading=16,
        spaceBefore=14,
        spaceAfter=6,
    )
    body_style = ParagraphStyle(
        "Body",
        parent=styles["Normal"],
        fontSize=9,
        leading=12,
        alignment=TA_LEFT,
        spaceAfter=4,
    )

    story = []

    # ── Cabecera ──────────────────────────────────────────────────────────────
    story.append(Paragraph("Reporte Semanal IBEX 35", title_style))
    story.append(Paragraph(
        f"Usuario {user_id} · Generado el {today.strftime('%d/%m/%Y')}",
        subtitle_style,
    ))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#1e3a5f")))
    story.append(Spacer(1, 0.3 * cm))

    # ── 1. Resumen semanal IBEX ───────────────────────────────────────────────
    story.append(Paragraph("1. Resumen de la semana — IBEX 35", section_style))
    ibex_data = _ibex_weekly_summary()
    if ibex_data:
        rows = [["Métrica", "Valor"]]
        rows += [[k, v] for k, v in ibex_data.items()]
        t = Table(rows, colWidths=[8 * cm, 8 * cm])
        t.setStyle(_default_table_style())
        story.append(t)
    else:
        story.append(Paragraph("No se pudieron obtener datos del IBEX esta semana.", body_style))
    story.append(Spacer(1, 0.4 * cm))

    # ── 2. Análisis fundamental top 5 ────────────────────────────────────────
    story.append(Paragraph("2. Análisis fundamental — Top 5 valores IBEX", section_style))
    story.append(Paragraph(
        "Datos fundamentales de los 5 mayores valores del índice por capitalización.",
        body_style,
    ))
    story.append(Spacer(1, 0.2 * cm))
    fund_rows = [["Símbolo", "P/E", "Div. Yield", "ROE", "Deuda/Eq.", "Market Cap"]]
    for sym in TOP5_IBEX:
        try:
            from services.fundamental_analyzer import fundamental_data
            fd = fundamental_data(sym)
            fund_rows.append([
                sym,
                _fmt(fd.get("pe_ratio"), ".1f"),
                _fmt_pct(fd.get("dividend_yield")),
                _fmt_pct(fd.get("roe")),
                _fmt(fd.get("debt_to_equity"), ".2f"),
                _fmt_bn(fd.get("market_cap")),
            ])
        except Exception as e:
            logger.warning(f"Error obteniendo fundamentales de {sym}: {e}")
            fund_rows.append([sym] + ["—"] * 5)

    t = Table(fund_rows, colWidths=[3 * cm, 2.5 * cm, 2.5 * cm, 2.5 * cm, 2.5 * cm, 3 * cm])
    t.setStyle(_default_table_style())
    story.append(t)
    story.append(Spacer(1, 0.4 * cm))

    # ── 3. Portfolio del usuario ──────────────────────────────────────────────
    story.append(Paragraph("3. Rendimiento del portfolio", section_style))
    portfolio_section = _user_portfolio_section(user_id, body_style)
    story.extend(portfolio_section)
    story.append(Spacer(1, 0.4 * cm))

    # ── 4. Backtests de la semana ─────────────────────────────────────────────
    story.append(Paragraph("4. Backtests ejecutados esta semana", section_style))
    bt_section = _user_backtests_section(user_id, body_style)
    story.extend(bt_section)

    # ── Build ─────────────────────────────────────────────────────────────────
    doc.build(story)
    logger.info(f"[REPORTER] PDF generado: {output_path}")
    return os.path.abspath(output_path)


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def _ibex_weekly_summary() -> dict:
    try:
        hist = yf.download("^IBEX", period="5d", progress=False, auto_adjust=True)
        if hist.empty or len(hist) < 2:
            return {}
        close = hist["Close"].squeeze()
        open_ = hist["Open"].squeeze()
        high = hist["High"].squeeze()
        low = hist["Low"].squeeze()
        vol = hist["Volume"].squeeze()

        week_open = float(open_.iloc[0])
        week_close = float(close.iloc[-1])
        week_change = (week_close - week_open) / week_open * 100
        return {
            "Apertura semana": f"{week_open:,.2f}",
            "Cierre semana": f"{week_close:,.2f}",
            "Variación semanal": f"{week_change:+.2f}%",
            "Máximo semana": f"{float(high.max()):,.2f}",
            "Mínimo semana": f"{float(low.min()):,.2f}",
            "Volumen total": f"{int(vol.sum()):,}",
        }
    except Exception as e:
        logger.warning(f"Error obteniendo resumen IBEX: {e}")
        return {}


def _user_portfolio_section(user_id: int, body_style) -> list:
    story = []
    try:
        from db.models import Portfolio, SessionLocal
        from services.portfolio_tracker import portfolio_summary

        db = SessionLocal()
        try:
            portfolios = db.query(Portfolio).filter(Portfolio.user_id == user_id).all()
        finally:
            db.close()

        if not portfolios:
            story.append(Paragraph("El usuario no tiene portfolios registrados.", body_style))
            return story

        for p in portfolios:
            try:
                summary = portfolio_summary(p.id)
                story.append(Paragraph(f"<b>{p.name}</b>", body_style))
                rows = [
                    ["Valor actual", f"€{summary['total_value']:,.2f}"],
                    ["Coste total", f"€{summary['total_cost']:,.2f}"],
                    ["P&L total", f"€{summary['total_pnl']:,.2f}"],
                    ["P&L %", f"{summary['total_pnl_pct']:+.2f}%" if summary['total_pnl_pct'] is not None else "—"],
                    ["Benchmark IBEX", f"{summary['benchmark_ibex_pct']:+.2f}%" if summary['benchmark_ibex_pct'] is not None else "—"],
                ]
                t = Table(rows, colWidths=[8 * cm, 8 * cm])
                t.setStyle(_default_table_style())
                story.append(t)
                story.append(Spacer(1, 0.2 * cm))
            except Exception as e:
                logger.warning(f"Error en resumen portfolio {p.id}: {e}")
                story.append(Paragraph(f"Error obteniendo resumen del portfolio '{p.name}'.", body_style))

    except EnvironmentError:
        story.append(Paragraph("Base de datos no disponible.", body_style))
    except Exception as e:
        logger.warning(f"Error sección portfolio: {e}")
        story.append(Paragraph("No se pudo obtener información del portfolio.", body_style))

    return story


def _user_backtests_section(user_id: int, body_style) -> list:
    story = []
    try:
        from db.models import BacktestResult, SessionLocal
        from datetime import timedelta

        db = SessionLocal()
        try:
            week_ago = datetime.now(timezone.utc) - timedelta(days=7)
            results = (
                db.query(BacktestResult)
                .filter(
                    BacktestResult.user_id == user_id,
                    BacktestResult.ran_at >= week_ago,
                )
                .order_by(BacktestResult.ran_at.desc())
                .all()
            )
        finally:
            db.close()

        if not results:
            story.append(Paragraph("No se ejecutaron backtests esta semana.", body_style))
            return story

        rows = [["Símbolo", "Días", "Operaciones", "Win Rate", "Retorno", "Max Drawdown", "Ejecutado"]]
        for r in results:
            rows.append([
                r.symbol,
                str(r.days_tested),
                str(r.total_trades),
                f"{r.win_rate:.1f}%" if r.win_rate is not None else "—",
                f"{r.total_return_pct:+.2f}%" if r.total_return_pct is not None else "—",
                f"{r.max_drawdown_pct:.2f}%" if r.max_drawdown_pct is not None else "—",
                r.ran_at.strftime("%d/%m/%Y %H:%M") if r.ran_at else "—",
            ])

        t = Table(rows, colWidths=[2.5*cm, 1.5*cm, 2.5*cm, 2*cm, 2.5*cm, 2.5*cm, 3*cm])
        t.setStyle(_default_table_style())
        story.append(t)

    except EnvironmentError:
        story.append(Paragraph("Base de datos no disponible.", body_style))
    except Exception as e:
        logger.warning(f"Error sección backtests: {e}")
        story.append(Paragraph("No se pudo obtener información de backtests.", body_style))

    return story


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _fmt(value, fmt: str) -> str:
    if value is None:
        return "—"
    try:
        return format(float(value), fmt)
    except (TypeError, ValueError):
        return "—"


def _fmt_pct(value) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return "—"


def _fmt_bn(value) -> str:
    if value is None:
        return "—"
    try:
        v = float(value)
        if v >= 1e9:
            return f"€{v/1e9:.1f}B"
        if v >= 1e6:
            return f"€{v/1e6:.1f}M"
        return f"€{v:,.0f}"
    except (TypeError, ValueError):
        return "—"


def _default_table_style() -> TableStyle:
    return TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e3a5f")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f7fa")]),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#d0d7de")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ])
