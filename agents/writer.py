import os
import json
import time
import shutil
import logging
from datetime import datetime

import pytz
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
import anthropic

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Table, TableStyle,
    Image, Spacer, PageBreak, HRFlowable,
)
from reportlab.platypus.flowables import KeepTogether

logger = logging.getLogger("bolsa.writer")

SKILLS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "skills")


def _strip_markdown_fence(text: str) -> str:
    """Extrae el contenido de un bloque ```json ... ``` o ``` ... ```."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]  # quitar línea de apertura (```json o ```)
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return text

COLORS = {
    "primary": "#003366",
    "secondary": "#0066CC",
    "accent": "#E8F4FD",
    "text": "#1A1A1A",
    "green": "#006600",
    "red": "#CC0000",
    "light_gray": "#F5F5F5",
    "border": "#CCCCCC",
    "white": "#FFFFFF",
}


def hex_to_rgb_tuple(hex_color: str):
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i+2], 16) / 255.0 for i in (0, 2, 4))


def hex_to_reportlab(hex_color: str):
    r, g, b = hex_to_rgb_tuple(hex_color)
    return colors.Color(r, g, b)


C_PRIMARY = hex_to_reportlab(COLORS["primary"])
C_SECONDARY = hex_to_reportlab(COLORS["secondary"])
C_ACCENT = hex_to_reportlab(COLORS["accent"])
C_LIGHT_GRAY = hex_to_reportlab(COLORS["light_gray"])
C_BORDER = hex_to_reportlab(COLORS["border"])
C_GREEN = hex_to_reportlab(COLORS["green"])
C_RED = hex_to_reportlab(COLORS["red"])
C_WHITE = colors.white
C_BLACK = colors.black


class WriterError(Exception):
    pass


class WriterAgent:
    def __init__(self, date: str, config: dict):
        self.date = date
        self.config = config
        self.client = anthropic.Anthropic(api_key=config["api_key"])
        self.model = config.get("model_writer", "claude-haiku-4-5-20251001")
        self.max_retries = int(config.get("max_retries", 3))
        self.retry_delay = int(config.get("retry_delay", 5))
        self.raw_dir = config.get("data_raw_dir", "data/raw")
        self.analysis_dir = config.get("data_analysis_dir", "data/analysis")
        self.output_dir = config.get("output_dir", "output")
        self.charts_dir = os.path.join(self.raw_dir, "charts_temp")
        self.madrid = pytz.timezone("Europe/Madrid")
        self.system_prompt = self._load_instructions()

    def _load_instructions(self) -> str:
        path = os.path.join(SKILLS_DIR, "writer_instructions.md")
        with open(path, encoding="utf-8") as f:
            return f.read()

    def run(self) -> dict:
        errors = []
        try:
            analysis, prices_df = self.load_analysis()
            text = self.generate_text(analysis)
            charts = self.generate_charts(prices_df, analysis)
            pdf_path = self.build_pdf(text, charts, analysis, prices_df)
            return {"pdf_file": pdf_path, "status": "ok", "errors": errors}
        except WriterError as e:
            errors.append(str(e))
            logger.error(f"Writer fallido: {e}")
            return {"pdf_file": None, "status": "error", "errors": errors}
        except Exception as e:
            errors.append(str(e))
            logger.error(f"Writer error inesperado: {e}")
            return {"pdf_file": None, "status": "error", "errors": errors}
        finally:
            if os.path.exists(self.charts_dir):
                shutil.rmtree(self.charts_dir, ignore_errors=True)

    def load_analysis(self):
        analysis_path = os.path.join(self.analysis_dir, f"ibex35_analysis_{self.date}.json")
        prices_path = os.path.join(self.raw_dir, f"ibex35_prices_{self.date}.csv")

        if not os.path.exists(analysis_path):
            raise WriterError(f"Análisis no encontrado: {analysis_path}")
        if not os.path.exists(prices_path):
            raise WriterError(f"Precios no encontrados: {prices_path}")

        with open(analysis_path, encoding="utf-8") as f:
            analysis = json.load(f)
        df = pd.read_csv(prices_path, encoding="utf-8")
        return analysis, df

    def generate_text(self, analysis: dict) -> dict:
        prompt = (
            f"Genera los textos narrativos para el informe del IBEX 35 del {self.date}.\n\n"
            f"=== ANÁLISIS DEL DÍA ===\n{json.dumps(analysis, ensure_ascii=False, indent=2)}\n\n"
            f"Responde ÚNICAMENTE con el JSON de textos. Sin texto adicional ni bloques markdown."
        )

        last_error = ""
        for attempt in range(self.max_retries):
            try:
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=4096,
                    system=self.system_prompt,
                    messages=[{"role": "user", "content": prompt}],
                )
                raw = response.content[0].text.strip()
                raw = _strip_markdown_fence(raw)
                return json.loads(raw)
            except json.JSONDecodeError as e:
                last_error = str(e)
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay)
            except anthropic.APIError as e:
                last_error = str(e)
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay)

        logger.warning("Writer: usando texto genérico de respaldo")
        return {
            "titulo_informe": f"IBEX 35 — Informe Diario — {self.date}",
            "titular_portada": f"IBEX 35 — Informe Diario — {self.date}",
            "titulares_candidatos": [],
            "resumen_ejecutivo": "Informe generado automáticamente. Consulte los datos adjuntos.",
            "narrativa_mercado": "Datos de mercado disponibles en la tabla adjunta.",
            "narrativa_sectores": "Análisis sectorial disponible en los gráficos adjuntos.",
            "narrativa_noticias": "Noticias del día disponibles en la sección correspondiente.",
            "conclusion": "Consulte los datos y gráficos para una visión completa del mercado.",
            "puntos_vigilancia": [],
            "calidad_datos": "limitados",
            "disclaimer": "Este informe ha sido generado de forma automatizada con fines meramente informativos y no constituye asesoramiento financiero ni recomendación de inversión.",
        }

    def generate_charts(self, prices_df: pd.DataFrame, analysis: dict) -> dict:
        os.makedirs(self.charts_dir, exist_ok=True)
        valid = prices_df[prices_df["error"].isna() | (prices_df["error"] == "")].copy()
        valid["change_pct"] = pd.to_numeric(valid["change_pct"], errors="coerce")
        valid["volume"] = pd.to_numeric(valid["volume"], errors="coerce")

        charts = {}
        charts["heatmap"] = self._chart_heatmap(valid)
        charts["top_movers"] = self._chart_top_movers(valid)
        charts["sector_bar"] = self._chart_sector_bar(analysis)
        charts["volume_bar"] = self._chart_volume_bar(valid)
        return charts

    def _chart_heatmap(self, df: pd.DataFrame) -> str:
        path = os.path.join(self.charts_dir, "heatmap.png")
        try:
            fig, ax = plt.subplots(figsize=(12, 8), facecolor="white")
            ax.set_facecolor("white")

            n_cols = 7
            n_rows = int(np.ceil(len(df) / n_cols))
            df_sorted = df.sort_values("change_pct", ascending=False).reset_index(drop=True)

            for idx, row in df_sorted.iterrows():
                r = idx // n_cols
                c = idx % n_cols
                chg = row["change_pct"] if pd.notna(row["change_pct"]) else 0
                intensity = min(abs(chg) / 3.0, 1.0)
                if chg > 0:
                    color = (1 - intensity * 0.7, 1.0, 1 - intensity * 0.7)
                elif chg < 0:
                    color = (1.0, 1 - intensity * 0.7, 1 - intensity * 0.7)
                else:
                    color = (0.9, 0.9, 0.9)

                rect = mpatches.FancyBboxPatch(
                    (c + 0.05, n_rows - r - 0.95), 0.88, 0.88,
                    boxstyle="round,pad=0.02", facecolor=color, edgecolor="#cccccc", linewidth=0.5
                )
                ax.add_patch(rect)
                ticker_short = row["ticker"].replace(".MC", "")
                chg_str = f"{chg:+.2f}%" if pd.notna(row["change_pct"]) else "N/D"
                ax.text(c + 0.49, n_rows - r - 0.45, ticker_short,
                        ha="center", va="center", fontsize=7, fontweight="bold", color="#1a1a1a")
                ax.text(c + 0.49, n_rows - r - 0.68, chg_str,
                        ha="center", va="center", fontsize=6.5,
                        color=COLORS["green"] if chg > 0 else (COLORS["red"] if chg < 0 else "#555"))

            ax.set_xlim(0, n_cols)
            ax.set_ylim(0, n_rows)
            ax.axis("off")
            ax.set_title(f"Mapa de calor IBEX 35 — {self.date}", fontsize=13, fontweight="bold",
                         color=COLORS["primary"], pad=10)
            plt.tight_layout()
            fig.savefig(path, dpi=150, bbox_inches="tight", facecolor="white")
            plt.close(fig)
        except Exception as e:
            logger.warning(f"Error generando heatmap: {e}")
            path = None
        return path

    def _chart_top_movers(self, df: pd.DataFrame) -> str:
        path = os.path.join(self.charts_dir, "top_movers.png")
        try:
            valid = df.dropna(subset=["change_pct"]).copy()
            top5 = valid.nlargest(5, "change_pct")
            bot5 = valid.nsmallest(5, "change_pct")
            combined = pd.concat([top5, bot5]).drop_duplicates()
            combined = combined.sort_values("change_pct")

            labels = [r.replace(".MC", "") for r in combined["ticker"]]
            values = combined["change_pct"].tolist()
            bar_colors = [COLORS["green"] if v > 0 else COLORS["red"] for v in values]

            fig, ax = plt.subplots(figsize=(10, 6), facecolor="white")
            ax.set_facecolor("white")
            bars = ax.barh(labels, values, color=bar_colors, edgecolor="white", height=0.6)
            ax.axvline(0, color="#555", linewidth=0.8)
            for bar, val in zip(bars, values):
                ax.text(val + (0.05 if val >= 0 else -0.05), bar.get_y() + bar.get_height() / 2,
                        f"{val:+.2f}%", va="center",
                        ha="left" if val >= 0 else "right", fontsize=8, color="#1a1a1a")
            ax.set_xlabel("Variación (%)", fontsize=9)
            ax.set_title(f"Mejores y peores valores — {self.date}", fontsize=12,
                         fontweight="bold", color=COLORS["primary"])
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)
            plt.tight_layout()
            fig.savefig(path, dpi=150, bbox_inches="tight", facecolor="white")
            plt.close(fig)
        except Exception as e:
            logger.warning(f"Error generando top_movers: {e}")
            path = None
        return path

    def _chart_sector_bar(self, analysis: dict) -> str:
        path = os.path.join(self.charts_dir, "sector_bar.png")
        try:
            sectors = analysis.get("sector_analysis", [])
            if not sectors:
                return None
            names = [s["sector"] for s in sectors]
            values = [s.get("avg_change_pct", 0) or 0 for s in sectors]
            bar_colors = [COLORS["green"] if v > 0 else COLORS["red"] for v in values]
            sorted_pairs = sorted(zip(values, names), reverse=True)
            values, names = zip(*sorted_pairs)

            fig, ax = plt.subplots(figsize=(10, 5), facecolor="white")
            ax.set_facecolor("white")
            bars = ax.bar(names, values, color=bar_colors, edgecolor="white", width=0.55)
            ax.axhline(0, color="#555", linewidth=0.8)
            for bar, val in zip(bars, values):
                ax.text(bar.get_x() + bar.get_width() / 2,
                        val + (0.03 if val >= 0 else -0.08),
                        f"{val:+.2f}%", ha="center", va="bottom" if val >= 0 else "top",
                        fontsize=8, color="#1a1a1a")
            ax.set_ylabel("Variación media (%)", fontsize=9)
            ax.set_title(f"Variación por sector — {self.date}", fontsize=12,
                         fontweight="bold", color=COLORS["primary"])
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)
            plt.xticks(rotation=20, ha="right", fontsize=9)
            plt.tight_layout()
            fig.savefig(path, dpi=150, bbox_inches="tight", facecolor="white")
            plt.close(fig)
        except Exception as e:
            logger.warning(f"Error generando sector_bar: {e}")
            path = None
        return path

    def _chart_volume_bar(self, df: pd.DataFrame) -> str:
        path = os.path.join(self.charts_dir, "volume_bar.png")
        try:
            valid = df.dropna(subset=["volume"]).copy()
            top10 = valid.nlargest(10, "volume")
            labels = [r.replace(".MC", "") for r in top10["ticker"]]
            vols = (top10["volume"] / 1_000_000).tolist()

            fig, ax = plt.subplots(figsize=(10, 5), facecolor="white")
            ax.set_facecolor("white")
            ax.bar(labels, vols, color=COLORS["secondary"], edgecolor="white", width=0.6)
            ax.set_ylabel("Volumen (millones)", fontsize=9)
            ax.set_title(f"Top 10 valores por volumen — {self.date}", fontsize=12,
                         fontweight="bold", color=COLORS["primary"])
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)
            plt.tight_layout()
            fig.savefig(path, dpi=150, bbox_inches="tight", facecolor="white")
            plt.close(fig)
        except Exception as e:
            logger.warning(f"Error generando volume_bar: {e}")
            path = None
        return path

    def build_pdf(self, text: dict, charts: dict, analysis: dict, prices_df: pd.DataFrame) -> str:
        os.makedirs(self.output_dir, exist_ok=True)
        out_path = os.path.join(self.output_dir, f"informe_{self.date}.pdf")

        styles = getSampleStyleSheet()
        style_normal = ParagraphStyle("normal_es", parent=styles["Normal"],
                                      fontName="Helvetica", fontSize=9,
                                      leading=14, textColor=colors.HexColor(COLORS["text"]))
        style_h1 = ParagraphStyle("h1_es", parent=styles["Heading1"],
                                   fontName="Helvetica-Bold", fontSize=18,
                                   textColor=colors.HexColor(COLORS["primary"]), spaceAfter=6)
        style_h2 = ParagraphStyle("h2_es", parent=styles["Heading2"],
                                   fontName="Helvetica-Bold", fontSize=13,
                                   textColor=colors.HexColor(COLORS["primary"]), spaceAfter=4)
        style_caption = ParagraphStyle("caption", parent=styles["Normal"],
                                        fontName="Helvetica-Oblique", fontSize=7.5,
                                        textColor=colors.HexColor("#555555"), spaceAfter=4)
        style_small = ParagraphStyle("small", parent=styles["Normal"],
                                      fontName="Helvetica", fontSize=7.5,
                                      leading=11, textColor=colors.HexColor("#333333"))

        timestamp = datetime.now(self.madrid).strftime("%d/%m/%Y %H:%M")
        date_parts = self.date.split("-")
        meses = ["enero","febrero","marzo","abril","mayo","junio",
                 "julio","agosto","septiembre","octubre","noviembre","diciembre"]
        date_es = f"{int(date_parts[2])} de {meses[int(date_parts[1])-1]} de {date_parts[0]}"

        story = []

        # ── Página 1: Portada ──────────────────────────────────────────────
        story.append(Spacer(1, 2*cm))
        # Usar titular_portada si existe; si no, titulo_informe genérico
        portada_title = (
            text.get("titular_portada")
            or text.get("titulo_informe")
            or f"IBEX 35 — Informe Diario — {date_es}"
        )
        story.append(Paragraph(portada_title, style_h1))
        story.append(Spacer(1, 0.3*cm))
        story.append(HRFlowable(width="100%", thickness=2, color=C_PRIMARY))
        story.append(Spacer(1, 0.5*cm))

        ms = analysis.get("market_summary", {})
        chg = ms.get("ibex35_change_pct", 0) or 0
        chg_color = COLORS["green"] if chg >= 0 else COLORS["red"]
        chg_sign = "+" if chg >= 0 else ""
        kpi_data = [
            ["Variación del índice", "Sentimiento", "Volatilidad", "Volumen"],
            [
                Paragraph(f'<font color="{chg_color}"><b>{chg_sign}{chg:.2f}%</b></font>', style_normal),
                Paragraph(f"<b>{ms.get('market_sentiment','—').capitalize()}</b>", style_normal),
                Paragraph(f"<b>{ms.get('volatility_level','—').capitalize()}</b>", style_normal),
                Paragraph(f"<b>{ms.get('volume_vs_average','—').capitalize()}</b>", style_normal),
            ],
        ]
        kpi_table = Table(kpi_data, colWidths=[4*cm]*4)
        kpi_table.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), C_PRIMARY),
            ("TEXTCOLOR", (0,0), (-1,0), C_WHITE),
            ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
            ("FONTSIZE", (0,0), (-1,0), 9),
            ("ALIGN", (0,0), (-1,-1), "CENTER"),
            ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
            ("ROWBACKGROUNDS", (0,1), (-1,-1), [C_ACCENT, C_WHITE]),
            ("GRID", (0,0), (-1,-1), 0.3, C_BORDER),
            ("TOPPADDING", (0,0), (-1,-1), 6),
            ("BOTTOMPADDING", (0,0), (-1,-1), 6),
        ]))
        story.append(kpi_table)
        story.append(Spacer(1, 0.5*cm))

        resumen_text = text.get("resumen_ejecutivo", "")
        resumen_box_data = [[Paragraph(resumen_text, style_normal)]]
        resumen_box = Table(resumen_box_data, colWidths=[16*cm])
        resumen_box.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,-1), C_ACCENT),
            ("BOX", (0,0), (-1,-1), 0.5, C_BORDER),
            ("TOPPADDING", (0,0), (-1,-1), 10),
            ("BOTTOMPADDING", (0,0), (-1,-1), 10),
            ("LEFTPADDING", (0,0), (-1,-1), 12),
            ("RIGHTPADDING", (0,0), (-1,-1), 12),
        ]))
        story.append(resumen_box)

        highlights = analysis.get("report_highlights", [])
        if highlights:
            story.append(Spacer(1, 0.4*cm))
            story.append(Paragraph("<b>Puntos clave del día</b>", style_h2))
            for h in highlights:
                story.append(Paragraph(f"• {h}", style_normal))

        story.append(PageBreak())

        # ── Página 2: Panorama + Heatmap ──────────────────────────────────
        story.append(Paragraph("Panorama del mercado", style_h1))
        story.append(HRFlowable(width="100%", thickness=1, color=C_PRIMARY))
        story.append(Spacer(1, 0.3*cm))
        story.append(Paragraph(text.get("narrativa_mercado", ""), style_normal))
        story.append(Spacer(1, 0.4*cm))

        if charts.get("heatmap") and os.path.exists(charts["heatmap"]):
            img = Image(charts["heatmap"], width=16*cm, height=9*cm)
            story.append(img)
            story.append(Paragraph("Mapa de calor de variaciones del IBEX 35", style_caption))

        story.append(PageBreak())

        # ── Página 3: Tabla completa IBEX 35 ──────────────────────────────
        story.append(Paragraph("Datos completos IBEX 35", style_h1))
        story.append(HRFlowable(width="100%", thickness=1, color=C_PRIMARY))
        story.append(Spacer(1, 0.3*cm))

        valid_df = prices_df[prices_df["error"].isna() | (prices_df["error"] == "")].copy()
        valid_df = valid_df.sort_values("change_pct", ascending=False)

        table_header = ["Empresa", "Ticker", "Apertura", "Máximo", "Mínimo", "Cierre", "Volumen (M)", "Var%"]
        table_data = [table_header]
        for _, row in valid_df.iterrows():
            chg_val = row.get("change_pct", 0)
            chg_val = chg_val if pd.notna(chg_val) else 0
            chg_str = f"{chg_val:+.2f}%"
            chg_color_rl = C_GREEN if chg_val >= 0 else C_RED
            vol = row.get("volume", 0)
            vol = vol if pd.notna(vol) else 0
            table_data.append([
                Paragraph(str(row.get("name", ""))[:20], style_small),
                Paragraph(str(row.get("ticker", "")).replace(".MC",""), style_small),
                f'{row.get("open", 0) or 0:.2f}',
                f'{row.get("high", 0) or 0:.2f}',
                f'{row.get("low", 0) or 0:.2f}',
                f'{row.get("close", 0) or 0:.2f}',
                f'{vol/1_000_000:.1f}',
                Paragraph(f'<font color="{COLORS["green"] if chg_val >= 0 else COLORS["red"]}"><b>{chg_str}</b></font>', style_small),
            ])

        col_widths = [4.5*cm, 1.8*cm, 1.8*cm, 1.8*cm, 1.8*cm, 1.8*cm, 1.8*cm, 1.5*cm]
        tbl = Table(table_data, colWidths=col_widths, repeatRows=1)
        row_bg = []
        for i in range(1, len(table_data)):
            bg = C_LIGHT_GRAY if i % 2 == 0 else C_WHITE
            row_bg.append(("BACKGROUND", (0, i), (-1, i), bg))

        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), C_PRIMARY),
            ("TEXTCOLOR", (0, 0), (-1, 0), C_WHITE),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 8),
            ("FONTSIZE", (0, 1), (-1, -1), 7.5),
            ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
            ("ALIGN", (0, 0), (1, -1), "LEFT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.25, C_BORDER),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ] + row_bg))
        story.append(tbl)
        story.append(PageBreak())

        # ── Página 4: Top movers ───────────────────────────────────────────
        story.append(Paragraph("Mejores y peores valores", style_h1))
        story.append(HRFlowable(width="100%", thickness=1, color=C_PRIMARY))
        story.append(Spacer(1, 0.3*cm))

        if charts.get("top_movers") and os.path.exists(charts["top_movers"]):
            story.append(Image(charts["top_movers"], width=15*cm, height=9*cm))
            story.append(Paragraph("Variación porcentual de los mejores y peores valores del día", style_caption))

        story.append(Spacer(1, 0.3*cm))
        gainers = analysis.get("top_gainers", [])
        losers = analysis.get("top_losers", [])
        if gainers or losers:
            gl_header = ["Mayores subidas", "Var%", "", "Mayores bajadas", "Var%"]
            gl_data = [gl_header]
            for i in range(max(len(gainers), len(losers))):
                g = gainers[i] if i < len(gainers) else {}
                l = losers[i] if i < len(losers) else {}
                g_chg = g.get("change_pct", 0) or 0
                l_chg = l.get("change_pct", 0) or 0
                gl_data.append([
                    g.get("name", ""),
                    Paragraph(f'<font color="{COLORS["green"]}"><b>+{g_chg:.2f}%</b></font>', style_small) if g else "",
                    "",
                    l.get("name", ""),
                    Paragraph(f'<font color="{COLORS["red"]}"><b>{l_chg:.2f}%</b></font>', style_small) if l else "",
                ])
            gl_tbl = Table(gl_data, colWidths=[5*cm, 2.5*cm, 0.5*cm, 5*cm, 2.5*cm])
            gl_tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (1, 0), hex_to_reportlab("#E8F5E9")),
                ("BACKGROUND", (3, 0), (4, 0), hex_to_reportlab("#FFEBEE")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (1, -1), 0.25, C_BORDER),
                ("GRID", (3, 0), (4, -1), 0.25, C_BORDER),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]))
            story.append(gl_tbl)

        story.append(PageBreak())

        # ── Página 5: Sectores ─────────────────────────────────────────────
        story.append(Paragraph("Análisis sectorial", style_h1))
        story.append(HRFlowable(width="100%", thickness=1, color=C_PRIMARY))
        story.append(Spacer(1, 0.3*cm))

        if charts.get("sector_bar") and os.path.exists(charts["sector_bar"]):
            story.append(Image(charts["sector_bar"], width=15*cm, height=8*cm))
            story.append(Paragraph("Variación media por sector del IBEX 35", style_caption))

        story.append(Spacer(1, 0.3*cm))
        story.append(Paragraph(text.get("narrativa_sectores", ""), style_normal))
        story.append(PageBreak())

        # ── Página 6: Noticias ─────────────────────────────────────────────
        story.append(Paragraph("Noticias relevantes", style_h1))
        story.append(HRFlowable(width="100%", thickness=1, color=C_PRIMARY))
        story.append(Spacer(1, 0.3*cm))
        story.append(Paragraph(text.get("narrativa_noticias", ""), style_normal))
        story.append(Spacer(1, 0.4*cm))

        key_news = analysis.get("key_news_impact", [])
        for item in key_news[:8]:
            impact = item.get("impact", "neutral")
            impact_color = COLORS["green"] if impact == "positivo" else (COLORS["red"] if impact == "negativo" else "#555555")
            story.append(Paragraph(
                f'<b>{item.get("news_title", "")}</b> — <font color="{impact_color}">{impact.upper()}</font>',
                style_normal
            ))
            story.append(Paragraph(item.get("analysis", ""), style_small))
            story.append(Spacer(1, 0.2*cm))

        story.append(PageBreak())

        # ── Página 7: Volumen + Señales técnicas ──────────────────────────
        story.append(Paragraph("Volumen y señales técnicas", style_h1))
        story.append(HRFlowable(width="100%", thickness=1, color=C_PRIMARY))
        story.append(Spacer(1, 0.3*cm))

        if charts.get("volume_bar") and os.path.exists(charts["volume_bar"]):
            story.append(Image(charts["volume_bar"], width=15*cm, height=7*cm))
            story.append(Paragraph("Top 10 valores por volumen negociado", style_caption))

        signals = analysis.get("technical_signals", [])
        if signals:
            story.append(Spacer(1, 0.3*cm))
            story.append(Paragraph("<b>Señales técnicas destacadas</b>", style_h2))
            sig_data = [["Ticker", "Señal", "RSI aprox.", "Comentario"]]
            for s in signals:
                sig_data.append([
                    s.get("ticker", "").replace(".MC", ""),
                    s.get("signal", ""),
                    str(s.get("rsi_approx", "—") or "—"),
                    Paragraph(s.get("comment", ""), style_small),
                ])
            sig_tbl = Table(sig_data, colWidths=[2*cm, 3*cm, 2.5*cm, 8.5*cm])
            sig_tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), C_PRIMARY),
                ("TEXTCOLOR", (0, 0), (-1, 0), C_WHITE),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.25, C_BORDER),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]))
            story.append(sig_tbl)

        story.append(PageBreak())

        # ── Página 8: Conclusión ───────────────────────────────────────────
        story.append(Paragraph("Conclusión", style_h1))
        story.append(HRFlowable(width="100%", thickness=1, color=C_PRIMARY))
        story.append(Spacer(1, 0.3*cm))
        story.append(Paragraph(text.get("conclusion", ""), style_normal))
        story.append(Spacer(1, 1*cm))
        story.append(HRFlowable(width="100%", thickness=0.5, color=C_BORDER))
        story.append(Spacer(1, 0.3*cm))
        story.append(Paragraph(text.get("disclaimer", ""), style_small))
        story.append(Spacer(1, 0.2*cm))
        story.append(Paragraph(f"Informe generado el {timestamp} | Sistema automatizado IBEX 35", style_small))

        # ── Construcción del documento ─────────────────────────────────────
        def on_page(canvas, doc):
            if doc.page == 1:
                return
            canvas.saveState()
            canvas.setFillColor(C_PRIMARY)
            canvas.rect(1.5*cm, A4[1] - 1.2*cm, A4[0] - 3*cm, 0.6*cm, fill=1, stroke=0)
            canvas.setFillColor(C_WHITE)
            canvas.setFont("Helvetica-Bold", 8)
            canvas.drawString(1.8*cm, A4[1] - 0.85*cm, f"IBEX 35 — Informe Diario — {date_es}")
            canvas.drawRightString(A4[0] - 1.8*cm, A4[1] - 0.85*cm, f"Página {doc.page}")
            canvas.setFillColor(C_BORDER)
            canvas.rect(1.5*cm, 1.0*cm, A4[0] - 3*cm, 0.3*cm, fill=1, stroke=0)
            canvas.setFillColor(colors.HexColor("#555555"))
            canvas.setFont("Helvetica", 7)
            canvas.drawString(1.8*cm, 1.1*cm, f"Generado: {timestamp}")
            canvas.drawRightString(A4[0] - 1.8*cm, 1.1*cm, "Solo con fines informativos — No es asesoramiento financiero")
            canvas.restoreState()

        doc = SimpleDocTemplate(
            out_path,
            pagesize=A4,
            leftMargin=2*cm, rightMargin=2*cm,
            topMargin=1.8*cm, bottomMargin=1.8*cm,
        )
        doc.build(story, onFirstPage=on_page, onLaterPages=on_page)

        size_kb = os.path.getsize(out_path) / 1024
        logger.info(f"PDF generado: {out_path} ({size_kb:.0f} KB)")
        if size_kb < 50:
            raise WriterError(f"PDF demasiado pequeño ({size_kb:.0f} KB), posible error de generación")
        return out_path
