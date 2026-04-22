# Arquitectura del sistema

## Pipeline principal

```
GitHub Actions (17:35 Madrid, días laborables)
         │
         ▼
      main.py
    ┌────────────────────────────────────┐
    │         check_market_hours()       │
    │     (o FORCE_RUN=true para tests)  │
    └────────────────┬───────────────────┘
                     │
         ┌───────────▼───────────┐
         │   agents/leader.py    │  ← Orquestador (Opus)
         │   (Orquestador)       │
         └───────┬───────────────┘
                 │
        ┌────────┴────────┐
        │   (paralelo)    │
        ▼                 ▼
researcher.py        analyst.py
(Recopilador)        (Analista)
Sonnet               Sonnet
Read/Bash/WebFetch   Read/Bash/WebFetch
        │                 │
        └────────┬────────┘
                 │
                 ▼
           writer.py       ← Redactor (Sonnet) — único con Write
           (Redactor)
                 │
                 ▼
        output/informe_YYYY-MM-DD.pdf
                 │
                 ▼
         Validación final
         por leader.py (Opus)
                 │
                 ▼
         _run_newsletter()    ← añadido en Fase 1, no bloquea el pipeline
                 │
        ┌────────┴────────────────────┐
        │                             │
        ▼                             ▼
generate_newsletter_data()     Carga suscriptores
(writer.py)                    activos de PostgreSQL
        │                             │
        ▼                             │
data/analysis/                        │
newsletter_YYYY-MM-DD.json            │
                                      ▼
                             send_bulk_newsletter()
                             (SendGrid Personalizations)
                                      │
                                      ▼
                               Email a suscriptores
```

## Módulos Python

| Archivo | Rol | Descripción |
|---|---|---|
| `main.py` | Entry point | Controla horario, logging, directorios, orquesta el pipeline y lanza el newsletter |
| `agents/leader.py` | Orquestador | Coordina subagentes, valida el informe final |
| `agents/researcher.py` | Recopilador | Descarga precios, volúmenes y noticias vía yfinance/WebFetch |
| `agents/analyst.py` | Analista | Análisis técnico (RSI, medias, señales), macro, atribución sectorial |
| `agents/writer.py` | Redactor | Genera el informe PDF/HTML con gráficos; también expone `generate_newsletter_data()` |
| `agents/ibex_data.py` | Utilidad | Helpers para obtener datos del IBEX 35 y sus componentes |
| `agents/utils.py` | Utilidad | Funciones compartidas (logging, formato, limpieza de runs previos) |
| `db/models.py` | Base de datos | Modelos SQLAlchemy: `User`, `NewsletterSubscriber`, `Subscription`, `Alert`. Requiere `DATABASE_URL` (PostgreSQL) |
| `services/email_formatter.py` | Formateador | `format_newsletter_html()` → HTML mobile-friendly para el newsletter |
| `services/email_sender.py` | Envío email | `send_bulk_newsletter()` via SendGrid Personalizations API (batch, no loop) |
| `services/technical_analyzer.py` | Análisis técnico | `analyze(symbol)` → SMA20, SMA50, RSI14, MACD, soporte y resistencia via yfinance |
| `services/alerts_engine.py` | Motor de alertas | Worker APScheduler; evalúa alertas activas a las 17:35 Madrid y notifica por email |
| `api/flask_app.py` | API REST | Fase 1: `/register`, `/api/v1/newsletter/latest`, `/health`. Fase 2: auth JWT, alertas, análisis técnico, webhooks Stripe |
| `frontend/dashboard.html` | Dashboard web | SPA vanilla HTML/CSS/JS — auth, indicadores técnicos, gestión de alertas, upgrade a Premium |

## Datos y outputs

