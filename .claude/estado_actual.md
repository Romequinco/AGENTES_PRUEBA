# Estado actual del sistema

> Última actualización: 2026-04-21

## Estado general: FUNCIONAL

El pipeline completo está operativo. GitHub Actions ejecuta el sistema diariamente en días laborables a las 17:35 (cierre IBEX 35). La capa de newsletter por email (Fase 1) está construida y verificada en local.

---

## Qué funciona

- Pipeline completo: Recopilador → Analista → Redactor → Validación
- Informe de 7 páginas con estructura institucional
- Gráfico de 52 semanas del IBEX
- Mapa de calor sectorial (con leyenda corregida)
- Limpieza automática de datos del run anterior al inicio de cada ejecución
- Ejecución fuera de horario con `FORCE_RUN=true`
- GitHub Actions con variables de entorno/secrets configurados
- **Newsletter por email (Fase 1):** pipeline completo probado en local y funcionando

## Trabajo reciente completado (sesión 2026-04-21 — Fase 1 Newsletter)

- `db/models.py` — modelos SQLAlchemy: tablas `users` y `newsletter_subscribers` con PostgreSQL
- `agents/writer.py` — añadida función `generate_newsletter_data(analysis_json)` fuera de la clase `WriterAgent`
- `services/email_formatter.py` — `format_newsletter_html()` genera HTML mobile-friendly con métricas, mejores/peores del día, idea del día y link de unsubscribe
- `services/email_sender.py` — `send_bulk_newsletter()` usa SendGrid Personalizations API (1 request por batch, no loop por destinatario)
- `main.py` — añadida `_run_newsletter()` que se ejecuta después del PDF, falla silenciosamente sin afectar el pipeline
- `api/flask_app.py` — API Flask mínima: `POST /register`, `GET /api/v1/newsletter/latest`, `GET /health`
- `requirements.txt` — añadidos: SQLAlchemy, psycopg2-binary, Flask, Werkzeug, sendgrid
- `.env.example` — añadidas variables `DATABASE_URL`, `SENDGRID_API_KEY`, `SENDGRID_FROM_EMAIL`
- Base de datos PostgreSQL local creada (`ibex_newsletter`) y tablas inicializadas
- Verificado end-to-end: PDF generado + newsletter JSON guardado + email enviado via SendGrid

## Pendiente / Próximos pasos

- **Fase 2:** Stripe (pagos), auth JWT, dashboard premium, alertas por ticker
- Las carpetas `.claude/agents/`, `.claude/skills/` y `.claude/hooks/` están vacías — los agentes Claude están implementados como módulos Python en `agents/`, no como definiciones `.md`
- No hay tests automatizados del output del informe (solo tests del código en `tests/`)
- Añadir test de humo para `generate_newsletter_data()` y el endpoint `/api/v1/newsletter/latest`

## Limitaciones conocidas

- GitHub Actions puede tener retrasos de 10-15 min en el cron (absorbido por la ventana 17:30–19:30)
- yfinance puede tardar 30-60 min tras el cierre en reflejar el OHLCV del día → `get_last_market_date()` maneja este edge case
- En las semanas de cambio de horario (marzo/octubre) ambos crons caen fuera de la estación correcta durante 1-2 días — efecto mínimo y autoresolutivo
- `DATABASE_URL` debe ser PostgreSQL (no SQLite) — Railway tiene filesystem efímero; SQLite perdería datos en cada redeploy
- El sender de SendGrid (`SENDGRID_FROM_EMAIL`) debe estar verificado en SendGrid antes del primer envío

## Cómo ejecutar en local

```bash
# Instalación
pip install -r requirements.txt
cp .env.example .env  # y rellenar variables

# Crear tablas en PostgreSQL (solo la primera vez)
python -c "from dotenv import load_dotenv; load_dotenv(); from db.models import create_tables; create_tables()"

# Ejecución normal (respeta horario de mercado)
python main.py

# Forzar ejecución fuera de horario (genera PDF + envía newsletter)
$env:FORCE_RUN="true"; python main.py  # PowerShell
FORCE_RUN=true python main.py          # bash/Linux

# Arrancar la API Flask
python api/flask_app.py
```
