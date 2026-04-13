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
    "primary": "#003366",      # azul marino — banners de sección
    "secondary": "#1A5C9A",    # azul medio — sub-secciones
    "accent": "#C5DCF5",       # azul claro saturado — fondos de cajas
    "accent_alt": "#E5EFF9",   # azul muy claro — filas alternas
    "text": "#1A1A1A",
    "green": "#006600",
    "red": "#CC0000",
    "light_gray": "#EAF0F7",   # filas alternas de tabla con tono azulado
    "border": "#7AADD4",       # borde más visible y con color
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
C_ACCENT_ALT = hex_to_reportlab(COLORS["accent_alt"])
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
            "heatmap": {
                "descripcion": "Representación visual del IBEX 35 donde cada bloque corresponde a una empresa, su tamaño es proporcional a la capitalización bursátil y su color refleja la variación diaria.",
                "leyenda": "Verde intenso: subida >+3% | Verde suave: subida leve | Gris: sin cambios | Rojo suave: caída leve | Rojo intenso: caída <-3%",
                "insight_clave": "Consulte el mapa de calor adjunto para identificar los sectores con mayor impacto en la sesión.",
            },
            "narrativa_heatmap": "El mapa de calor ofrece una representación visual del comportamiento sectorial del IBEX 35, ponderada por capitalización bursátil. Consulte los gráficos adjuntos para un análisis detallado.",
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
        """Treemap agrupado por sector, tamaño proporcional a capitalización bursátil."""
        path = os.path.join(self.charts_dir, "heatmap.png")
        try:
            import squarify

            df = df.copy()
            df["change_pct"] = pd.to_numeric(df["change_pct"], errors="coerce").fillna(0)
            df["market_cap"] = pd.to_numeric(df.get("market_cap", pd.Series(dtype=float)), errors="coerce")

            # Usar market_cap real; si no hay datos, asignar peso uniforme
            if df["market_cap"].isna().all():
                df["market_cap"] = 1.0
            else:
                median_cap = df["market_cap"].median()
                df["market_cap"] = df["market_cap"].fillna(median_cap)
            df["market_cap"] = df["market_cap"].clip(lower=1e6)

            # Agrupar por sector
            if "sector" not in df.columns or df["sector"].isna().all():
                df["sector"] = "IBEX 35"
            df["sector"] = df["sector"].fillna("Otros")

            sector_caps = df.groupby("sector")["market_cap"].sum().sort_values(ascending=False)
            total_cap = sector_caps.sum()

            fig, ax = plt.subplots(figsize=(14, 9), facecolor="white")
            ax.set_facecolor("white")
            ax.axis("off")

            # Layout de sectores usando squarify sobre el canvas completo
            sector_sizes = sector_caps.values.tolist()
            sector_names = sector_caps.index.tolist()
            sector_rects = squarify.squarify(
                squarify.normalize_sizes(sector_sizes, 100, 100), 0, 0, 100, 100
            )

            PADDING = 0.8  # margen interior del sector para etiqueta

            for s_rect, s_name in zip(sector_rects, sector_names):
                sx, sy, sw, sh = s_rect["x"], s_rect["y"], s_rect["dx"], s_rect["dy"]

                # Dibujar borde de sector
                sector_border = mpatches.FancyBboxPatch(
                    (sx, sy), sw, sh,
                    boxstyle="square,pad=0",
                    facecolor="none", edgecolor="#ffffff", linewidth=2.5, zorder=3
                )
                ax.add_patch(sector_border)

                # Tickers dentro del sector
                tickers_in = df[df["sector"] == s_name].copy()
                tickers_in = tickers_in.sort_values("market_cap", ascending=False)
                t_sizes = tickers_in["market_cap"].tolist()

                if not t_sizes:
                    continue

                t_rects = squarify.squarify(
                    squarify.normalize_sizes(t_sizes, sw - PADDING * 2, sh - PADDING * 2),
                    sx + PADDING, sy + PADDING,
                    sw - PADDING * 2, sh - PADDING * 2
                )

                for t_rect, (_, t_row) in zip(t_rects, tickers_in.iterrows()):
                    tx, ty, tw, th = t_rect["x"], t_rect["y"], t_rect["dx"], t_rect["dy"]
                    chg = t_row["change_pct"]

                    # Color por cambio porcentual (escala saturada en ±3%)
                    intensity = min(abs(chg) / 3.0, 1.0)
                    if chg > 0.1:
                        r_c = 1 - intensity * 0.75
                        g_c = 0.35 + intensity * 0.45
                        b_c = 1 - intensity * 0.75
                        face = (r_c, g_c, b_c)
                    elif chg < -0.1:
                        r_c = 0.35 + intensity * 0.55
                        g_c = 1 - intensity * 0.75
                        b_c = 1 - intensity * 0.75
                        face = (r_c, g_c, b_c)
                    else:
                        face = (0.82, 0.82, 0.82)

                    rect = mpatches.FancyBboxPatch(
                        (tx, ty), tw, th,
                        boxstyle="square,pad=0",
                        facecolor=face, edgecolor="#ffffff", linewidth=0.6, zorder=2
                    )
                    ax.add_patch(rect)

                    # Etiqueta solo si el bloque es suficientemente grande
                    if tw > 2.5 and th > 1.8:
                        ticker_short = str(t_row.get("ticker", "")).replace(".MC", "")
                        chg_str = f"{chg:+.1f}%"
                        font_size = max(5.5, min(9, tw * 0.85))
                        text_color = "#ffffff" if intensity > 0.4 else "#1a1a1a"
                        ax.text(tx + tw / 2, ty + th * 0.58, ticker_short,
                                ha="center", va="center", fontsize=font_size,
                                fontweight="bold", color=text_color, zorder=4, clip_on=True)
                        ax.text(tx + tw / 2, ty + th * 0.3, chg_str,
                                ha="center", va="center", fontsize=font_size * 0.85,
                                color=text_color, zorder=4, clip_on=True)

                # Etiqueta del sector (sobre el bloque, arriba)
                if sw > 5 and sh > 3:
                    sector_pct = sector_caps[s_name] / total_cap * 100
                    ax.text(sx + sw / 2, sy + sh - PADDING * 0.4,
                            f"{s_name}  ({sector_pct:.0f}%)",
                            ha="center", va="top", fontsize=7.5, fontweight="bold",
                            color="#ffffff", zorder=5,
                            bbox=dict(boxstyle="round,pad=0.15", facecolor="#00000066", edgecolor="none"))

            ax.set_xlim(0, 100)
            ax.set_ylim(0, 100)
            ax.set_title(f"Treemap IBEX 35 por capitalización bursátil — {self.date}",
                         fontsize=13, fontweight="bold", color=COLORS["primary"], pad=10)

            # Leyenda de colores
            legend_elements = [
                mpatches.Patch(facecolor=(0.25, 0.70, 0.25), label="Subida fuerte (>+3%)"),
                mpatches.Patch(facecolor=(0.70, 0.90, 0.70), label="Subida leve"),
                mpatches.Patch(facecolor=(0.82, 0.82, 0.82), label="Sin cambios"),
                mpatches.Patch(facecolor=(0.90, 0.70, 0.70), label="Caída leve"),
                mpatches.Patch(facecolor=(0.90, 0.25, 0.25), label="Caída fuerte (<-3%)"),
            ]
            ax.legend(handles=legend_elements, loc="lower right", fontsize=7,
                      framealpha=0.85, edgecolor="#cccccc")

            plt.tight_layout()
            fig.savefig(path, dpi=150, bbox_inches="tight", facecolor="white")
            plt.close(fig)
        except Exception as e:
            logger.warning(f"Error generando treemap: {e}")
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

        # ── Estilos ────────────────────────────────────────────────────────
        styles = getSampleStyleSheet()
        style_normal = ParagraphStyle("normal_es", parent=styles["Normal"],
                                      fontName="Helvetica", fontSize=9,
                                      leading=13, spaceAfter=0, spaceBefore=0,
                                      textColor=colors.HexColor(COLORS["text"]))
        style_banner_h1 = ParagraphStyle("banner_h1", fontName="Helvetica-Bold", fontSize=14,
                                          textColor=C_WHITE, leading=18,
                                          spaceAfter=0, spaceBefore=0)
        style_banner_h2 = ParagraphStyle("banner_h2", fontName="Helvetica-Bold", fontSize=11,
                                          textColor=C_WHITE, leading=15,
                                          spaceAfter=0, spaceBefore=0)
        style_caption = ParagraphStyle("caption", parent=styles["Normal"],
                                        fontName="Helvetica-Oblique", fontSize=7,
                                        textColor=colors.HexColor("#444444"),
                                        spaceAfter=2, spaceBefore=0)
        style_small = ParagraphStyle("small", parent=styles["Normal"],
                                      fontName="Helvetica", fontSize=7.5,
                                      leading=10, spaceAfter=0, spaceBefore=0,
                                      textColor=colors.HexColor("#222222"))
        style_portada_title = ParagraphStyle("portada_title", fontName="Helvetica-Bold",
                                              fontSize=20, textColor=C_WHITE,
                                              leading=24, spaceAfter=0, spaceBefore=0)

        # ── Helpers ────────────────────────────────────────────────────────
        PAGE_W = 17 * cm  # ancho útil (A4 - márgenes 2cm*2)

        def banner_h1(title: str):
            """Barra de sección principal — fondo azul marino."""
            data = [[Paragraph(title, style_banner_h1)]]
            tbl = Table(data, colWidths=[PAGE_W])
            tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), C_PRIMARY),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ]))
            return tbl

        def banner_h2(title: str):
            """Barra de sub-sección — fondo azul medio."""
            data = [[Paragraph(title, style_banner_h2)]]
            tbl = Table(data, colWidths=[PAGE_W])
            tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), C_SECONDARY),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ]))
            return tbl

        SP = Spacer(1, 0.15 * cm)   # espaciador compacto estándar entre elementos

        # ── Fechas ────────────────────────────────────────────────────────
        date_parts = self.date.split("-")
        meses = ["enero","febrero","marzo","abril","mayo","junio",
                 "julio","agosto","septiembre","octubre","noviembre","diciembre"]
        date_es = f"{int(date_parts[2])} de {meses[int(date_parts[1])-1]} de {date_parts[0]}"
        date_footer = datetime.now(self.madrid).strftime("%d/%m/%Y")

        story = []

        # ══════════════════════════════════════════════════════════════════
        # Página 1: Portada
        # ══════════════════════════════════════════════════════════════════
        portada_title = (
            text.get("titular_portada")
            or text.get("titulo_informe")
            or f"IBEX 35 — Informe Diario — {date_es}"
        )
        # Banner de portada: fondo primario ancho con título grande
        portada_data = [[Paragraph(portada_title, style_portada_title)]]
        portada_tbl = Table(portada_data, colWidths=[PAGE_W])
        portada_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), C_PRIMARY),
            ("TOPPADDING", (0, 0), (-1, -1), 14),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 14),
            ("LEFTPADDING", (0, 0), (-1, -1), 14),
            ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ]))
        story.append(portada_tbl)
        story.append(SP)

        ms = analysis.get("market_summary", {})
        chg = ms.get("ibex35_change_pct", 0) or 0
        chg_color = COLORS["green"] if chg >= 0 else COLORS["red"]
        chg_sign = "+" if chg >= 0 else ""

        style_kpi_val = ParagraphStyle("kpi_val", fontName="Helvetica-Bold", fontSize=10,
                                        textColor=colors.HexColor(COLORS["text"]),
                                        alignment=1, spaceAfter=0, spaceBefore=0)
        kpi_data = [
            [
                Paragraph("<b>Variación</b>", style_small),
                Paragraph("<b>Sentimiento</b>", style_small),
                Paragraph("<b>Volatilidad</b>", style_small),
                Paragraph("<b>Volumen</b>", style_small),
            ],
            [
                Paragraph(f'<font color="{chg_color}"><b>{chg_sign}{chg:.2f}%</b></font>', style_kpi_val),
                Paragraph(f"<b>{ms.get('market_sentiment','—').capitalize()}</b>", style_kpi_val),
                Paragraph(f"<b>{ms.get('volatility_level','—').capitalize()}</b>", style_kpi_val),
                Paragraph(f"<b>{ms.get('volume_vs_average','—').capitalize()}</b>", style_kpi_val),
            ],
        ]
        kpi_table = Table(kpi_data, colWidths=[PAGE_W / 4] * 4)
        kpi_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), C_ACCENT),
            ("BACKGROUND", (0, 1), (-1, 1), C_ACCENT_ALT),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.5, C_BORDER),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        story.append(kpi_table)
        story.append(SP)

        resumen_text = text.get("resumen_ejecutivo", "")
        resumen_box_data = [[Paragraph(resumen_text, style_normal)]]
        resumen_box = Table(resumen_box_data, colWidths=[PAGE_W])
        resumen_box.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), C_ACCENT),
            ("BOX", (0, 0), (-1, -1), 1.0, C_PRIMARY),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ]))
        story.append(resumen_box)

        highlights = analysis.get("report_highlights", [])
        if highlights:
            story.append(SP)
            story.append(banner_h2("Puntos clave del día"))
            story.append(SP)
            hl_data = [[Paragraph(f"• {h}", style_normal)] for h in highlights]
            hl_tbl = Table(hl_data, colWidths=[PAGE_W])
            hl_tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), C_ACCENT_ALT),
                ("BOX", (0, 0), (-1, -1), 0.5, C_BORDER),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("LINEBELOW", (0, 0), (-1, -2), 0.3, C_BORDER),
            ]))
            story.append(hl_tbl)

        story.append(PageBreak())

        # ══════════════════════════════════════════════════════════════════
        # Panorama del mercado + Heatmap
        # ══════════════════════════════════════════════════════════════════
        story.append(banner_h1("Panorama del mercado"))
        story.append(SP)
        story.append(Paragraph(text.get("narrativa_mercado", ""), style_normal))
        story.append(SP)

        story.append(banner_h2("Mapa de calor del IBEX 35"))
        story.append(SP)

        heatmap_meta = text.get("heatmap", {})
        if heatmap_meta.get("descripcion"):
            desc_box_data = [[Paragraph(heatmap_meta["descripcion"], style_small)]]
            desc_box = Table(desc_box_data, colWidths=[PAGE_W])
            desc_box.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), C_ACCENT),
                ("BOX", (0, 0), (-1, -1), 0.5, C_BORDER),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ]))
            story.append(desc_box)
            story.append(SP)

        if charts.get("heatmap") and os.path.exists(charts["heatmap"]):
            story.append(Image(charts["heatmap"], width=PAGE_W, height=9*cm))
            leyenda_text = heatmap_meta.get(
                "leyenda",
                "Verde intenso: >+3% | Verde suave: subida leve | Gris: sin cambios | Rojo suave: caída leve | Rojo intenso: <-3% — Tamaño proporcional a capitalización bursátil"
            )
            story.append(Paragraph(leyenda_text, style_caption))

        if heatmap_meta.get("insight_clave"):
            story.append(SP)
            insight_data = [[
                Paragraph("<b>Lectura clave</b>", style_small),
                Paragraph(heatmap_meta["insight_clave"], style_small),
            ]]
            insight_tbl = Table(insight_data, colWidths=[3*cm, PAGE_W - 3*cm])
            insight_tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (0, 0), C_PRIMARY),
                ("TEXTCOLOR", (0, 0), (0, 0), C_WHITE),
                ("BACKGROUND", (1, 0), (1, 0), C_ACCENT_ALT),
                ("BOX", (0, 0), (-1, -1), 0.5, C_BORDER),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, C_BORDER),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]))
            story.append(insight_tbl)

        narrativa_heatmap = text.get("narrativa_heatmap", "")
        if narrativa_heatmap:
            story.append(SP)
            story.append(Paragraph(narrativa_heatmap, style_normal))

        # ══════════════════════════════════════════════════════════════════
        # Datos completos IBEX 35
        # ══════════════════════════════════════════════════════════════════
        story.append(SP)
        story.append(banner_h1("Datos completos IBEX 35"))
        story.append(SP)

        valid_df = prices_df[prices_df["error"].isna() | (prices_df["error"] == "")].copy()
        valid_df = valid_df.sort_values("change_pct", ascending=False)

        table_header = ["Empresa", "Ticker", "Apertura", "Máximo", "Mínimo", "Cierre", "Vol (M)", "Var%"]
        table_data = [table_header]
        for _, row in valid_df.iterrows():
            chg_val = row.get("change_pct", 0)
            chg_val = chg_val if pd.notna(chg_val) else 0
            chg_str = f"{chg_val:+.2f}%"
            vol = row.get("volume", 0)
            vol = vol if pd.notna(vol) else 0
            table_data.append([
                Paragraph(str(row.get("name", ""))[:22], style_small),
                Paragraph(str(row.get("ticker", "")).replace(".MC",""), style_small),
                Paragraph(f'{row.get("open", 0) or 0:.2f}', style_small),
                Paragraph(f'{row.get("high", 0) or 0:.2f}', style_small),
                Paragraph(f'{row.get("low", 0) or 0:.2f}', style_small),
                Paragraph(f'{row.get("close", 0) or 0:.2f}', style_small),
                Paragraph(f'{vol/1_000_000:.1f}', style_small),
                Paragraph(f'<font color="{COLORS["green"] if chg_val >= 0 else COLORS["red"]}"><b>{chg_str}</b></font>', style_small),
            ])

        col_widths = [4.2*cm, 1.7*cm, 1.8*cm, 1.8*cm, 1.8*cm, 1.8*cm, 1.9*cm, 1.5*cm]
        tbl = Table(table_data, colWidths=col_widths, repeatRows=1)
        row_bg = []
        for i in range(1, len(table_data)):
            bg = C_LIGHT_GRAY if i % 2 == 0 else C_WHITE
            row_bg.append(("BACKGROUND", (0, i), (-1, i), bg))
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), C_PRIMARY),
            ("TEXTCOLOR", (0, 0), (-1, 0), C_WHITE),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 7.5),
            ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
            ("ALIGN", (0, 0), (1, -1), "LEFT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.4, C_BORDER),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ] + row_bg))
        story.append(tbl)

        # ══════════════════════════════════════════════════════════════════
        # Mejores y peores valores
        # ══════════════════════════════════════════════════════════════════
        story.append(SP)
        story.append(banner_h1("Mejores y peores valores"))
        story.append(SP)

        if charts.get("top_movers") and os.path.exists(charts["top_movers"]):
            story.append(Image(charts["top_movers"], width=PAGE_W, height=8*cm))
            story.append(Paragraph("Variación porcentual de los mejores y peores valores del día", style_caption))

        gainers = analysis.get("top_gainers", [])
        losers = analysis.get("top_losers", [])
        if gainers or losers:
            story.append(SP)
            gl_header = [
                Paragraph("<b>Mayores subidas</b>", style_small),
                Paragraph("<b>Var%</b>", style_small),
                Paragraph("", style_small),
                Paragraph("<b>Mayores bajadas</b>", style_small),
                Paragraph("<b>Var%</b>", style_small),
            ]
            gl_data = [gl_header]
            for i in range(max(len(gainers), len(losers))):
                g = gainers[i] if i < len(gainers) else {}
                l = losers[i] if i < len(losers) else {}
                g_chg = g.get("change_pct", 0) or 0
                l_chg = l.get("change_pct", 0) or 0
                gl_data.append([
                    Paragraph(g.get("name", ""), style_small) if g else Paragraph("", style_small),
                    Paragraph(f'<font color="{COLORS["green"]}"><b>+{g_chg:.2f}%</b></font>', style_small) if g else Paragraph("", style_small),
                    Paragraph("", style_small),
                    Paragraph(l.get("name", ""), style_small) if l else Paragraph("", style_small),
                    Paragraph(f'<font color="{COLORS["red"]}"><b>{l_chg:.2f}%</b></font>', style_small) if l else Paragraph("", style_small),
                ])
            row_bg_gl = []
            for i in range(1, len(gl_data)):
                row_bg_gl.append(("BACKGROUND", (0, i), (1, i), C_ACCENT_ALT if i % 2 == 0 else C_WHITE))
                row_bg_gl.append(("BACKGROUND", (3, i), (4, i), hex_to_reportlab("#FFF0F0") if i % 2 == 0 else C_WHITE))
            gl_tbl = Table(gl_data, colWidths=[5*cm, 2.5*cm, 0.5*cm, 5*cm, 2.5*cm])
            gl_tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (1, 0), C_ACCENT),
                ("BACKGROUND", (3, 0), (4, 0), hex_to_reportlab("#F5CCCC")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (1, -1), 0.4, C_BORDER),
                ("GRID", (3, 0), (4, -1), 0.4, C_BORDER),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ] + row_bg_gl))
            story.append(gl_tbl)

        # ══════════════════════════════════════════════════════════════════
        # Análisis sectorial
        # ══════════════════════════════════════════════════════════════════
        story.append(SP)
        story.append(banner_h1("Análisis sectorial"))
        story.append(SP)

        if charts.get("sector_bar") and os.path.exists(charts["sector_bar"]):
            story.append(Image(charts["sector_bar"], width=PAGE_W, height=7*cm))
            story.append(Paragraph("Variación media por sector del IBEX 35", style_caption))

        story.append(SP)
        story.append(Paragraph(text.get("narrativa_sectores", ""), style_normal))

        # ══════════════════════════════════════════════════════════════════
        # Noticias relevantes
        # ══════════════════════════════════════════════════════════════════
        story.append(SP)
        story.append(banner_h1("Noticias relevantes"))
        story.append(SP)
        story.append(Paragraph(text.get("narrativa_noticias", ""), style_normal))
        story.append(SP)

        key_news = analysis.get("key_news_impact", [])
        for item in key_news[:8]:
            impact = item.get("impact", "neutral")
            impact_color = COLORS["green"] if impact == "positivo" else (COLORS["red"] if impact == "negativo" else "#555555")
            news_block = [
                [Paragraph(
                    f'<b>{item.get("news_title", "")}</b>  <font color="{impact_color}">▶ {impact.upper()}</font>',
                    style_normal
                )],
                [Paragraph(item.get("analysis", ""), style_small)],
            ]
            news_tbl = Table(news_block, colWidths=[PAGE_W])
            news_tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), C_ACCENT),
                ("BACKGROUND", (0, 1), (-1, 1), C_ACCENT_ALT),
                ("BOX", (0, 0), (-1, -1), 0.5, C_BORDER),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ]))
            story.append(KeepTogether(news_tbl))
            story.append(Spacer(1, 0.1*cm))

        # ══════════════════════════════════════════════════════════════════
        # Volumen + Señales técnicas
        # ══════════════════════════════════════════════════════════════════
        story.append(SP)
        story.append(banner_h1("Volumen y señales técnicas"))
        story.append(SP)

        if charts.get("volume_bar") and os.path.exists(charts["volume_bar"]):
            story.append(Image(charts["volume_bar"], width=PAGE_W, height=6.5*cm))
            story.append(Paragraph("Top 10 valores por volumen negociado", style_caption))

        signals = analysis.get("technical_signals", [])
        if signals:
            story.append(SP)
            story.append(banner_h2("Señales técnicas destacadas"))
            story.append(SP)
            # Máximo 10 señales; todas las celdas como Paragraph para garantizar word-wrap
            sig_header = [
                Paragraph("<b>Ticker</b>", style_small),
                Paragraph("<b>Señal técnica</b>", style_small),
                Paragraph("<b>RSI 14</b>", style_small),
                Paragraph("<b>Comentario</b>", style_small),
            ]
            sig_data = [sig_header]
            for s in signals[:10]:
                rsi_val = s.get("rsi_14") or s.get("rsi_approx")
                rsi_str = f"{rsi_val:.1f}" if isinstance(rsi_val, (int, float)) else "—"
                sig_data.append([
                    Paragraph(s.get("ticker", "").replace(".MC", ""), style_small),
                    Paragraph(s.get("signal", ""), style_small),
                    Paragraph(rsi_str, style_small),
                    Paragraph(s.get("comment", ""), style_small),
                ])
            row_bg_sig = []
            for i in range(1, len(sig_data)):
                bg = C_LIGHT_GRAY if i % 2 == 0 else C_WHITE
                row_bg_sig.append(("BACKGROUND", (0, i), (-1, i), bg))
            sig_tbl = Table(sig_data, colWidths=[1.8*cm, 4.2*cm, 1.5*cm, 9.5*cm])
            sig_tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), C_PRIMARY),
                ("TEXTCOLOR", (0, 0), (-1, 0), C_WHITE),
                ("GRID", (0, 0), (-1, -1), 0.4, C_BORDER),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ] + row_bg_sig))
            story.append(sig_tbl)

        # ══════════════════════════════════════════════════════════════════
        # Conclusión
        # ══════════════════════════════════════════════════════════════════
        story.append(SP)
        story.append(banner_h1("Conclusión"))
        story.append(SP)
        story.append(Paragraph(text.get("conclusion", ""), style_normal))
        story.append(Spacer(1, 0.4*cm))
        story.append(HRFlowable(width="100%", thickness=0.5, color=C_BORDER))
        story.append(Spacer(1, 0.15*cm))
        story.append(Paragraph(text.get("disclaimer", ""), style_small))

        # ── Construcción del documento ─────────────────────────────────────
        def on_page(canvas, doc):
            canvas.saveState()
            # Header (todas las páginas)
            canvas.setFillColor(C_PRIMARY)
            canvas.rect(1.5*cm, A4[1] - 1.1*cm, A4[0] - 3*cm, 0.55*cm, fill=1, stroke=0)
            canvas.setFillColor(C_WHITE)
            canvas.setFont("Helvetica-Bold", 8)
            canvas.drawString(1.8*cm, A4[1] - 0.78*cm, f"IBEX 35 — Informe Diario — {date_es}")
            canvas.drawRightString(A4[0] - 1.8*cm, A4[1] - 0.78*cm, f"Página {doc.page}")
            # Footer
            canvas.setFillColor(C_SECONDARY)
            canvas.rect(1.5*cm, 0.8*cm, A4[0] - 3*cm, 0.35*cm, fill=1, stroke=0)
            canvas.setFillColor(C_WHITE)
            canvas.setFont("Helvetica", 7)
            canvas.drawString(1.8*cm, 0.92*cm, date_footer)
            canvas.drawRightString(A4[0] - 1.8*cm, 0.92*cm, "Solo con fines informativos — No es asesoramiento financiero")
            canvas.restoreState()

        doc = SimpleDocTemplate(
            out_path,
            pagesize=A4,
            leftMargin=2*cm, rightMargin=2*cm,
            topMargin=1.5*cm, bottomMargin=1.5*cm,
        )
        doc.build(story, onFirstPage=on_page, onLaterPages=on_page)

        size_kb = os.path.getsize(out_path) / 1024
        logger.info(f"PDF generado: {out_path} ({size_kb:.0f} KB)")
        if size_kb < 50:
            raise WriterError(f"PDF demasiado pequeño ({size_kb:.0f} KB), posible error de generación")
        return out_path
