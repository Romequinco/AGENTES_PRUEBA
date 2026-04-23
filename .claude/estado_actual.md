# Estado actual del sistema

> Última actualización: 2026-04-23 — Fase 4 completa

## Estado general: LISTO PARA PRODUCCIÓN

El sistema completo está operativo en local y preparado para deploy en Railway. Pipeline diario, newsletter, tiers Premium y PRO, y toda la infraestructura de producción están implementados y verificados.

---

## Qué funciona (4 fases)

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

---

## Arquitectura de servicios

- **web**: `gunicorn api.flask_app:app` — API REST (Railway)
- **worker**: `python services/alerts_engine.py` — alertas diarias 17:35 + reportes lunes 08:00
- **GitHub Actions**: pipeline PDF + newsletter (17:35 Madrid, días laborables)

## Variables de entorno requeridas

Ver `DEPLOY.md` para la lista completa agrupada por servicio.

## Próximos pasos

1. Ejecutar `DEPLOY.md` paso a paso en Railway
2. Añadir `DATABASE_URL`, `SENDGRID_API_KEY`, `SENDGRID_FROM_EMAIL` a GitHub Secrets
3. Verificar `/health` en el dominio `.railway.app`

## Limitaciones conocidas

- GitHub Actions puede tener retrasos de 10-15 min en el cron
- yfinance tarda 30-60 min tras el cierre en reflejar OHLCV del día
- `DATABASE_URL` debe ser PostgreSQL — Railway tiene filesystem efímero
- `SENDGRID_FROM_EMAIL` debe estar verificado en SendGrid antes del primer envío
- `STRIPE_WEBHOOK_SECRET` es fijo en producción (configurado en dashboard Stripe)
