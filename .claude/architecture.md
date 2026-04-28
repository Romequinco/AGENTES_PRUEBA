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
         └───────┬───────────────┘
                 │
        ┌────────┴────────┐
        │   (paralelo)    │
        ▼                 ▼
researcher.py        analyst.py
(Recopilador)        (Analista)
Sonnet               Sonnet
        │                 │
        └────────┬────────┘
                 │
                 ▼
           writer.py       ← Redactor (Sonnet) — único con Write
                 │
                 ▼
        output/informe_YYYY-MM-DD.pdf
                 │
                 ▼
         Validación final por leader.py
                 │
                 ▼
         _run_newsletter()    ← no bloquea el pipeline
                 │
        ┌────────┴────────────────────┐
        │                             │
        ▼                             ▼
generate_newsletter_data()     Carga suscriptores
(writer.py)                    activos de PostgreSQL
        │                             │
        ▼                             ▼
data/analysis/                send_bulk_newsletter()
newsletter_YYYY-MM-DD.json    (SendGrid Personalizations)
```

## Módulos Python

| Archivo | Rol | Descripción |
|---|---|---|
| `main.py` | Entry point | Controla horario, logging, directorios, pipeline y newsletter |
| `agents/leader.py` | Orquestador | Coordina subagentes, valida informe final |
| `agents/researcher.py` | Recopilador | Precios, volúmenes y noticias vía yfinance/WebFetch |
| `agents/analyst.py` | Analista | RSI, medias, señales, macro, atribución sectorial |
| `agents/writer.py` | Redactor | Genera PDF/HTML con gráficos; expone `generate_newsletter_data()` |
| `agents/ibex_data.py` | Utilidad | Helpers para datos del IBEX 35 |
| `agents/utils.py` | Utilidad | Logging, formato, limpieza de runs previos |
| `db/models.py` | Base de datos | SQLAlchemy: User, NewsletterSubscriber, Subscription, Alert, Strategy, BacktestResult, Portfolio, PortfolioPosition. Índices en `Alert.active` e `BacktestResult.ran_at`. Expone `get_db_session()` para servicios. |
| `services/email_formatter.py` | Email | `format_newsletter_html()` → HTML mobile-friendly |
| `services/email_sender.py` | Email | `send_bulk_newsletter()` vía SendGrid Personalizations API |
| `services/technical_analyzer.py` | Análisis técnico | SMA20, SMA50, RSI14, MACD, soporte, resistencia vía yfinance |
| `services/alerts_engine.py` | Worker | APScheduler: alertas 17:35 diario + reportes PRO lunes 08:00 |
| `services/monitoring.py` | Monitoring | `send_error_alert()` + `@monitor_errors` con rate limiting 1h |
| `services/backtester.py` | PRO | Backtest determinista, estrategias JSON, límite 3/mes |
| `services/fundamental_analyzer.py` | PRO | `fundamental_data()` + `data_quality_score()` vía yfinance |
| `services/portfolio_tracker.py` | PRO | `add_position`, `close_position`, `portfolio_summary` con benchmark IBEX |
| `services/reporter.py` | PRO | `generate_weekly_report(user_id)` → PDF |
| `api/flask_app.py` | App factory | Crea Flask app, registra 6 blueprints. JWT fail-fast: lanza `RuntimeError` si `JWT_SECRET_KEY` no está o < 32 chars. Tokens expiran en 30 días. |
| `api/auth.py` | Blueprint | `/auth/register`, `/auth/login` |
| `api/newsletter.py` | Blueprint | `/register` (legacy), `/api/v1/newsletter/latest`, `/health`, `/dashboard.html` |
| `api/premium.py` | Blueprint | `/api/v1/alerts`, `/api/v1/technical/<symbol>` (tier premium/pro) |
| `api/pro.py` | Blueprint | Estrategias, backtests, portfolios, reporte semanal (tier pro) |
| `api/stripe.py` | Blueprint | `/stripe/create-checkout`, `/stripe/webhook` |
| `api/admin.py` | Blueprint | `/admin/metrics` (protegido por ADMIN_API_KEY) |
| `api/helpers.py` | Helpers | `get_db()` (wrapper de `db.models.get_db_session`), `require_premium()`, `require_pro()` |
| `frontend/dashboard.html` | Dashboard | SPA vanilla: auth, indicadores técnicos, alertas, upgrade |
| `frontend/admin_dashboard.html` | Admin | KPIs en tiempo real — actualización cada 5 min |
| `.claude/skills/*_instructions.md` | Prompts | System prompts de cada agente del pipeline |

## Sistema completo en Railway

```
┌─────────────────────────────────────────────────────────────────┐
│                         Railway.app                             │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Servicio: web                                           │   │
│  │  gunicorn api.flask_app:app --workers 2 --bind 0.0.0.0:$PORT│
│  │                                                          │   │
│  │  /auth/*         → Blueprint auth (JWT)                  │   │
│  │  /api/v1/alerts  → Blueprint premium (APScheduler)       │   │
│  │  /api/v1/technical/<symbol>                              │   │
│  │  /stripe/*       → Blueprint stripe (webhooks)           │   │
│  │  /api/v1/newsletter/latest                               │   │
│  │  /health         → {db, sendgrid, stripe, timestamp}     │   │
│  │  /dashboard.html → SPA vanilla                           │   │
│  │  /api/v1/strategies, /backtest (PRO)                     │   │
│  │  /api/v1/portfolios, /reports/weekly (PRO)               │   │
│  │  /admin/metrics  → KPIs (X-Admin-Key)                    │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Servicio: worker                                        │   │
│  │  python services/alerts_engine.py                        │   │
│  │                                                          │   │
│  │  17:35 Madrid → _evaluate_alerts() (diario)             │   │
│  │  Lunes 08:00  → _generate_weekly_reports() (semanal)    │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌──────────────────────┐                                       │
│  │  PostgreSQL plugin   │ ← DATABASE_URL auto-configurado       │
│  └──────────────────────┘                                       │
└─────────────────────────────────────────────────────────────────┘
         ↑
         │ GitHub Actions (17:35 Madrid, días laborables)
         │ pipeline PDF + newsletter
```

## Datos y outputs

```
data/
  raw/          ← JSONs del Recopilador (precios, noticias)
  analysis/     ← JSONs del Analista + newsletter_YYYY-MM-DD.json

output/         ← Informes PDF diarios + reportes semanales PRO
logs/           ← Log de cada ejecución: run_YYYY-MM-DD.log
```

## Variables de entorno

Ver `DEPLOY.md` para lista completa con descripción y fuentes.

## Informe generado (estructura — 7 páginas)

1. Cabecera macro (10 indicadores)
2. Tabla resumen IBEX 35 (precio, variación, volumen, señal técnica)
3. Mapa de calor sectorial
4. Gráfico 52 semanas
5. Atribución de rentabilidad
6. Ideas de mercado
7. Calendario económico
