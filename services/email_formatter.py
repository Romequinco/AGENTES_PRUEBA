"""Formatea el newsletter diario del IBEX 35 como HTML mobile-friendly."""

from __future__ import annotations

_SENTIMENT_LABEL = {
    "alcista": ("▲", "#27ae60"),
    "bajista": ("▼", "#c0392b"),
    "neutral": ("◆", "#5a6977"),
}


def format_newsletter_html(newsletter_data: dict, unsubscribe_url: str = "#unsubscribe") -> str:
    fecha = newsletter_data.get("fecha", "")
    ibex_cierre = newsletter_data.get("ibex_cierre")
    cambio_pct = newsletter_data.get("cambio_pct")
    sentimiento = newsletter_data.get("sentimiento", "neutral").lower()
    resumen = newsletter_data.get("resumen", "")
    top_ganadores = newsletter_data.get("top_3_ganadores", [])
    top_perdedores = newsletter_data.get("top_3_perdedores", [])
    idea_dia = newsletter_data.get("idea_dia")

    sent_icon, sent_color = _SENTIMENT_LABEL.get(sentimiento, _SENTIMENT_LABEL["neutral"])
    cambio_str = f"{cambio_pct:+.2f}%" if cambio_pct is not None else "–"
    cierre_str = f"{ibex_cierre:,.1f}" if ibex_cierre is not None else "–"
    cambio_color = "#27ae60" if (cambio_pct or 0) >= 0 else "#c0392b"

    def _row(item: dict, color: str) -> str:
        pct = item.get("change_pct", 0)
        sign = "+" if pct >= 0 else ""
        return (
            f'<tr>'
            f'<td style="padding:6px 8px;font-weight:600;">{item.get("name","")}</td>'
            f'<td style="padding:6px 8px;color:#5a6977;font-size:13px;">{item.get("ticker","")}</td>'
            f'<td style="padding:6px 8px;text-align:right;font-weight:700;color:{color};">{sign}{pct:.2f}%</td>'
            f'</tr>'
        )

    ganadores_rows = "".join(_row(i, "#27ae60") for i in top_ganadores)
    perdedores_rows = "".join(_row(i, "#c0392b") for i in top_perdedores)

    idea_block = ""
    if idea_dia:
        idea_block = f"""
        <tr><td colspan="2" style="padding:0 0 8px;">
          <div style="background:#f4f6f8;border-left:4px solid #2c5282;padding:12px 16px;border-radius:0 6px 6px 0;">
            <div style="font-weight:700;color:#1a2332;margin-bottom:4px;">
              {idea_dia.get('nombre','')} ({idea_dia.get('ticker','')})
            </div>
            <div style="font-size:13px;color:#2c5282;margin-bottom:6px;">{idea_dia.get('setup','')}</div>
            <div style="font-size:12px;color:#5a6977;line-height:1.5;">{idea_dia.get('contexto','')[:300]}…</div>
          </div>
        </td></tr>
        """

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>IBEX 35 Cierre · {fecha}</title>
</head>
<body style="margin:0;padding:0;background:#f0f2f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">

  <!-- wrapper -->
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f2f5;">
  <tr><td align="center" style="padding:24px 16px;">

    <!-- card -->
    <table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.08);">

      <!-- header -->
      <tr>
        <td style="background:#1a2332;padding:24px 28px;">
          <div style="color:#ffffff;font-size:22px;font-weight:700;letter-spacing:-.3px;">IBEX 35 · Cierre del día</div>
          <div style="color:#8fa3bb;font-size:13px;margin-top:4px;">{fecha}</div>
        </td>
      </tr>

      <!-- main metric -->
      <tr>
        <td style="padding:24px 28px 0;border-bottom:1px solid #e2e8f0;">
          <table width="100%" cellpadding="0" cellspacing="0">
          <tr>
            <td>
              <div style="font-size:36px;font-weight:800;color:#1a2332;letter-spacing:-1px;">{cierre_str}</div>
              <div style="font-size:20px;font-weight:700;color:{cambio_color};margin-top:2px;">{cambio_str}</div>
            </td>
            <td align="right" valign="top">
              <div style="display:inline-block;background:#e8edf3;border-radius:20px;padding:6px 16px;font-size:15px;font-weight:600;color:{sent_color};">
                {sent_icon} {sentimiento.capitalize()}
              </div>
            </td>
          </tr>
          <tr><td colspan="2" style="padding:16px 0;">
            <div style="font-size:14px;color:#4a5568;line-height:1.6;">{resumen}</div>
          </td></tr>
          </table>
        </td>
      </tr>

      <!-- movers -->
      <tr>
        <td style="padding:20px 28px 0;">
          <table width="100%" cellpadding="0" cellspacing="0">
          <tr>
            <!-- gainers -->
            <td width="48%" valign="top">
              <div style="font-size:13px;font-weight:700;color:#27ae60;text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px;">▲ Mejores</div>
              <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
                {ganadores_rows}
              </table>
            </td>
            <td width="4%"></td>
            <!-- losers -->
            <td width="48%" valign="top">
              <div style="font-size:13px;font-weight:700;color:#c0392b;text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px;">▼ Peores</div>
              <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
                {perdedores_rows}
              </table>
            </td>
          </tr>
          </table>
        </td>
      </tr>

      <!-- idea del día -->
      {'<tr><td style="padding:20px 28px 0;"><div style="font-size:13px;font-weight:700;color:#1a2332;text-transform:uppercase;letter-spacing:.5px;margin-bottom:10px;">💡 Idea del día</div><table width="100%" cellpadding="0" cellspacing="0">' + idea_block + '</table></td></tr>' if idea_dia else ''}

      <!-- footer -->
      <tr>
        <td style="padding:24px 28px;background:#f8f9fa;border-top:1px solid #e2e8f0;margin-top:20px;">
          <div style="font-size:12px;color:#8fa3bb;text-align:center;line-height:1.6;">
            Informe automático generado por el sistema de análisis IBEX 35.<br>
            <a href="{unsubscribe_url}" style="color:#8fa3bb;text-decoration:underline;">Cancelar suscripción</a>
          </div>
        </td>
      </tr>

    </table>
    <!-- /card -->

  </td></tr>
  </table>
  <!-- /wrapper -->

</body>
</html>"""