```
data/
  raw/          ← JSONs descargados por el Recopilador (precios, noticias)
  analysis/     ← JSONs procesados por el Analista (señales, métricas)
                   + newsletter_YYYY-MM-DD.json (generado por _run_newsletter)

output/         ← Informes finales (PDF o HTML), un archivo por día
logs/           ← Log de cada ejecución: run_YYYY-MM-DD.log
db/             ← Modelos SQLAlchemy (la DB real vive en PostgreSQL)
services/       ← Formateador HTML y sender de email
api/            ← API Flask
```

## Variables de entorno necesarias

| Variable | Obligatoria | Descripción |
|---|---|---|
| `ANTHROPIC_API_KEY` | Sí | Clave API de Anthropic |
| `DATABASE_URL` | Sí | URL PostgreSQL. Sin ella el newsletter se omite (no crashea el pipeline) |
| `SENDGRID_API_KEY` | Sí (newsletter) | Clave API de SendGrid |
| `SENDGRID_FROM_EMAIL` | Sí (newsletter) | Email remitente verificado en SendGrid |
| `JWT_SECRET_KEY` | Sí (API Fase 2) | Clave secreta para firmar tokens JWT — mínimo 32 chars |
| `STRIPE_SECRET_KEY` | Sí (pagos) | Clave secreta de Stripe (`sk_test_...` o `sk_live_...`) |
| `STRIPE_WEBHOOK_SECRET` | Sí (pagos) | Signing secret del webhook de Stripe (`whsec_...`) |
| `STRIPE_PREMIUM_PRICE_ID` | Sí (pagos) | Price ID del plan Premium en Stripe (`price_...`) |
| `STRIPE_SUCCESS_URL` | Opcional | URL de redirección tras pago exitoso (default: `/dashboard.html`) |
| `STRIPE_CANCEL_URL` | Opcional | URL de redirección si se cancela el pago (default: `/dashboard.html`) |
| `ALERTS_TIMEZONE` | Opcional | Timezone del motor de alertas (default: `Europe/Madrid`) |
| `ALERTS_HOUR` | Opcional | Hora de evaluación de alertas (default: `17`) |
| `ALERTS_MINUTE` | Opcional | Minuto de evaluación de alertas (default: `35`) |
| `FINNHUB_API_KEY` | Opcional | Para noticias adicionales |
| `FORCE_RUN` | Opcional | `true` para ignorar horario de mercado |

## Arquitectura de servicios (Fase 2)

```
┌─────────────────────────────────────────────────────────────┐
│                        API Flask                            │
│  /auth/register   /auth/login                               │
│  /api/v1/alerts   /api/v1/technical/<symbol>                │
│  /stripe/create-checkout   /stripe/webhook                  │
│  /api/v1/newsletter/latest   /health   /dashboard.html      │
└──────────────┬──────────────────────────┬───────────────────┘
               │                          │
               ▼                          ▼
        PostgreSQL DB              Stripe API
        (users, alerts,            (checkout sessions,
        subscriptions,             webhooks → tier premium)
        newsletter_subscribers)
               │
               ▼
     alerts_engine.py (worker)
     APScheduler 17:35 Madrid
     → technical_analyzer.py
     → email_sender.py (alertas)
```

**Workers independientes:**
- `python api/flask_app.py` — API REST (o gunicorn en producción)
- `python services/alerts_engine.py` — evaluación diaria de alertas

## Ejecución automática

- **GitHub Actions** dispara el workflow en días laborables a las 17:35 Madrid
- Variables de entorno necesarias: definidas en `.env` local o secrets de GitHub
- Para ejecutar fuera de horario: `FORCE_RUN=true python main.py`
- El motor de alertas corre como worker separado en Railway (no en GitHub Actions)

## Informe generado (estructura actual — 7 páginas)

1. Cabecera macro (10 indicadores)
2. Tabla resumen IBEX 35 (precio, variación, volumen, señal técnica)
3. Mapa de calor sectorial
4. Gráfico 52 semanas
5. Atribución de rentabilidad
6. Ideas de mercado
7. Calendario económico
