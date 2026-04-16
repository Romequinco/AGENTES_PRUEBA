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
    "primary":         "#1a2332",   # azul muy oscuro institucional — banners principales
    "secondary":       "#2c5282",   # acento institucional — sub-secciones
    "text_secondary":  "#5a6977",   # texto secundario
    "accent":          "#e8edf3",   # azul muy pálido — fondos de cajas informativas
    "accent_alt":      "#f4f6f8",   # casi blanco azulado — filas alternas de tabla
    "text":            "#1a2332",   # mismo tono que primary para coherencia tipográfica
    "green":           "#27ae60",   # verde apagado profesional (no neón)
    "red":             "#c0392b",   # rojo institucional contenido
    "neutral":         "#95a5a6",   # neutro
    "light_gray":      "#f8f9fa",   # gris casi blanco — filas alternas tabla principal
    "row_alt":         "#f8f9fa",   # alias de light_gray
    "border":          "#e2e8f0",   # borde sutil, no intrusivo
    "white":           "#FFFFFF",
    "highlight_green": "#e8f5e9",   # fondo top 5 tabla unificada
    "highlight_red":   "#ffebee",   # fondo bottom 5 tabla unificada
    "badge_green":     "#d4edda",   # badge positivo
    "badge_red":       "#f8d7da",   # badge negativo
    "badge_neutral":   "#e2e3e5",   # badge neutro
    # aliases para compatibilidad
    "green_row":       "#e8f5e9",
    "red_row":         "#ffebee",
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
            analysis, prices_df, indicators, macro_data = self.load_analysis()
            text = self.generate_text(analysis)
            charts = self.generate_charts(prices_df, analysis, indicators)
            pdf_path = self.build_pdf(text, charts, analysis, prices_df, indicators, macro_data)
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
        indicators_path = os.path.join(self.raw_dir, f"ibex35_indicators_{self.date}.json")

        if not os.path.exists(analysis_path):
            raise WriterError(f"Análisis no encontrado: {analysis_path}")
        if not os.path.exists(prices_path):
            raise WriterError(f"Precios no encontrados: {prices_path}")

        with open(analysis_path, encoding="utf-8") as f:
            analysis = json.load(f)
        df = pd.read_csv(prices_path, encoding="utf-8")
        indicators = {}
        if os.path.exists(indicators_path):
            with open(indicators_path, encoding="utf-8") as f:
                indicators = json.load(f)
        # Compat: renombrar actionable_ideas → ideas_vigilar si es necesario
        if "actionable_ideas" in analysis and "ideas_vigilar" not in analysis:
            analysis["ideas_vigilar"] = analysis["actionable_ideas"]
        macro_data = {}
        macro_path = os.path.join(self.raw_dir, f"macro_{self.date}.json")
        if os.path.exists(macro_path):
            with open(macro_path, encoding="utf-8") as f:
                macro_data = json.load(f)
        return analysis, df, indicators, macro_data

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
                    max_tokens=3000,
                    system=self.system_prompt,
                    messages=[{"role": "user", "content": prompt}],
                    timeout=300.0,
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
            "resumen_ejecutivo": f"El IBEX 35 registró movimientos durante la sesión del {self.date}. Los datos de mercado están disponibles en la tabla adjunta del informe.",
            "puntos_clave": ["• Sin datos suficientes para generar puntos clave del día"],
            "contexto_macro_europeo": [],
            "atribucion_concentracion": "",
            "heatmap": {
                "descripcion": "Representación visual del IBEX 35 donde cada bloque corresponde a una empresa, su tamaño es proporcional a la capitalización bursátil y su color refleja la variación diaria.",
                "leyenda": "Verde intenso: subida >+3% | Verde suave: subida leve | Gris: sin cambios | Rojo suave: caída leve | Rojo intenso: caída <-3%",
                "insight_clave": "",
            },
            "analisis_sectorial_texto": "",
            "noticias": [],
            "agenda_evento_clave": {"evento": "", "contexto": ""},
            "conclusion": f"La sesión del IBEX 35 del {self.date} concluyó con los resultados reflejados en los datos adjuntos. El análisis técnico detallado no está disponible en esta ejecución.",
            "calidad_datos": "limitados",
            "disclaimer": "Este informe ha sido generado de forma automatizada con fines meramente informativos y no constituye asesoramiento financiero ni recomendación de inversión. Las secciones de ideas son análisis técnicos objetivos para seguimiento, no constituyen consejo de inversión.",
        }

    @staticmethod
    def _chart_placeholder(path: str, label: str) -> str:
        """Genera un PNG de placeholder gris cuando un chart secundario falla."""
        try:
            fig, ax = plt.subplots(figsize=(10, 4), facecolor="#f4f6f8")
            ax.set_facecolor("#f4f6f8")
            ax.text(0.5, 0.5, f"Datos no disponibles\n({label})",
                    ha="center", va="center", fontsize=11,
                    color="#888888", transform=ax.transAxes)
            ax.axis("off")
            fig.savefig(path, dpi=100, bbox_inches="tight", facecolor="#f4f6f8")
            plt.close(fig)
            return path
        except Exception:
            return None

    def generate_charts(self, prices_df: pd.DataFrame, analysis: dict, indicators: dict = None) -> dict:
        os.makedirs(self.charts_dir, exist_ok=True)
        valid = prices_df[prices_df["error"].isna() | (prices_df["error"] == "")].copy()
        valid["change_pct"] = pd.to_numeric(valid["change_pct"], errors="coerce")
        valid["volume"] = pd.to_numeric(valid["volume"], errors="coerce")

        charts = {}
        charts["heatmap"] = self._chart_heatmap(valid)
        if not charts["heatmap"]:
            raise WriterError("No se pudo generar el mapa de calor (heatmap). Abortando generación de PDF.")

        placeholder_sector = os.path.join(self.charts_dir, "sector_bar_placeholder.png")
        charts["sector_bar"] = self._chart_sector_bar(analysis) or self._chart_placeholder(placeholder_sector, "sectores")

        if indicators:
            placeholder_contrib = os.path.join(self.charts_dir, "contribution_placeholder.png")
            charts["point_contribution"] = (
                self._chart_point_contribution(indicators, analysis)
                or self._chart_placeholder(placeholder_contrib, "atribución de movimiento")
            )

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
            fig = plt.figure(figsize=(14, 11), facecolor="white")
            ax = fig.add_axes([0.0, 0.20, 1.0, 0.78])    # treemap
            ax_leg = fig.add_axes([0.0, 0.0, 1.0, 0.20])  # leyenda inferior
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
                ncol=len(color_legend), fontsize=12,
                framealpha=0.90, edgecolor="#aaaaaa",
                title="Variación diaria", title_fontsize=12,
                bbox_to_anchor=(0.5, 1.02),
            )

            # ── Leyenda inferior: sectores pequeños que no caben en el bloque ──
            if small_sectors:
                legend_text = "Sectores pequeños: " + "  |  ".join(
                    f"{n} ({p:.0f}%)" for n, p in small_sectors
                )
            else:
                legend_text = "Tamaño proporcional a capitalización bursátil"
            ax_leg.text(0.5, 0.22, legend_text, ha="center", va="center",
                        fontsize=12, color="#222222", fontweight="bold",
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

    def _chart_point_contribution(self, indicators: dict, analysis: dict) -> str:
        """Gráfico de barras horizontales: contribución en puntos al movimiento del IBEX."""
        path = os.path.join(self.charts_dir, "point_contribution.png")
        try:
            tickers_data = indicators.get("tickers", {})
            contribs = []
            for ticker, ind in tickers_data.items():
                pts = ind.get("contribution_pts")
                name = ind.get("name", ticker.replace(".MC", ""))
                if pts is not None:
                    contribs.append((pts, ticker.replace(".MC", ""), name))
            if not contribs:
                return None
            contribs.sort(key=lambda x: x[0])

            # Top 5 positivos + top 5 negativos
            top_pos = [c for c in contribs if c[0] > 0][-5:]
            top_neg = [c for c in contribs if c[0] < 0][:5]
            display = top_neg + top_pos
            if not display:
                return None

            labels = [f"{c[1]}" for c in display]
            values = [c[0] for c in display]
            bar_colors = [COLORS["green"] if v > 0 else COLORS["red"] for v in values]

            fig, ax = plt.subplots(figsize=(10, 6), facecolor="white")
            ax.set_facecolor("white")
            bars = ax.barh(labels, values, color=bar_colors, edgecolor="none", height=0.5)
            ax.axvline(0, color=COLORS["secondary"], linewidth=0.8, alpha=0.5)
            x_range = max(abs(v) for v in values) if values else 1
            offset = x_range * 0.02
            for bar, val in zip(bars, values):
                ax.text(val + (offset if val >= 0 else -offset),
                        bar.get_y() + bar.get_height() / 2,
                        f"{val:+.1f} pts", va="center",
                        ha="left" if val >= 0 else "right",
                        fontsize=8.5, color=COLORS["primary"], fontweight="bold")
            ax.set_title(f"Atribución del movimiento del IBEX — {self.date}", fontsize=12,
                         fontweight="bold", color=COLORS["primary"], pad=10)
            ax.set_xlabel("Contribución en puntos al índice", fontsize=9, color=COLORS["text"])
            for spine in ["top", "right", "left", "bottom"]:
                ax.spines[spine].set_visible(False)
            ax.tick_params(axis="both", which="both", length=0)
            ax.grid(False)
            plt.tight_layout()
            fig.savefig(path, dpi=150, bbox_inches="tight", facecolor="white")
            plt.close(fig)
        except Exception as e:
            logger.warning(f"Error generando point_contribution: {e}")
            path = None
        return path

    def _chart_range_52w(self, indicators: dict) -> str:
        """Gráfico de barras horizontales mostrando posición de cada acción en su rango 52W."""
        path = os.path.join(self.charts_dir, "range_52w.png")
        try:
            tickers_data = indicators.get("tickers", {})
            entries = []
            for ticker, ind in tickers_data.items():
                pct = ind.get("range_52w_pct")
                name = ind.get("name", ticker.replace(".MC", ""))
                chg = ind.get("change_pct", 0) or 0
                if pct is not None:
                    short = ticker.replace(".MC", "")
                    entries.append((pct, short, chg))
            if not entries:
                return None
            entries.sort(key=lambda x: x[0])

            labels = [e[1] for e in entries]
            values = [e[0] for e in entries]
            # Color por posición: rojo (cerca min) → naranja → verde (cerca max)
            bar_colors = []
            for v in values:
                if v >= 80:
                    bar_colors.append(COLORS["green"])
                elif v >= 50:
                    bar_colors.append("#2ecc71")
                elif v >= 30:
                    bar_colors.append("#f39c12")
                else:
                    bar_colors.append(COLORS["red"])

            fig, ax = plt.subplots(figsize=(12, max(9, len(entries) * 0.38)), facecolor="white")
            ax.set_facecolor("white")
            bars = ax.barh(labels, values, color=bar_colors, edgecolor="none", height=0.6)
            ax.axvline(50, color=COLORS["border"], linewidth=0.8, linestyle="--", alpha=0.7)
            for bar, val in zip(bars, values):
                ax.text(min(val + 1, 98), bar.get_y() + bar.get_height() / 2,
                        f"{val:.0f}%", va="center", ha="left",
                        fontsize=7.5, color=COLORS["primary"])
            ax.set_xlim(0, 105)
            ax.set_title(f"Posición en rango 52 semanas — {self.date}", fontsize=12,
                         fontweight="bold", color=COLORS["primary"], pad=10)
            ax.set_xlabel("% del rango anual (0% = mínimo, 100% = máximo)", fontsize=8, color=COLORS["text"])
            for spine in ["top", "right", "left", "bottom"]:
                ax.spines[spine].set_visible(False)
            ax.tick_params(axis="both", which="both", length=0)
            ax.tick_params(axis="y", labelsize=8)
            ax.grid(False)
            plt.tight_layout()
            fig.savefig(path, dpi=150, bbox_inches="tight", facecolor="white")
            plt.close(fig)
        except Exception as e:
            logger.warning(f"Error generando range_52w: {e}")
            path = None
        return path

    def _chart_macro_comparison(self, analysis: dict) -> str:
        """Tabla visual de comparativa de índices europeos y macro del día."""
        path = os.path.join(self.charts_dir, "macro_comparison.png")
        try:
            macro_ctx = analysis.get("macro_context", {})
            if not macro_ctx:
                return None

            # Extraer datos del texto de ibex_vs_europe — si el análisis tiene datos crudos
            # Construimos una tabla simple con los datos disponibles en el JSON
            rows = []
            ibex_ms = analysis.get("market_summary", {})
            ibex_chg = ibex_ms.get("ibex35_change_pct", 0) or 0
            rows.append(("IBEX 35", ibex_chg, "España"))

            # Buscamos datos macro en el contexto (puede que el analyst los incluya como texto)
            ibex_vs = macro_ctx.get("ibex_vs_europe", "")
            eur_usd = macro_ctx.get("eur_usd_impact", "")
            vix = macro_ctx.get("vix_level", "")
            commodities = macro_ctx.get("commodities_impact", "")

            # Crear figura de texto informativo si no hay datos estructurados de índices
            fig, ax = plt.subplots(figsize=(12, 5), facecolor="white")
            ax.set_facecolor("#f8f9fa")
            ax.axis("off")

            texts = [
                ("IBEX vs Europa", ibex_vs[:120] if ibex_vs else "Sin datos"),
                ("EUR/USD", eur_usd[:120] if eur_usd else "Sin datos"),
                ("VIX", vix[:120] if vix else "Sin datos"),
                ("Materias primas", commodities[:120] if commodities else "Sin datos"),
            ]
            for i, (label, content) in enumerate(texts):
                y = 0.82 - i * 0.22
                ax.text(0.01, y, f"▌ {label}", transform=ax.transAxes,
                        fontsize=10, fontweight="bold", color=COLORS["primary"], va="top")
                ax.text(0.01, y - 0.06, content, transform=ax.transAxes,
                        fontsize=8.5, color="#333333", va="top", wrap=True)

            ax.set_title(f"Contexto Macro Europeo — {self.date}", fontsize=12,
                         fontweight="bold", color=COLORS["primary"], pad=10,
                         loc="left")
            fig.savefig(path, dpi=150, bbox_inches="tight", facecolor="white")
            plt.close(fig)
        except Exception as e:
            logger.warning(f"Error generando macro_comparison: {e}")
            path = None
        return path

    def build_pdf(self, text: dict, charts: dict, analysis: dict, prices_df: pd.DataFrame, indicators: dict = None, macro_data: dict = None) -> str:
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
        meses = ["enero","febrero","marzo","abril","mayo","junio",
                 "julio","agosto","septiembre","octubre","noviembre","diciembre"]
        try:
            dt = datetime.strptime(self.date, "%Y-%m-%d")
            date_es = f"{dt.day} de {meses[dt.month - 1]} de {dt.year}"
        except ValueError:
            date_es = self.date
        date_footer = datetime.now(self.madrid).strftime("%d/%m/%Y")

        story = []
        macro_raw = (macro_data or {}).get("macro", {})
        C_GREEN_ROW = hex_to_reportlab(COLORS["highlight_green"])
        C_RED_ROW   = hex_to_reportlab(COLORS["highlight_red"])
        C_HIGHLIGHT_GREEN = hex_to_reportlab(COLORS["highlight_green"])
        C_HIGHLIGHT_RED   = hex_to_reportlab(COLORS["highlight_red"])
        C_SECONDARY_NEW   = hex_to_reportlab(COLORS["secondary"])

        # ── helpers de señal/tendencia ──────────────────────────────────
        def _tend_icon(macd_trend):
            return "&#9650;" if macd_trend == "alcista" else ("&#9660;" if macd_trend == "bajista" else "&#8594;")

        def _tend_color(macd_trend):
            return COLORS["green"] if macd_trend == "alcista" else (COLORS["red"] if macd_trend == "bajista" else COLORS["neutral"])

        def _signal_text(rsi, macd_trend):
            if rsi and rsi > 70:
                return ("(!) Sobrecompra", COLORS["red"])
            if rsi and rsi < 30:
                return ("Sobrevendido", COLORS["green"])
            if macd_trend == "alcista":
                return ("Alcista", COLORS["green"])
            if macd_trend == "bajista":
                return ("Bajista", COLORS["red"])
            return ("Neutro", COLORS["neutral"])

        # ══════════════════════════════════════════════════════════════════
        # PÁGINA 1: Portada + Cabecera macro + Resumen ejecutivo + Puntos clave
        # ══════════════════════════════════════════════════════════════════
        portada_title = (
            text.get("titular_portada")
            or text.get("titulo_informe")
            or f"IBEX 35 — Informe Diario — {date_es}"
        )
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

        # ── Cabecera macro: 10 indicadores en tabla 2×5 ──────────────────
        ms = analysis.get("market_summary", {})
        ibex_ind = (indicators or {}).get("ibex_index", {})
        ibex_close = ibex_ind.get("close") or ms.get("ibex35_close_pts", 0) or 0
        ibex_chg   = ibex_ind.get("change_pct") or ms.get("ibex35_change_pct", 0) or 0
        ibex_pts   = ibex_ind.get("change_abs") or 0

        MACRO_MAP = [
            ("IBEX 35",      ibex_close,                                   ibex_chg,           None),
            ("DAX",          macro_raw.get("^GDAXI", {}).get("close"),     macro_raw.get("^GDAXI", {}).get("change_pct"),   None),
            ("CAC 40",       macro_raw.get("^FCHI", {}).get("close"),      macro_raw.get("^FCHI", {}).get("change_pct"),    None),
            ("EuroStoxx 50", macro_raw.get("^STOXX50E", {}).get("close"),  macro_raw.get("^STOXX50E", {}).get("change_pct"), None),
            ("FTSE 100",     macro_raw.get("^FTSE", {}).get("close"),      macro_raw.get("^FTSE", {}).get("change_pct"),    None),
            ("S&P Fut.",     macro_raw.get("ES=F", {}).get("close"),       macro_raw.get("ES=F", {}).get("change_pct"),     None),
            ("EUR/USD",      macro_raw.get("EURUSD=X", {}).get("close"),   macro_raw.get("EURUSD=X", {}).get("change_pct"), None),
            ("Brent",        macro_raw.get("BZ=F", {}).get("close"),       macro_raw.get("BZ=F", {}).get("change_pct"),     None),
            ("Bono 10Y ES",  macro_raw.get("ES10Y=X", {}).get("close"),    macro_raw.get("ES10Y=X", {}).get("change_pct"),  None),
            ("VSTOXX",       macro_raw.get("^VSTOXX", {}).get("close"),    macro_raw.get("^VSTOXX", {}).get("change_pct"),  None),
        ]

        style_macro_label = ParagraphStyle("macro_label", fontName="Helvetica", fontSize=6.5,
                                            textColor=colors.HexColor(COLORS["text_secondary"]),
                                            alignment=1, spaceAfter=0, spaceBefore=0)
        style_macro_val   = ParagraphStyle("macro_val", fontName="Helvetica-Bold", fontSize=8.5,
                                            textColor=colors.HexColor(COLORS["text"]),
                                            alignment=1, spaceAfter=0, spaceBefore=0)
        style_macro_chg   = ParagraphStyle("macro_chg", fontName="Helvetica-Bold", fontSize=8,
                                            alignment=1, spaceAfter=0, spaceBefore=0)

        macro_row1 = []
        macro_row2 = []
        for name, val, chg_m, _ in MACRO_MAP[:5]:
            val_str = f"{val:,.2f}" if val else "N/D"
            if chg_m is not None:
                arrow = "&#9650;" if chg_m >= 0 else "&#9660;"
                color_m = COLORS["green"] if chg_m >= 0 else COLORS["red"]
                chg_str = f'<font color="{color_m}">{arrow} {chg_m:+.2f}%</font>'
            else:
                chg_str = "—"
            macro_row1.append([
                Paragraph(name, style_macro_label),
                Paragraph(val_str, style_macro_val),
                Paragraph(chg_str, style_macro_chg),
            ])
        for name, val, chg_m, _ in MACRO_MAP[5:]:
            val_str = f"{val:,.2f}" if val else "N/D"
            if chg_m is not None:
                arrow = "&#9650;" if chg_m >= 0 else "&#9660;"
                color_m = COLORS["green"] if chg_m >= 0 else COLORS["red"]
                chg_str = f'<font color="{color_m}">{arrow} {chg_m:+.2f}%</font>'
            else:
                chg_str = "—"
            macro_row2.append([
                Paragraph(name, style_macro_label),
                Paragraph(val_str, style_macro_val),
                Paragraph(chg_str, style_macro_chg),
            ])

        col_w_macro = [PAGE_W / 5] * 5
        def _build_macro_row(cells):
            row_data = []
            for cell in cells:
                row_data.append(KeepTogether(cell))
            return row_data

        macro_inner_data1 = [[
            Table([[c] for c in cell], colWidths=[PAGE_W/5 - 0.2*cm])
            for cell in macro_row1
        ]]
        macro_inner_data2 = [[
            Table([[c] for c in cell], colWidths=[PAGE_W/5 - 0.2*cm])
            for cell in macro_row2
        ]]

        # Construir tabla macro plana (2 filas de 5, cada celda con 3 líneas)
        flat_macro = []
        flat_row1 = []
        flat_row2 = []
        for name, val, chg_m, _ in MACRO_MAP[:5]:
            val_str = f"{val:,.2f}" if val else "N/D"
            if chg_m is not None:
                arrow = "&#9650;" if chg_m >= 0 else "&#9660;"
                color_m = COLORS["green"] if chg_m >= 0 else COLORS["red"]
                chg_str = f'<font color="{color_m}"><b>{arrow} {chg_m:+.2f}%</b></font>'
            else:
                chg_str = "—"
            flat_row1.append(Paragraph(
                f'<font size="6" color="{COLORS["text_secondary"]}">{name}</font><br/>'
                f'<b>{val_str}</b><br/>{chg_str}', style_macro_val
            ))
        for name, val, chg_m, _ in MACRO_MAP[5:]:
            val_str = f"{val:,.2f}" if val else "N/D"
            if chg_m is not None:
                arrow = "&#9650;" if chg_m >= 0 else "&#9660;"
                color_m = COLORS["green"] if chg_m >= 0 else COLORS["red"]
                chg_str = f'<font color="{color_m}"><b>{arrow} {chg_m:+.2f}%</b></font>'
            else:
                chg_str = "—"
            flat_row2.append(Paragraph(
                f'<font size="6" color="{COLORS["text_secondary"]}">{name}</font><br/>'
                f'<b>{val_str}</b><br/>{chg_str}', style_macro_val
            ))

        macro_tbl = Table([flat_row1, flat_row2], colWidths=col_w_macro)
        macro_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), C_ACCENT_ALT),
            ("GRID", (0, 0), (-1, -1), 0.5, C_BORDER),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(macro_tbl)
        story.append(SP)

        # ── Resumen ejecutivo ──────────────────────────────────────────
        story.append(banner_h2("Resumen Ejecutivo"))
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
        story.append(SP)

        # ── Puntos clave ───────────────────────────────────────────────
        puntos = text.get("puntos_clave", [])
        if not puntos:
            # fallback a report_highlights si existen
            puntos = [f"• {h}" for h in analysis.get("report_highlights", [])]
        if puntos:
            story.append(banner_h2("Puntos Clave"))
            story.append(SP)
            pk_data = [[Paragraph(str(p), style_normal)] for p in puntos[:5]]
            pk_tbl = Table(pk_data, colWidths=[PAGE_W])
            pk_tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), C_ACCENT_ALT),
                ("BOX", (0, 0), (-1, -1), 0.5, C_BORDER),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("LINEBELOW", (0, 0), (-1, -2), 0.3, C_BORDER),
            ]))
            story.append(pk_tbl)

        story.append(PageBreak())

        # ══════════════════════════════════════════════════════════════════
        # PÁGINA 2: Mapa de calor + Contexto macro europeo
        # ══════════════════════════════════════════════════════════════════
        story.append(banner_h1("Mapa de Calor IBEX 35"))
        story.append(SP)

        if charts.get("heatmap") and os.path.exists(charts["heatmap"]):
            story.append(Image(charts["heatmap"], width=PAGE_W, height=10*cm))

        heatmap_meta = text.get("heatmap", {})
        _insight = heatmap_meta.get("insight_clave", "")
        _bad = ["consulte", "datos disponibles", "adjunt"]
        if _insight and not any(b in _insight.lower() for b in _bad):
            story.append(SP)
            story.append(Paragraph(_insight, style_small))

        heatmap_leyenda = heatmap_meta.get("leyenda", "")
        if heatmap_leyenda:
            story.append(Paragraph(heatmap_leyenda, style_caption))

        story.append(SP)
        story.append(banner_h2("Contexto Macro Europeo"))
        story.append(SP)

        macro_comparativas = text.get("contexto_macro_europeo", [])
        if macro_comparativas:
            comp_data = []
            for comp in macro_comparativas[:4]:
                comparacion = comp.get("comparacion", "")
                interpretacion = comp.get("interpretacion", "")
                if comparacion:
                    comp_data.append([Paragraph(
                        f'<b>&#9632; {comparacion}</b>  &#8594;  {interpretacion}',
                        style_small
                    )])
            if comp_data:
                comp_tbl = Table(comp_data, colWidths=[PAGE_W])
                comp_tbl.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, -1), C_ACCENT_ALT),
                    ("BOX", (0, 0), (-1, -1), 0.5, C_BORDER),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ("LEFTPADDING", (0, 0), (-1, -1), 10),
                    ("LINEBELOW", (0, 0), (-1, -2), 0.3, C_BORDER),
                ]))
                story.append(comp_tbl)
        else:
            # Fallback: usar divergence_signals del análisis
            macro_ctx = analysis.get("macro_context", {})
            div_signals = macro_ctx.get("divergence_signals", [])
            if div_signals:
                div_data = [[Paragraph(f"&#9632; {s}", style_small)] for s in div_signals]
                div_tbl = Table(div_data, colWidths=[PAGE_W])
                div_tbl.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, -1), C_ACCENT_ALT),
                    ("BOX", (0, 0), (-1, -1), 0.5, C_BORDER),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ("LEFTPADDING", (0, 0), (-1, -1), 10),
                    ("LINEBELOW", (0, 0), (-1, -2), 0.3, C_BORDER),
                ]))
                story.append(div_tbl)

        story.append(PageBreak())

        # ══════════════════════════════════════════════════════════════════
        # PÁGINA 3: Atribución + Análisis sectorial
        # ══════════════════════════════════════════════════════════════════
        story.append(banner_h1("Atribución del Movimiento del IBEX"))
        story.append(SP)

        if charts.get("point_contribution") and os.path.exists(charts["point_contribution"]):
            story.append(Image(charts["point_contribution"], width=PAGE_W, height=7*cm))

        atrib_text = text.get("atribucion_concentracion", "")
        if not atrib_text:
            movement = analysis.get("movement_attribution", {})
            atrib_text = movement.get("concentration", "")
        if atrib_text:
            story.append(SP)
            story.append(Paragraph(atrib_text, style_small))

        story.append(SP)
        story.append(banner_h1("Análisis Sectorial"))
        story.append(SP)

        if charts.get("sector_bar") and os.path.exists(charts["sector_bar"]):
            story.append(Image(charts["sector_bar"], width=PAGE_W, height=6*cm))

        sectorial_text = text.get("analisis_sectorial_texto", "")
        if sectorial_text:
            story.append(SP)
            story.append(Paragraph(sectorial_text, style_normal))

        story.append(PageBreak())

        # ══════════════════════════════════════════════════════════════════
        # PÁGINA 4: Tabla unificada IBEX 35
        # ══════════════════════════════════════════════════════════════════
        story.append(banner_h1("IBEX 35 — Datos Completos"))
        story.append(SP)

        valid_df = prices_df[prices_df["error"].isna() | (prices_df["error"] == "")].copy()
        valid_df["change_pct"] = pd.to_numeric(valid_df["change_pct"], errors="coerce")
        valid_df = valid_df.sort_values("change_pct", ascending=False)
        tickers_ind = (indicators or {}).get("tickers", {})

        # Cabecera: Ticker|Cierre|Var%|Vol(M)|Vol ratio|RSI 14|Pos 52W|Tend.|Señal
        # Anchos (cm): [1.8, 1.7, 1.6, 1.6, 1.8, 1.5, 1.7, 1.5, 3.8] = 17.0cm
        unified_col_w = [1.8*cm, 1.7*cm, 1.6*cm, 1.6*cm, 1.8*cm, 1.5*cm, 1.7*cm, 1.5*cm, 3.8*cm]
        unified_header = [
            Paragraph("Ticker", style_small_white),
            Paragraph("Cierre", style_small_white),
            Paragraph("Var%", style_small_white),
            Paragraph("Vol (M)", style_small_white),
            Paragraph("Vol ratio", style_small_white),
            Paragraph("RSI 14", style_small_white),
            Paragraph("Pos 52W", style_small_white),
            Paragraph("Tend.", style_small_white),
            Paragraph("Señal", style_small_white),
        ]
        unified_data = [unified_header]
        row_bg_unified = []
        cell_styles = []
        n_valid = len(valid_df)

        for idx, (_, row) in enumerate(valid_df.iterrows(), start=1):
            ticker = str(row.get("ticker", ""))
            ind = tickers_ind.get(ticker, {})
            chg = row.get("change_pct") or 0
            chg_val = float(chg) if pd.notna(chg) else 0.0
            chg_color = COLORS["green"] if chg_val >= 0 else COLORS["red"]
            close = row.get("close") or 0
            close_val = float(close) if pd.notna(close) else 0.0
            vol = row.get("volume") or 0
            vol_val = float(vol) if pd.notna(vol) else 0.0
            vol_ratio = ind.get("volume_ratio")
            vol_ratio_str = f"{vol_ratio:.1f}x" if vol_ratio is not None else "—"
            vol_bold = vol_ratio is not None and vol_ratio > 2.0
            rsi = ind.get("rsi_14")
            rsi_str = f"{rsi:.0f}" if rsi is not None else "—"
            range_pct = ind.get("range_52w_pct")
            range_str = f"{range_pct:.0f}%" if range_pct is not None else "—"
            macd_trend = ind.get("macd_trend", "")
            tend_icon = _tend_icon(macd_trend)
            tend_color = _tend_color(macd_trend)
            signal_txt, signal_color = _signal_text(rsi, macd_trend)

            # vol_ratio style
            vr_para = Paragraph(
                f'<b>{vol_ratio_str}</b>' if vol_bold else vol_ratio_str, style_small
            )

            unified_data.append([
                Paragraph(ticker.replace(".MC", ""), style_small),
                Paragraph(f"{close_val:.2f}", style_small),
                Paragraph(f'<font color="{chg_color}"><b>{chg_val:+.2f}%</b></font>', style_small),
                Paragraph(f"{vol_val/1_000_000:.1f}", style_small),
                vr_para,
                Paragraph(rsi_str, style_small),
                Paragraph(range_str, style_small),
                Paragraph(f'<font color="{tend_color}"><b>{tend_icon}</b></font>', style_small),
                Paragraph(f'<font color="{signal_color}"><b>{signal_txt}</b></font>', style_small),
            ])

            # Row background
            if idx <= 5:
                row_bg_unified.append(("BACKGROUND", (0, idx), (-1, idx), C_HIGHLIGHT_GREEN))
            elif idx >= n_valid - 4:
                row_bg_unified.append(("BACKGROUND", (0, idx), (-1, idx), C_HIGHLIGHT_RED))
            else:
                bg = C_LIGHT_GRAY if idx % 2 == 0 else C_WHITE
                row_bg_unified.append(("BACKGROUND", (0, idx), (-1, idx), bg))

            # Per-cell RSI coloring (AFTER row bg)
            if rsi is not None:
                rsi_col = 5  # columna RSI 14
                if rsi > 70:
                    cell_styles.append(("BACKGROUND", (rsi_col, idx), (rsi_col, idx), hex_to_reportlab("#FFCDD2")))
                elif rsi < 30:
                    cell_styles.append(("BACKGROUND", (rsi_col, idx), (rsi_col, idx), hex_to_reportlab("#C8E6C9")))

        unified_tbl = Table(unified_data, colWidths=unified_col_w, repeatRows=1)
        unified_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), C_PRIMARY),
            ("TEXTCOLOR", (0, 0), (-1, 0), C_WHITE),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 7),
            ("FONTSIZE", (0, 1), (-1, -1), 7),
            ("ALIGN", (1, 0), (-1, -1), "CENTER"),
            ("ALIGN", (0, 0), (0, -1), "LEFT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.3, C_BORDER),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ] + row_bg_unified + cell_styles))
        story.append(unified_tbl)
        story.append(SP)
        story.append(Paragraph(
            "Tend.: &#9650; MACD alcista | &#9660; bajista | &#8594; neutro. "
            "Vol ratio: volumen vs media 20d (negrita si >2x). RSI: fondo rojo >70, verde <30. "
            "Señal: (!) Sobrecompra >70 | Sobrevendido <30 | Alcista/Bajista por MACD | Neutro.",
            style_caption
        ))

        story.append(PageBreak())

        # ══════════════════════════════════════════════════════════════════
        # PÁGINA 5: Señales técnicas + Alertas de volumen
        # ══════════════════════════════════════════════════════════════════
        story.append(banner_h1("Señales Técnicas"))
        story.append(SP)

        signals = analysis.get("technical_signals", [])[:8]
        if signals:
            sig_header = [
                Paragraph("Ticker", style_small_white),
                Paragraph("Señal", style_small_white),
                Paragraph("RSI", style_small_white),
                Paragraph("Indicador clave", style_small_white),
                Paragraph("Nivel a vigilar", style_small_white),
                Paragraph("Comentario", style_small_white),
            ]
            sig_data = [sig_header]
            for s in signals:
                # Compatibilidad con schema antiguo (signal) y nuevo (signal_type)
                signal_label = s.get("signal_type") or s.get("signal") or ""
                rsi_val = s.get("rsi") or s.get("rsi_14")
                rsi_str = f"{rsi_val:.1f}" if isinstance(rsi_val, (int, float)) else "—"
                key_ind  = s.get("key_indicator") or s.get("price_vs_ma") or "—"
                level_w  = s.get("level_to_watch") or s.get("key_level") or "—"
                comment  = s.get("comment") or ""
                sig_data.append([
                    Paragraph(str(s.get("ticker") or "").replace(".MC", ""), style_small),
                    Paragraph(str(signal_label), style_small),
                    Paragraph(rsi_str, style_small),
                    Paragraph(str(key_ind)[:60], style_small),
                    Paragraph(str(level_w)[:40], style_small),
                    Paragraph(str(comment)[:120], style_small),
                ])
            row_bg_sig = []
            for i in range(1, len(sig_data)):
                bg = C_LIGHT_GRAY if i % 2 == 0 else C_WHITE
                row_bg_sig.append(("BACKGROUND", (0, i), (-1, i), bg))
            # Anchos: [1.5, 2.5, 1.0, 3.5, 2.8, 5.7] = 17.0cm
            sig_tbl = Table(sig_data, colWidths=[1.5*cm, 2.5*cm, 1.0*cm, 3.5*cm, 2.8*cm, 5.7*cm])
            sig_tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), C_PRIMARY),
                ("TEXTCOLOR", (0, 0), (-1, 0), C_WHITE),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("GRID", (0, 0), (-1, -1), 0.4, C_BORDER),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ] + row_bg_sig))
            story.append(sig_tbl)
        else:
            story.append(Paragraph("No se identificaron señales técnicas excepcionales en la sesión.", style_small))

        story.append(SP)
        story.append(banner_h1("Alertas de Volumen"))
        story.append(SP)

        volume_alerts = [a for a in analysis.get("volume_alerts", []) if (a.get("volume_ratio") or 0) > 2.0][:4]
        if volume_alerts:
            vol_header = [
                Paragraph("Ticker", style_small_white),
                Paragraph("Vol ratio", style_small_white),
                Paragraph("Var%", style_small_white),
                Paragraph("Lectura", style_small_white),
            ]
            vol_data = [vol_header]
            for alert in volume_alerts:
                chg_a = alert.get("change_pct", 0) or 0
                chg_color_a = COLORS["green"] if chg_a >= 0 else COLORS["red"]
                vr = alert.get("volume_ratio", 0) or 0
                vol_data.append([
                    Paragraph(f'<b>{str(alert.get("ticker","")).replace(".MC","")}</b>', style_small),
                    Paragraph(f'<font color="{COLORS["red"]}"><b>{vr:.1f}x</b></font>', style_small),
                    Paragraph(f'<font color="{chg_color_a}"><b>{chg_a:+.2f}%</b></font>', style_small),
                    Paragraph(str(alert.get("interpretation", ""))[:120], style_small),
                ])
            row_bg_vol = []
            for i in range(1, len(vol_data)):
                bg = C_LIGHT_GRAY if i % 2 == 0 else C_WHITE
                row_bg_vol.append(("BACKGROUND", (0, i), (-1, i), bg))
            # Anchos: [2.0, 2.2, 2.0, 10.8] = 17.0cm
            vol_tbl = Table(vol_data, colWidths=[2.0*cm, 2.2*cm, 2.0*cm, 10.8*cm])
            vol_tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), C_PRIMARY),
                ("TEXTCOLOR", (0, 0), (-1, 0), C_WHITE),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("GRID", (0, 0), (-1, -1), 0.4, C_BORDER),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ] + row_bg_vol))
            story.append(vol_tbl)
        else:
            story.append(Paragraph("Sin alertas de volumen inusual en la sesión (todos los valores por debajo de 2x la media 20 días).", style_small))

        story.append(PageBreak())

        # ══════════════════════════════════════════════════════════════════
        # PÁGINA 6: Ideas a vigilar + Noticias
        # ══════════════════════════════════════════════════════════════════
        story.append(banner_h1("Ideas a Vigilar"))
        story.append(SP)

        # Disclaimer compacto
        disc_ideas = (
            "<b>AVISO:</b> Las siguientes situaciones son análisis técnicos objetivos. "
            "No constituyen recomendaciones de inversión ni asesoramiento financiero."
        )
        disc_data = [[Paragraph(disc_ideas, style_small)]]
        disc_tbl = Table(disc_data, colWidths=[PAGE_W])
        disc_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), hex_to_reportlab("#FFF3CD")),
            ("BOX", (0, 0), (-1, -1), 1.0, hex_to_reportlab("#F0AD4E")),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ]))
        story.append(disc_tbl)
        story.append(SP)

        ideas = analysis.get("ideas_vigilar", [])[:3]
        for idea in ideas:
            ticker_short = str(idea.get("ticker") or idea.get("name", "")).replace(".MC", "")
            nombre = idea.get("nombre") or idea.get("name", "")
            setup = idea.get("setup_type") or idea.get("type", "")
            contexto    = idea.get("contexto") or idea.get("thesis", "")
            catalizador = idea.get("catalizador") or ""
            res = idea.get("resistencia") or idea.get("key_level") or "—"
            sop = idea.get("soporte") or "—"
            esc_al  = idea.get("escenario_alcista") or ""
            esc_baj = idea.get("escenario_bajista") or idea.get("risk_scenario", "")
            horizonte = idea.get("horizonte") or idea.get("timeframe", "")

            res_str = f"{res:.2f}€" if isinstance(res, float) else str(res)
            sop_str = f"{sop:.2f}€" if isinstance(sop, float) else str(sop)

            idea_rows = [
                [Paragraph(f'<b>{ticker_short} — {nombre}</b>  |  <i>{setup}</i>', style_banner_h2), None],
                [Paragraph(f'<b>Contexto:</b> {contexto}', style_small),
                 Paragraph(f'<b>Catalizador:</b> {catalizador}', style_small)],
                [Paragraph(f'<b>Niveles:</b> Resistencia {res_str}  |  Soporte {sop_str}', style_small),
                 Paragraph(f'<b>Horizonte:</b> {horizonte}', style_small)],
                [Paragraph(f'<b>Escenario alcista:</b> {esc_al}', style_small),
                 Paragraph(f'<b>Escenario bajista:</b> {esc_baj}', style_small)],
            ]
            idea_tbl = Table(idea_rows, colWidths=[PAGE_W * 0.55, PAGE_W * 0.45])
            idea_tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), C_SECONDARY_NEW),
                ("BACKGROUND", (0, 1), (-1, 1), C_ACCENT),
                ("BACKGROUND", (0, 2), (-1, 2), C_ACCENT_ALT),
                ("BACKGROUND", (0, 3), (-1, 3), hex_to_reportlab("#f0f4f8")),
                ("SPAN", (0, 0), (1, 0)),
                ("BOX", (0, 0), (-1, -1), 0.5, C_PRIMARY),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, C_BORDER),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
            story.append(idea_tbl)
            story.append(Spacer(1, 0.2*cm))

        if not ideas:
            story.append(Paragraph("No se identificaron setups de alta confluencia en la sesión.", style_small))

        story.append(SP)
        story.append(banner_h1("Noticias Relevantes"))
        story.append(SP)

        noticias = text.get("noticias", [])[:6]
        # Fallback a key_news_impact del análisis si el writer no generó noticias estructuradas
        if not noticias:
            for item in analysis.get("key_news_impact", [])[:6]:
                noticias.append({
                    "sentimiento": item.get("impact", "NEUTRO").upper(),
                    "titular": item.get("news_title", ""),
                    "impacto": item.get("analysis", "")[:100],
                })

        SENT_COLORS = {
            "POSITIVO": COLORS["badge_green"],
            "NEGATIVO": COLORS["badge_red"],
            "NEUTRO":   COLORS["badge_neutral"],
        }
        SENT_TEXT_COLORS = {
            "POSITIVO": COLORS["green"],
            "NEGATIVO": COLORS["red"],
            "NEUTRO":   "#444444",
        }
        for noticia in noticias:
            sentimiento = str(noticia.get("sentimiento", "NEUTRO")).upper()
            titular = str(noticia.get("titular", ""))
            impacto = str(noticia.get("impacto", ""))
            bg_sent = SENT_COLORS.get(sentimiento, COLORS["badge_neutral"])
            txt_sent = SENT_TEXT_COLORS.get(sentimiento, "#444444")
            news_rows = [
                [
                    Paragraph(f'<font color="{txt_sent}"><b>{sentimiento}</b></font>', style_small),
                    Paragraph(f'<b>{titular}</b>', style_small),
                ],
                [
                    Paragraph("", style_small),
                    Paragraph(f'&#8594; {impacto}', style_small),
                ],
            ]
            news_tbl = Table(news_rows, colWidths=[2.2*cm, PAGE_W - 2.2*cm])
            news_tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (0, -1), hex_to_reportlab(bg_sent)),
                ("BACKGROUND", (1, 0), (1, 0), C_ACCENT),
                ("BACKGROUND", (1, 1), (1, 1), C_ACCENT_ALT),
                ("BOX", (0, 0), (-1, -1), 0.5, C_BORDER),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, C_BORDER),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]))
            story.append(KeepTogether(news_tbl))
            story.append(Spacer(1, 0.1*cm))

        story.append(PageBreak())

        # ══════════════════════════════════════════════════════════════════
        # PÁGINA 7: Agenda económica + Conclusión
        # ══════════════════════════════════════════════════════════════════
        story.append(banner_h1("Agenda Económica"))
        story.append(SP)

        econ_cal = analysis.get("economic_calendar", {})
        agenda_clave = text.get("agenda_evento_clave", {})

        # Tabla de eventos
        IMPACT_COLORS = {"high": COLORS["red"], "medium": "#e67e22", "low": "#888888"}
        events = econ_cal.get("events_next_7d", [])[:10]
        if events:
            cal_header = [
                Paragraph("Fecha", style_small_white),
                Paragraph("País", style_small_white),
                Paragraph("Evento", style_small_white),
                Paragraph("Relev.", style_small_white),
                Paragraph("Impacto IBEX", style_small_white),
            ]
            cal_data = [cal_header]
            for ev in events:
                impact = ev.get("impact", "low")
                impact_color = IMPACT_COLORS.get(impact, "#888888")
                cal_data.append([
                    Paragraph(str(ev.get("date", "")), style_small),
                    Paragraph(str(ev.get("country", "")), style_small),
                    Paragraph(str(ev.get("event", "")), style_small),
                    Paragraph(f'<font color="{impact_color}"><b>{impact.upper()}</b></font>', style_small),
                    Paragraph(str(ev.get("ibex_impact_note", "")), style_small),
                ])
            row_bg_cal = []
            for i in range(1, len(cal_data)):
                bg = C_LIGHT_GRAY if i % 2 == 0 else C_WHITE
                row_bg_cal.append(("BACKGROUND", (0, i), (-1, i), bg))
            cal_tbl = Table(cal_data, colWidths=[2.2*cm, 1.3*cm, 4.5*cm, 1.6*cm, 7.4*cm])
            cal_tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), C_PRIMARY),
                ("TEXTCOLOR", (0, 0), (-1, 0), C_WHITE),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("GRID", (0, 0), (-1, -1), 0.4, C_BORDER),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ] + row_bg_cal))
            story.append(cal_tbl)
        elif not events and not agenda_clave.get("evento"):
            story.append(Paragraph("Calendario económico no disponible para los próximos días.", style_small))

        # Evento clave destacado
        if agenda_clave.get("evento"):
            story.append(SP)
            ke_data = [[
                Paragraph("<b>Evento clave</b>", style_small_white),
                Paragraph(f'<b>{agenda_clave["evento"]}</b><br/>{agenda_clave.get("contexto", "")}', style_small),
            ]]
            ke_tbl = Table(ke_data, colWidths=[3.0*cm, PAGE_W - 3.0*cm])
            ke_tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (0, 0), C_PRIMARY),
                ("BACKGROUND", (1, 0), (1, 0), C_ACCENT),
                ("BOX", (0, 0), (-1, -1), 0.5, C_PRIMARY),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
            story.append(ke_tbl)
        elif econ_cal.get("key_event_this_week"):
            story.append(SP)
            ke_data = [[
                Paragraph("<b>Evento clave</b>", style_small_white),
                Paragraph(econ_cal["key_event_this_week"], style_small),
            ]]
            ke_tbl = Table(ke_data, colWidths=[3.0*cm, PAGE_W - 3.0*cm])
            ke_tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (0, 0), C_PRIMARY),
                ("BACKGROUND", (1, 0), (1, 0), C_ACCENT),
                ("BOX", (0, 0), (-1, -1), 0.5, C_PRIMARY),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
            story.append(ke_tbl)

        story.append(SP)
        story.append(banner_h1("Conclusión"))
        story.append(SP)

        conclusion = text.get("conclusion", "")
        _bad_conclusion = ["consulte los datos", "consulte los gráficos", "ver tabla"]
        if not conclusion or any(b in conclusion.lower() for b in _bad_conclusion) or len(conclusion.split()) < 20:
            conclusion = (
                f"La sesión del IBEX 35 del {date_es} concluyó con los resultados reflejados en "
                f"los datos adjuntos. El seguimiento de los niveles técnicos del índice y la "
                f"evolución del contexto macro europeo será clave para las próximas sesiones."
            )
        story.append(Paragraph(conclusion, style_normal))
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
