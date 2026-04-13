import os
import json
import time
import shutil
import logging
from datetime import datetime

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

from agents.utils import MADRID_TZ, strip_markdown_fence, load_instructions

logger = logging.getLogger("bolsa.writer")

COLORS = {
    "primary":    "#1a2332",   # azul muy oscuro institucional — banners principales
    "secondary":  "#2c3e55",   # azul oscuro derivado — sub-secciones
    "accent":     "#e8edf3",   # azul muy pálido — fondos de cajas informativas
    "accent_alt": "#f4f6f8",   # casi blanco azulado — filas alternas de tabla
    "text":       "#1a2332",   # mismo tono que primary para coherencia tipográfica
    "green":      "#27ae60",   # verde apagado profesional (no neón)
    "red":        "#c0392b",   # rojo institucional contenido
    "light_gray": "#f8f9fa",   # gris casi blanco — filas alternas tabla principal
    "border":     "#c5ccd4",   # borde sutil, no intrusivo
    "white":      "#FFFFFF",
    "green_row":  "#eafaf1",   # fondo filas top gainers (muy tenue)
    "red_row":    "#fdecea",   # fondo filas top losers (muy tenue)
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
        self.madrid = MADRID_TZ
        self.system_prompt = self._load_instructions()

    def _load_instructions(self) -> str:
        return load_instructions("writer_instructions.md")

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
                raw = strip_markdown_fence(raw)
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

    @staticmethod
    def _change_to_color(chg: float):
        """
        Convierte una variación % en color RGB profesional (paleta institucional).
        - Neutro (|chg| <= 0.1%): gris suave
        - Positivo: verde pálido → #27ae60 (apagado, no neón)
        - Negativo: rojo pálido → #c0392b (institucional)
        Escala saturada en ±3%.
        """
        intensity = min(abs(chg) / 3.0, 1.0)
        if abs(chg) <= 0.1:
            return (0.82, 0.84, 0.86)          # gris azulado neutro
        elif chg > 0:
            # verde pálido (0.80, 0.93, 0.84) → #27ae60 (0.153, 0.682, 0.376)
            r = 0.80 - intensity * 0.647
            g = 0.93 - intensity * 0.248
            b = 0.84 - intensity * 0.464
            return (r, g, b)
        else:
            # rojo pálido (0.96, 0.82, 0.82) → #c0392b (0.753, 0.224, 0.169)
            r = 0.96 - intensity * 0.207
            g = 0.82 - intensity * 0.596
            b = 0.82 - intensity * 0.651
            return (r, g, b)

    def _chart_heatmap(self, df: pd.DataFrame) -> str:
        """Treemap agrupado por sector, tamaño proporcional a capitalización bursátil."""
        path = os.path.join(self.charts_dir, "heatmap.png")
        try:
            import squarify

            df = df.copy()
            df["change_pct"] = pd.to_numeric(df["change_pct"], errors="coerce").fillna(0)
            df["market_cap"] = pd.to_numeric(df.get("market_cap", pd.Series(dtype=float)), errors="coerce")

            if df["market_cap"].isna().all():
                df["market_cap"] = 1.0
            else:
                median_cap = df["market_cap"].median()
                df["market_cap"] = df["market_cap"].fillna(median_cap)
            df["market_cap"] = df["market_cap"].clip(lower=1e6)

            if "sector" not in df.columns or df["sector"].isna().all():
                df["sector"] = "IBEX 35"
            df["sector"] = df["sector"].fillna("Otros")

            sector_caps = df.groupby("sector")["market_cap"].sum().sort_values(ascending=False)
            total_cap = sector_caps.sum()
            sector_sizes = sector_caps.values.tolist()
            sector_names = sector_caps.index.tolist()

            # ── Layout: treemap arriba, leyenda de colores + sectorial debajo ──
            fig = plt.figure(figsize=(14, 10), facecolor="white")
            ax = fig.add_axes([0.01, 0.22, 0.98, 0.75])   # treemap
            ax_leg = fig.add_axes([0.01, 0.0, 0.98, 0.21])  # leyenda inferior
            ax.set_facecolor("white")
            ax.axis("off")
            ax_leg.axis("off")

            sector_rects = squarify.squarify(
                squarify.normalize_sizes(sector_sizes, 100, 100), 0, 0, 100, 100
            )

            PAD = 0.5
            small_sectors = []   # sectores cuyos bloques son demasiado pequeños para etiqueta

            for s_rect, s_name in zip(sector_rects, sector_names):
                sx, sy, sw, sh = s_rect["x"], s_rect["y"], s_rect["dx"], s_rect["dy"]
                sector_area = sw * sh

                # Borde de sector (blanco grueso)
                ax.add_patch(mpatches.FancyBboxPatch(
                    (sx, sy), sw, sh, boxstyle="square,pad=0",
                    facecolor="none", edgecolor="#ffffff", linewidth=3, zorder=3
                ))

                # Tickers dentro del sector
                tickers_in = df[df["sector"] == s_name].sort_values("market_cap", ascending=False)
                t_sizes = tickers_in["market_cap"].tolist()
                if not t_sizes:
                    continue

                t_rects = squarify.squarify(
                    squarify.normalize_sizes(t_sizes, sw - PAD * 2, sh - PAD * 2),
                    sx + PAD, sy + PAD, sw - PAD * 2, sh - PAD * 2
                )

                for t_rect, (_, t_row) in zip(t_rects, tickers_in.iterrows()):
                    tx, ty, tw, th = t_rect["x"], t_rect["y"], t_rect["dx"], t_rect["dy"]
                    chg = float(t_row["change_pct"])
                    face = self._change_to_color(chg)
                    intensity = min(abs(chg) / 3.0, 1.0)

                    ax.add_patch(mpatches.FancyBboxPatch(
                        (tx, ty), tw, th, boxstyle="square,pad=0",
                        facecolor=face, edgecolor="#ffffff", linewidth=0.5, zorder=2
                    ))

                    # Ticker + variación dentro del bloque — fuente proporcional al tamaño
                    if tw > 1.8 and th > 1.4:
                        ticker_short = str(t_row.get("ticker", "")).replace(".MC", "")
                        chg_str = f"{chg:+.1f}%"
                        # Escala proporcional: bloque grande → fuente grande, sin tope bajo
                        fsize = max(3.5, min(18.0, min(tw, th) * 1.15))
                        txt_color = "#ffffff" if intensity > 0.35 else "#111111"
                        ax.text(tx + tw / 2, ty + th * 0.60, ticker_short,
                                ha="center", va="center", fontsize=fsize,
                                fontweight="bold", color=txt_color, zorder=4, clip_on=True)
                        ax.text(tx + tw / 2, ty + th * 0.28, chg_str,
                                ha="center", va="center", fontsize=fsize * 0.80,
                                color=txt_color, zorder=4, clip_on=True)

                # ── Etiqueta de sector — tamaño proporcional al bloque ──
                sector_pct = sector_caps[s_name] / total_cap * 100
                label = f"{s_name} ({sector_pct:.0f}%)"

                if sector_area >= 200:          # bloques grandes: etiqueta dentro grande
                    fsize_s = max(8, min(13, (sector_area ** 0.45) * 0.55))
                    ax.text(sx + sw / 2, sy + sh - PAD * 0.6,
                            label, ha="center", va="top",
                            fontsize=fsize_s, fontweight="bold", color="#ffffff", zorder=5,
                            bbox=dict(boxstyle="round,pad=0.2", facecolor="#00000077", edgecolor="none"))
                elif sector_area >= 60:         # bloques medianos: etiqueta compacta
                    fsize_s = max(6.5, min(8.5, (sector_area ** 0.40) * 0.55))
                    ax.text(sx + sw / 2, sy + sh - PAD * 0.4,
                            label, ha="center", va="top",
                            fontsize=fsize_s, fontweight="bold", color="#ffffff", zorder=5,
                            bbox=dict(boxstyle="round,pad=0.12", facecolor="#00000077", edgecolor="none"))
                else:                           # bloques pequeños: referencia en leyenda inferior
                    small_sectors.append((s_name, sector_pct))

            ax.set_xlim(0, 100)
            ax.set_ylim(0, 100)
            ax.set_title(f"Mapa de calor IBEX 35 — {self.date}",
                         fontsize=12, fontweight="bold", color=COLORS["primary"], pad=6)

            # ── Leyenda de escala cromática — horizontal DEBAJO del treemap ──
            color_legend = [
                (self._change_to_color(4.0),  ">+3%  subida fuerte"),
                (self._change_to_color(1.5),  "+1–3% subida"),
                (self._change_to_color(0.0),  "~0%  sin cambios"),
                (self._change_to_color(-1.5), "-1–3% caída"),
                (self._change_to_color(-4.0), "<-3%  caída fuerte"),
            ]
            legend_patches = [mpatches.Patch(facecolor=c, label=l, edgecolor="#888888")
                              for c, l in color_legend]
            leg = ax_leg.legend(
                handles=legend_patches, loc="upper center",
                ncol=len(color_legend), fontsize=9,
                framealpha=0.90, edgecolor="#aaaaaa",
                title="Variación diaria", title_fontsize=9,
                bbox_to_anchor=(0.5, 1.0),
            )

            # ── Leyenda inferior: sectores pequeños que no caben en el bloque ──
            if small_sectors:
                legend_text = "Sectores pequeños: " + "  |  ".join(
                    f"{n} ({p:.0f}%)" for n, p in small_sectors
                )
            else:
                legend_text = "Tamaño proporcional a capitalización bursátil"
            ax_leg.text(0.5, 0.18, legend_text, ha="center", va="center",
                        fontsize=10, color="#222222", fontweight="bold",
                        transform=ax_leg.transAxes)

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
            bars = ax.barh(labels, values, color=bar_colors, edgecolor="none", height=0.45)
            ax.axvline(0, color=COLORS["secondary"], linewidth=0.8, alpha=0.5)
            # Etiquetas al final de cada barra
            x_range = max(abs(v) for v in values) if values else 1
            offset = x_range * 0.02
            for bar, val in zip(bars, values):
                ax.text(val + (offset if val >= 0 else -offset),
                        bar.get_y() + bar.get_height() / 2,
                        f"{val:+.2f}%", va="center",
                        ha="left" if val >= 0 else "right",
                        fontsize=8.5, color=COLORS["primary"], fontweight="bold")
            ax.set_title(f"Mejores y peores valores — {self.date}", fontsize=12,
                         fontweight="bold", color=COLORS["primary"], pad=10)
            for spine in ["top", "right", "left", "bottom"]:
                ax.spines[spine].set_visible(False)
            ax.tick_params(axis="both", which="both", length=0)
            ax.set_xlabel("")
            ax.grid(False)
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
            sorted_pairs = sorted(zip(values, names), reverse=True)
            values, names = zip(*sorted_pairs)
            bar_colors = [COLORS["green"] if v > 0 else COLORS["red"] for v in values]

            fig, ax = plt.subplots(figsize=(10, 5), facecolor="white")
            ax.set_facecolor("white")
            bars = ax.bar(names, values, color=bar_colors, edgecolor="none", width=0.42)
            ax.axhline(0, color=COLORS["secondary"], linewidth=0.8, alpha=0.5)
            y_range = max(abs(v) for v in values) if values else 1
            offset = y_range * 0.03
            for bar, val in zip(bars, values):
                ax.text(bar.get_x() + bar.get_width() / 2,
                        val + (offset if val >= 0 else -offset),
                        f"{val:+.2f}%", ha="center",
                        va="bottom" if val >= 0 else "top",
                        fontsize=8.5, color=COLORS["primary"], fontweight="bold")
            ax.set_title(f"Variación por sector — {self.date}", fontsize=12,
                         fontweight="bold", color=COLORS["primary"], pad=10)
            for spine in ["top", "right", "left"]:
                ax.spines[spine].set_visible(False)
            ax.spines["bottom"].set_color(COLORS["border"])
            ax.tick_params(axis="both", which="both", length=0)
            ax.set_ylabel("")
            ax.grid(False)
            plt.xticks(rotation=20, ha="right", fontsize=9, color=COLORS["text"])
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
            bars_v = ax.bar(labels, vols, color=COLORS["secondary"], edgecolor="none", width=0.48)
            v_range = max(vols) if vols else 1
            for bar, val in zip(bars_v, vols):
                ax.text(bar.get_x() + bar.get_width() / 2,
                        val + v_range * 0.02,
                        f"{val:.1f}M", ha="center", va="bottom",
                        fontsize=8, color=COLORS["primary"], fontweight="bold")
            ax.set_title(f"Top 10 valores por volumen — {self.date}", fontsize=12,
                         fontweight="bold", color=COLORS["primary"], pad=10)
            for spine in ["top", "right", "left"]:
                ax.spines[spine].set_visible(False)
            ax.spines["bottom"].set_color(COLORS["border"])
            ax.tick_params(axis="both", which="both", length=0)
            ax.set_ylabel("")
            ax.grid(False)
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
                                      alignment=4,  # TA_JUSTIFY
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
                                      alignment=4,  # TA_JUSTIFY
                                      textColor=colors.HexColor("#222222"))
        style_small_white = ParagraphStyle("small_white", parent=style_small,
                                            fontName="Helvetica-Bold",
                                            textColor=C_WHITE, alignment=0)
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
                Paragraph("<b>Lectura clave</b>", style_small_white),
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
        n_rows = len(table_data)
        C_GREEN_ROW = hex_to_reportlab(COLORS["green_row"])
        C_RED_ROW   = hex_to_reportlab(COLORS["red_row"])
        row_bg = []
        for i in range(1, n_rows):
            if i <= 5:                          # top 5 mejores — fondo verde muy tenue
                row_bg.append(("BACKGROUND", (0, i), (-1, i), C_GREEN_ROW))
            elif i >= n_rows - 5:               # top 5 peores — fondo rojo muy tenue
                row_bg.append(("BACKGROUND", (0, i), (-1, i), C_RED_ROW))
            else:
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
            ("GRID", (0, 0), (-1, -1), 0.3, C_BORDER),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
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
                Paragraph("Ticker", style_small_white),
                Paragraph("Señal técnica", style_small_white),
                Paragraph("RSI 14", style_small_white),
                Paragraph("Comentario", style_small_white),
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
