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
            analysis, prices_df, indicators = self.load_analysis()
            text = self.generate_text(analysis)
            charts = self.generate_charts(prices_df, analysis, indicators)
            pdf_path = self.build_pdf(text, charts, analysis, prices_df, indicators)
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
        return analysis, df, indicators

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
            "titulares_candidatos": [],
            "resumen_ejecutivo": "Informe generado automáticamente. Consulte los datos adjuntos.",
            "narrativa_mercado": "Datos de mercado disponibles en la tabla adjunta.",
            "narrativa_macro": "Contexto macro europeo disponible en los datos adjuntos.",
            "narrativa_atribucion": "Atribución del movimiento disponible en el gráfico adjunto.",
            "narrativa_volumen": "",
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
            "disclaimer": "Este informe ha sido generado de forma automatizada con fines meramente informativos y no constituye asesoramiento financiero ni recomendación de inversión. Las secciones 'Ideas a vigilar' no son recomendaciones de compra o venta.",
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

        placeholder_movers = os.path.join(self.charts_dir, "top_movers_placeholder.png")
        charts["top_movers"] = self._chart_top_movers(valid) or self._chart_placeholder(placeholder_movers, "top movers")

        placeholder_sector = os.path.join(self.charts_dir, "sector_bar_placeholder.png")
        charts["sector_bar"] = self._chart_sector_bar(analysis) or self._chart_placeholder(placeholder_sector, "sectores")

        placeholder_volume = os.path.join(self.charts_dir, "volume_bar_placeholder.png")
        charts["volume_bar"] = self._chart_volume_bar(valid) or self._chart_placeholder(placeholder_volume, "volumen")

        # Nuevos gráficos
        if indicators:
            placeholder_contrib = os.path.join(self.charts_dir, "contribution_placeholder.png")
            charts["point_contribution"] = (
                self._chart_point_contribution(indicators, analysis)
                or self._chart_placeholder(placeholder_contrib, "atribución de movimiento")
            )

            placeholder_range = os.path.join(self.charts_dir, "range_placeholder.png")
            charts["range_52w"] = (
                self._chart_range_52w(indicators)
                or self._chart_placeholder(placeholder_range, "rango 52 semanas")
            )

            placeholder_macro = os.path.join(self.charts_dir, "macro_placeholder.png")
            charts["macro_comparison"] = (
                self._chart_macro_comparison(analysis)
                or self._chart_placeholder(placeholder_macro, "comparativa europea")
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

            fig, ax = plt.subplots(figsize=(12, 9), facecolor="white")
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

    def build_pdf(self, text: dict, charts: dict, analysis: dict, prices_df: pd.DataFrame, indicators: dict = None) -> str:
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
        # Contexto Macro Europeo (Bloque A)
        # ══════════════════════════════════════════════════════════════════
        macro_ctx = analysis.get("macro_context", {})
        if macro_ctx:
            story.append(SP)
            story.append(banner_h1("Contexto Macro Europeo"))
            story.append(SP)
            if text.get("narrativa_macro"):
                story.append(Paragraph(text["narrativa_macro"], style_normal))
                story.append(SP)
            if charts.get("macro_comparison") and os.path.exists(charts["macro_comparison"]):
                story.append(Image(charts["macro_comparison"], width=PAGE_W, height=6*cm))
                story.append(Paragraph("Síntesis del contexto macroeconómico europeo del día", style_caption))
            # Tabla compacta de señales divergentes
            div_signals = macro_ctx.get("divergence_signals", [])
            if div_signals:
                story.append(SP)
                div_data = [[Paragraph(f"⚡ {s}", style_small)] for s in div_signals]
                div_tbl = Table(div_data, colWidths=[PAGE_W])
                div_tbl.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, -1), C_ACCENT),
                    ("BOX", (0, 0), (-1, -1), 0.5, C_PRIMARY),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ("LEFTPADDING", (0, 0), (-1, -1), 10),
                    ("LINEBELOW", (0, 0), (-1, -2), 0.3, C_BORDER),
                ]))
                story.append(div_tbl)

        # ══════════════════════════════════════════════════════════════════
        # Atribución del movimiento (Bloque B)
        # ══════════════════════════════════════════════════════════════════
        movement = analysis.get("movement_attribution", {})
        if movement:
            story.append(SP)
            story.append(banner_h1("Atribución del movimiento del IBEX"))
            story.append(SP)
            if text.get("narrativa_atribucion"):
                story.append(Paragraph(text["narrativa_atribucion"], style_normal))
                story.append(SP)
            if charts.get("point_contribution") and os.path.exists(charts["point_contribution"]):
                story.append(Image(charts["point_contribution"], width=PAGE_W, height=8*cm))
                story.append(Paragraph(
                    f"Contribución en puntos al movimiento del IBEX. "
                    f"{movement.get('concentration', '')}",
                    style_caption
                ))

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
                Paragraph(str(row.get("name") or "")[:22], style_small),
                Paragraph(str(row.get("ticker") or "").replace(".MC",""), style_small),
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
        # Posición en rango 52 semanas (Bloque D)
        # ══════════════════════════════════════════════════════════════════
        story.append(SP)
        story.append(banner_h1("Posición en rango 52 semanas"))
        story.append(SP)
        if charts.get("range_52w") and os.path.exists(charts["range_52w"]):
            story.append(Image(charts["range_52w"], width=PAGE_W, height=11*cm))
            story.append(Paragraph(
                "Posición de cada valor dentro de su rango de 52 semanas. 0% = mínimo anual, 100% = máximo anual. "
                "Verde: cerca de máximos | Naranja: zona media | Rojo: cerca de mínimos.",
                style_caption
            ))
        range_ext = analysis.get("range_extremes", {})
        near_high = range_ext.get("near_52w_high", [])
        near_low = range_ext.get("near_52w_low", [])
        if near_high or near_low:
            story.append(SP)
            ext_rows = []
            if near_high:
                for item in near_high:
                    ext_rows.append([
                        Paragraph(f'<font color="{COLORS["green"]}">▲</font> <b>Cerca máximo ({item.get("range_52w_pct", 0):.0f}%)</b>: {item.get("ticker", "").replace(".MC","")} — {item.get("name", "")}', style_small),
                        Paragraph(item.get("comment", ""), style_small),
                    ])
            if near_low:
                for item in near_low:
                    ext_rows.append([
                        Paragraph(f'<font color="{COLORS["red"]}">▼</font> <b>Cerca mínimo ({item.get("range_52w_pct", 0):.0f}%)</b>: {item.get("ticker", "").replace(".MC","")} — {item.get("name", "")}', style_small),
                        Paragraph(item.get("comment", ""), style_small),
                    ])
            if ext_rows:
                ext_tbl = Table(ext_rows, colWidths=[7*cm, PAGE_W - 7*cm])
                ext_tbl.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, -1), C_ACCENT_ALT),
                    ("BOX", (0, 0), (-1, -1), 0.5, C_BORDER),
                    ("INNERGRID", (0, 0), (-1, -1), 0.3, C_BORDER),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]))
                story.append(ext_tbl)

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
                    Paragraph(str(g.get("name") or ""), style_small) if g else Paragraph("", style_small),
                    Paragraph(f'<font color="{COLORS["green"]}"><b>+{g_chg:.2f}%</b></font>', style_small) if g else Paragraph("", style_small),
                    Paragraph("", style_small),
                    Paragraph(str(l.get("name") or ""), style_small) if l else Paragraph("", style_small),
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

        # Alertas de volumen inusual (Bloque C)
        volume_alerts = analysis.get("volume_alerts", [])
        if volume_alerts:
            story.append(SP)
            story.append(banner_h2("Alertas de volumen inusual"))
            story.append(SP)
            if text.get("narrativa_volumen"):
                story.append(Paragraph(text["narrativa_volumen"], style_normal))
                story.append(SP)
            vol_header = [
                Paragraph("Ticker", style_small_white),
                Paragraph("Ratio vs media 20d", style_small_white),
                Paragraph("Var% hoy", style_small_white),
                Paragraph("Interpretación", style_small_white),
            ]
            vol_data = [vol_header]
            for alert in volume_alerts:
                chg = alert.get("change_pct", 0) or 0
                chg_color = COLORS["green"] if chg >= 0 else COLORS["red"]
                signal = alert.get("volume_signal", "elevated")
                signal_color = COLORS["red"] if signal == "high" else "#e67e22"
                vol_data.append([
                    Paragraph(f'<b>{str(alert.get("ticker","")).replace(".MC","")}</b>', style_small),
                    Paragraph(f'<font color="{signal_color}"><b>{alert.get("volume_ratio", 0):.1f}x</b></font> ({signal})', style_small),
                    Paragraph(f'<font color="{chg_color}"><b>{chg:+.2f}%</b></font>', style_small),
                    Paragraph(str(alert.get("interpretation", "")), style_small),
                ])
            row_bg_vol = []
            for i in range(1, len(vol_data)):
                bg = C_LIGHT_GRAY if i % 2 == 0 else C_WHITE
                row_bg_vol.append(("BACKGROUND", (0, i), (-1, i), bg))
            vol_tbl = Table(vol_data, colWidths=[2*cm, 3*cm, 2*cm, PAGE_W - 7*cm])
            vol_tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), C_PRIMARY),
                ("TEXTCOLOR", (0, 0), (-1, 0), C_WHITE),
                ("GRID", (0, 0), (-1, -1), 0.4, C_BORDER),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ] + row_bg_vol))
            story.append(vol_tbl)

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
                    Paragraph(str(s.get("ticker") or "").replace(".MC", ""), style_small),
                    Paragraph(str(s.get("signal") or ""), style_small),
                    Paragraph(rsi_str, style_small),
                    Paragraph(str(s.get("comment") or ""), style_small),
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
        # Ideas a vigilar (Bloque H)
        # ══════════════════════════════════════════════════════════════════
        actionable = analysis.get("actionable_ideas", [])
        if actionable:
            story.append(SP)
            story.append(banner_h1("Ideas a vigilar"))
            story.append(SP)
            # Disclaimer prominente
            disclaimer_ideas = (
                "<b>AVISO IMPORTANTE:</b> Las siguientes situaciones son análisis técnicos objetivos para seguimiento. "
                "<b>No constituyen recomendaciones de inversión</b> ni asesoramiento financiero. "
                "La inversión en bolsa conlleva riesgo de pérdida de capital."
            )
            disc_data = [[Paragraph(disclaimer_ideas, style_small)]]
            disc_tbl = Table(disc_data, colWidths=[PAGE_W])
            disc_tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), hex_to_reportlab("#FFF3CD")),
                ("BOX", (0, 0), (-1, -1), 1.0, hex_to_reportlab("#F0AD4E")),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ]))
            story.append(disc_tbl)
            story.append(SP)

            TYPE_LABELS = {
                "technical_rebound": "Rebote técnico",
                "breakout": "Breakout",
                "breakdown_watch": "Vigilar ruptura",
                "fundamental_catalyst": "Catalizador fundamental",
            }
            for idea in actionable[:3]:
                idea_type = idea.get("type", "")
                type_label = TYPE_LABELS.get(idea_type, idea_type)
                ticker_short = str(idea.get("ticker", "")).replace(".MC", "")
                idea_rows = [
                    [
                        Paragraph(f'<b>{ticker_short}</b> — {idea.get("name", "")}', style_banner_h2),
                        Paragraph(f'<i>{type_label}</i>', style_banner_h2),
                    ],
                    [
                        Paragraph(f'<b>Tesis:</b> {idea.get("thesis", "")}', style_small),
                        Paragraph(f'<b>Nivel clave:</b> {idea.get("key_level", "")}', style_small),
                    ],
                    [
                        Paragraph(f'<b>Escenario de riesgo:</b> {idea.get("risk_scenario", "")}', style_small),
                        Paragraph(f'<b>Horizonte temporal:</b> {idea.get("timeframe", "")}', style_small),
                    ],
                ]
                idea_tbl = Table(idea_rows, colWidths=[PAGE_W * 0.6, PAGE_W * 0.4])
                idea_tbl.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), C_SECONDARY),
                    ("BACKGROUND", (0, 1), (-1, 1), C_ACCENT),
                    ("BACKGROUND", (0, 2), (-1, 2), C_ACCENT_ALT),
                    ("BOX", (0, 0), (-1, -1), 0.5, C_PRIMARY),
                    ("INNERGRID", (0, 0), (-1, -1), 0.3, C_BORDER),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("SPAN", (0, 0), (0, 0)),
                ]))
                story.append(idea_tbl)
                story.append(Spacer(1, 0.2*cm))

        # ══════════════════════════════════════════════════════════════════
        # Tabla resumen completa — IBEX 35 de un vistazo (Bloque E)
        # ══════════════════════════════════════════════════════════════════
        if indicators:
            tickers_ind = indicators.get("tickers", {})
            if tickers_ind:
                story.append(PageBreak())
                story.append(banner_h1("IBEX 35 — Tabla resumen de un vistazo"))
                story.append(SP)
                caption_table = (
                    "Todos los valores del IBEX 35. Color: verde = subida >+1%, rojo = bajada >−1%, gris = movimiento leve. "
                    "Tendencia basada en señal MACD. Pos. 52W = posición en el rango anual."
                )
                story.append(Paragraph(caption_table, style_caption))
                story.append(SP)

                valid_sum = prices_df[prices_df["error"].isna() | (prices_df["error"] == "")].copy()
                valid_sum["change_pct"] = pd.to_numeric(valid_sum["change_pct"], errors="coerce")
                valid_sum = valid_sum.sort_values("change_pct", ascending=False)

                sum_header = [
                    Paragraph("Empresa", style_small_white),
                    Paragraph("Ticker", style_small_white),
                    Paragraph("Cierre", style_small_white),
                    Paragraph("Var%", style_small_white),
                    Paragraph("RSI", style_small_white),
                    Paragraph("MA20", style_small_white),
                    Paragraph("Vol ratio", style_small_white),
                    Paragraph("Pos 52W%", style_small_white),
                    Paragraph("Tend.", style_small_white),
                ]
                sum_data = [sum_header]
                C_SUM_GREEN = hex_to_reportlab("#d5f5e3")
                C_SUM_RED = hex_to_reportlab("#fdecea")

                row_colors_sum = []
                for idx, (_, row) in enumerate(valid_sum.iterrows(), start=1):
                    ticker = str(row.get("ticker", ""))
                    ind = tickers_ind.get(ticker, {})
                    chg = row.get("change_pct", 0) or 0
                    chg_color = COLORS["green"] if chg >= 0 else COLORS["red"]
                    rsi = ind.get("rsi_14")
                    rsi_str = f"{rsi:.0f}" if rsi is not None else "—"
                    ma20 = ind.get("ma_20")
                    close = row.get("close", 0) or 0
                    ma20_signal = "▲" if (ma20 and close > ma20) else ("▼" if ma20 else "—")
                    ma20_color = COLORS["green"] if ma20_signal == "▲" else COLORS["red"]
                    vol_ratio = ind.get("volume_ratio")
                    vol_str = f"{vol_ratio:.1f}x" if vol_ratio is not None else "—"
                    vol_color = COLORS["red"] if (vol_ratio and vol_ratio >= 2.0) else (
                        "#e67e22" if (vol_ratio and vol_ratio >= 1.5) else COLORS["text"]
                    )
                    range_pct = ind.get("range_52w_pct")
                    range_str = f"{range_pct:.0f}%" if range_pct is not None else "—"
                    macd_trend = ind.get("macd_trend", "")
                    tend_icon = "▲" if macd_trend == "alcista" else ("▼" if macd_trend == "bajista" else "→")
                    tend_color = COLORS["green"] if tend_icon == "▲" else (COLORS["red"] if tend_icon == "▼" else "#888888")

                    sum_data.append([
                        Paragraph(str(row.get("name") or "")[:20], style_small),
                        Paragraph(ticker.replace(".MC", ""), style_small),
                        Paragraph(f"{close:.2f}", style_small),
                        Paragraph(f'<font color="{chg_color}"><b>{chg:+.2f}%</b></font>', style_small),
                        Paragraph(rsi_str, style_small),
                        Paragraph(f'<font color="{ma20_color}"><b>{ma20_signal}</b></font>', style_small),
                        Paragraph(f'<font color="{vol_color}">{vol_str}</font>', style_small),
                        Paragraph(range_str, style_small),
                        Paragraph(f'<font color="{tend_color}"><b>{tend_icon}</b></font>', style_small),
                    ])
                    if chg > 1.0:
                        row_colors_sum.append(("BACKGROUND", (0, idx), (-1, idx), C_SUM_GREEN))
                    elif chg < -1.0:
                        row_colors_sum.append(("BACKGROUND", (0, idx), (-1, idx), C_SUM_RED))
                    else:
                        row_colors_sum.append(("BACKGROUND", (0, idx), (-1, idx), C_LIGHT_GRAY if idx % 2 == 0 else C_WHITE))

                col_w_sum = [4.0*cm, 1.6*cm, 1.6*cm, 1.6*cm, 1.2*cm, 1.4*cm, 1.8*cm, 1.8*cm, 1.4*cm]
                sum_tbl = Table(sum_data, colWidths=col_w_sum, repeatRows=1)
                sum_tbl.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), C_PRIMARY),
                    ("TEXTCOLOR", (0, 0), (-1, 0), C_WHITE),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, 0), 7),
                    ("ALIGN", (2, 0), (-1, -1), "CENTER"),
                    ("ALIGN", (0, 0), (1, -1), "LEFT"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("GRID", (0, 0), (-1, -1), 0.3, C_BORDER),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ] + row_colors_sum))
                story.append(sum_tbl)
                story.append(SP)
                story.append(Paragraph(
                    "Leyenda: Tend. ▲ = MACD alcista | ▼ = MACD bajista | → = neutro. "
                    "Vol ratio = volumen hoy vs media 20 días. Pos 52W% = posición en rango anual.",
                    style_caption
                ))

        # ══════════════════════════════════════════════════════════════════
        # Agenda económica próximos días (Bloque F)
        # ══════════════════════════════════════════════════════════════════
        econ_cal = analysis.get("economic_calendar", {})
        if econ_cal and econ_cal.get("events_next_7d"):
            story.append(SP)
            story.append(banner_h1("Agenda económica — próximos días"))
            story.append(SP)
            if econ_cal.get("key_event_this_week"):
                key_event_data = [[
                    Paragraph("<b>Evento clave</b>", style_small_white),
                    Paragraph(econ_cal["key_event_this_week"], style_small),
                ]]
                ke_tbl = Table(key_event_data, colWidths=[3.5*cm, PAGE_W - 3.5*cm])
                ke_tbl.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (0, 0), C_PRIMARY),
                    ("BACKGROUND", (1, 0), (1, 0), C_ACCENT),
                    ("BOX", (0, 0), (-1, -1), 0.5, C_PRIMARY),
                    ("INNERGRID", (0, 0), (-1, -1), 0.3, C_BORDER),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ]))
                story.append(ke_tbl)
                story.append(SP)
            if text.get("narrativa_agenda"):
                story.append(Paragraph(text["narrativa_agenda"], style_normal))
                story.append(SP)

            IMPACT_COLORS = {"high": COLORS["red"], "medium": "#e67e22", "low": "#888888"}
            cal_header = [
                Paragraph("Fecha", style_small_white),
                Paragraph("País", style_small_white),
                Paragraph("Evento", style_small_white),
                Paragraph("Relevancia", style_small_white),
                Paragraph("Impacto para IBEX", style_small_white),
            ]
            cal_data = [cal_header]
            for ev in econ_cal["events_next_7d"][:10]:
                impact = ev.get("impact", "low")
                impact_color = IMPACT_COLORS.get(impact, "#888888")
                sectors_affected = ", ".join(ev.get("ibex_sectors_affected", []))
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
            cal_tbl = Table(cal_data, colWidths=[2.2*cm, 1.3*cm, 4.5*cm, 1.8*cm, PAGE_W - 9.8*cm])
            cal_tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), C_PRIMARY),
                ("TEXTCOLOR", (0, 0), (-1, 0), C_WHITE),
                ("GRID", (0, 0), (-1, -1), 0.4, C_BORDER),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ] + row_bg_cal))
            story.append(cal_tbl)

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
