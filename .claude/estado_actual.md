# Estado actual del sistema

> Última actualización: 2026-04-22

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

## Trabajo reciente completado (sesión 2026-04-22 — Fase 2 Premium)

- `requirements.txt` — añadidos: flask-jwt-extended, bcrypt, stripe, apscheduler
- `db/models.py` — nuevas tablas: `subscriptions` (Stripe), `alerts` (alertas técnicas); relaciones inversas en `User`
- `services/technical_analyzer.py` — nuevo: `analyze(symbol)` → SMA20, SMA50, RSI14, MACD, soporte, resistencia via yfinance
- `services/alerts_engine.py` — nuevo: worker APScheduler con SQLAlchemy job store; evalúa alertas a las 17:35 Madrid; falla claro si faltan env vars
- `api/flask_app.py` — ampliado con: auth JWT (`/auth/register`, `/auth/login`), endpoints PREMIUM protegidos (alertas, análisis técnico), webhooks Stripe (checkout.session.completed, subscription.deleted, invoice.payment_failed)
- `frontend/dashboard.html` — nuevo: SPA vanilla HTML/CSS/JS con auth, indicadores técnicos, gestión de alertas, botón de upgrade a Stripe

## Estado verificado end-to-end (sesión 2026-04-22)

Todos los endpoints de Fase 2 probados manualmente en local:

| Endpoint | Resultado |
|---|---|
| `POST /auth/register` | 201 + JWT |
| `POST /auth/login` | 200 + JWT |
| `GET /api/v1/technical/SAN.MC` | 200 + indicadores reales de yfinance |
| `POST /api/v1/alerts` | 201 + alerta creada en DB |
| `GET /api/v1/alerts` | 200 + lista de alertas |
| `POST /stripe/create-checkout` | 200 + URL de checkout real |
| Webhook `checkout.session.completed` | 200 + tier→premium en DB |
| DB tras pago | `tier: premium`, `status: active`, `stripe_subscription_id` guardado |

## Pendiente / Próximos pasos

- **Dashboard:** probar `frontend/dashboard.html` en el navegador con flujo completo de UI
- **Deploy Railway:** añadir las 4 variables nuevas de Fase 2 en el panel de Railway y configurar webhook real de Stripe apuntando al dominio de producción
- **Motor de alertas:** añadir como worker en Railway (`python services/alerts_engine.py`)
- **Fase 3:** backtester, fundamental_analyzer, portfolio_tracker, tier PRO

## Limitaciones conocidas (Fase 2)

- El `/register` legacy (Fase 1) usa werkzeug para el hash; `/auth/register` usa bcrypt — coexisten sin problema pero login detecta el formato del hash automáticamente
- `STRIPE_WEBHOOK_SECRET` cambia cada vez que arranca el proxy local de Stripe CLI — en producción es fijo (configurado en el dashboard de Stripe)
- El motor de alertas necesita `DATABASE_URL` y `SENDGRID_API_KEY` para arrancar — falla con error claro si faltan
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
