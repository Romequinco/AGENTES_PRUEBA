# Estado actual del sistema

> Última actualización: 2026-04-28 — Auditoría de seguridad y rendimiento completada

## Estado general: EN PRODUCCIÓN

El sistema completo está desplegado y operativo en Railway. Pipeline diario, newsletter, tiers Premium y PRO, y toda la infraestructura de producción verificados. Auditoría de código completada con 8 correcciones aplicadas.

**URL de producción:** `https://web-production-6a82d.up.railway.app`

---

## Qué funciona (4 fases + auditoría)

### Pipeline principal (siempre activo)
- Recopilador → Analista → Redactor → Validación por el Orquestador
- Informe PDF de 7 páginas con estructura institucional
- Gráfico 52 semanas, mapa de calor sectorial, atribución sectorial
- GitHub Actions: ejecución automática días laborables al cierre del IBEX

### Capa de newsletter — Fase 1
- Modelos PostgreSQL: `User`, `NewsletterSubscriber`
- HTML mobile-friendly generado automáticamente
- Envío batch vía SendGrid Personalizations API
- API Flask: `/register`, `/api/v1/newsletter/latest`, `/health`

### Auth + Premium + Stripe — Fase 2
- JWT auth (`/auth/register`, `/auth/login`)
- Alertas técnicas (precio, RSI) con motor APScheduler
- Integración Stripe completa (checkout, webhooks, tiers)
- Dashboard SPA vanilla en `frontend/dashboard.html`

### Tier PRO — Fase 3
- Backtester determinista con estrategias JSON (límite 3/mes)
- Análisis fundamental via yfinance
- Portfolio tracker con P&L y benchmark IBEX
- Reporte semanal PDF bajo demanda
- 9 endpoints PRO en la API
- **48/48 tests passing**

### Producción — Fase 4
- `railway.toml` + `Procfile`: 2 servicios (web + worker)
- `gunicorn` como servidor WSGI de producción
- `services/monitoring.py`: `send_error_alert()` + `@monitor_errors`
- `/health` expandido: db, sendgrid, stripe, timestamp
- Job semanal: reportes PRO cada lunes 08:00 Madrid
- `/admin/metrics` protegido con `ADMIN_API_KEY`
- `frontend/admin_dashboard.html`: KPIs en tiempo real
- `DEPLOY.md`: guía paso a paso para Railway

### Auditoría de seguridad y rendimiento (2026-04-28)

8 problemas identificados y corregidos (problema 5 fue falsa alarma):

| # | Archivo | Corrección |
|---|---|---|
| 1 | `api/flask_app.py` | `JWT_SECRET_KEY` fail-fast: la app no arranca si no está definida o < 32 chars |
| 2 | `api/flask_app.py` | `JWT_ACCESS_TOKEN_EXPIRES = timedelta(days=30)` — tokens ya no son eternos |
| 3 | `api/flask_app.py` | Dead code eliminado: guard de `DATABASE_URL` en `__main__` era inalcanzable |
| 4 | `services/alerts_engine.py` | N+1 query → `joinedload(Alert.user)` en query de alertas activas |
| 6 | `agents/leader.py` | `global logger` mutado → `self.logger` como atributo de instancia |
| 7 | `api/helpers.py` + `services/portfolio_tracker.py` | `get_db()` duplicado → centralizado en `db/models.get_db_session()` |
| 8 | `main.py` | Excepción silenciada en `get_last_market_date()` → `logger.warning(...)` |
| 9 | `db/models.py` | Índices añadidos: `Alert.active` + `BacktestResult.ran_at` |

Índices creados en Railway PostgreSQL con `CREATE INDEX CONCURRENTLY IF NOT EXISTS`.

---

## Arquitectura de servicios

- **web**: `gunicorn api.flask_app:app` — API REST (Railway)
- **worker**: `python services/alerts_engine.py` — alertas diarias 17:35 + reportes lunes 08:00
- **GitHub Actions**: pipeline PDF + newsletter (17:35 Madrid, días laborables)

## Variables de entorno requeridas

Ver `DEPLOY.md` para la lista completa agrupada por servicio.

## Deploy realizado (2026-04-28)

- Proyecto creado en Railway y conectado al repo GitHub
- PostgreSQL provisionado y tablas creadas (`Base.metadata.create_all`)
- Variables de entorno configuradas en servicios `web` y `worker`
- Dominio generado: `web-production-6a82d.up.railway.app`
- Webhook de Stripe configurado: `POST /stripe/webhook` con 3 eventos
- `STRIPE_WEBHOOK_SECRET` actualizado en Railway con el signing secret real
- GitHub Actions secrets configurados (ANTHROPIC_API_KEY, DATABASE_URL, SENDGRID_*, GMAIL_*)
- `/health` verificado: `{"status":"ok","db":"connected","sendgrid":"configured","stripe":"configured"}`

## Próximos pasos

- Esperar primera ejecución automática (17:35 Madrid, día laborable) para verificar pipeline completo
- Comprobar logs del worker en Railway → servicio `worker` → Logs
- Opcional: forzar pipeline ahora desde GitHub → Actions → Run workflow

## Limitaciones conocidas

- GitHub Actions puede tener retrasos de 10-15 min en el cron
- yfinance tarda 30-60 min tras el cierre en reflejar OHLCV del día
- `DATABASE_URL` debe ser PostgreSQL — Railway tiene filesystem efímero
- `SENDGRID_FROM_EMAIL` debe estar verificado en SendGrid antes del primer envío
- `STRIPE_WEBHOOK_SECRET` es fijo en producción (configurado en dashboard Stripe)
- Tokens JWT expiran en 30 días — el frontend no implementa refresh automático (ver `DEPLOY.md`)
