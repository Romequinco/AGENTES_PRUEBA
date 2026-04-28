# CLAUDE.md

Guía de contexto para Claude Code en este repositorio. Ver también:
- [`.claude/architecture.md`](.claude/architecture.md) — diagrama del pipeline y módulos Python
- [`.claude/decisions.md`](.claude/decisions.md) — registro de decisiones de diseño
- [`.claude/estado_actual.md`](.claude/estado_actual.md) — qué funciona, qué no, próximos pasos

## Propósito del proyecto

Sistema multi-agente para generar informes automáticos del IBEX 35 y enviarlos por email como newsletter. Un orquestador coordina tres subagentes especializados que se ejecutan diariamente vía GitHub Actions a las 17:35 (cierre del mercado Madrid).

Pipeline: **Recopilador → Analista → Redactor → Validación por el Orquestador → Newsletter**

## Arquitectura de agentes

| Agente | Módulo Python | Modelo | Herramientas |
|---|---|---|---|
| Recopilador | `agents/researcher.py` | Sonnet | Read, Bash, WebFetch |
| Analista | `agents/analyst.py` | Sonnet | Read, Bash, WebFetch |
| Redactor | `agents/writer.py` | Sonnet | Read, Bash, WebFetch, Write |
| Orquestador | `agents/leader.py` | Opus | Todas |

El orquestador lanza Recopilador y Analista **en paralelo**, luego Redactor en secuencia, y finalmente valida él mismo el resultado.

## Reglas de orquestación

- El orquestador nunca escribe datos directamente; delega toda escritura al Redactor
- Los subagentes de análisis son read-only: si intentan modificar archivos, es un error de diseño
- Los subagentes **no heredan** el historial del padre — cada prompt debe incluir el contexto necesario
- Usar `isolation: worktree` solo si dos agentes deben editar archivos distintos en paralelo

## Capa de newsletter (Fase 1 — operativa)

La capa de newsletter se ejecuta **después** del pipeline principal y nunca lo bloquea.

| Módulo | Rol |
|---|---|
| `agents/writer.py::generate_newsletter_data()` | Extrae campos clave del JSON del Analista |
| `services/email_formatter.py` | HTML mobile-friendly |
| `services/email_sender.py` | Envío batch via SendGrid Personalizations API |
| `db/models.py` | SQLAlchemy: `User`, `NewsletterSubscriber` |

**Regla crítica:** `DATABASE_URL` debe apuntar a PostgreSQL (no SQLite). Railway tiene filesystem efímero.

## Auth + Premium (Fase 2 — operativa)

| Módulo | Rol |
|---|---|
| `api/auth.py` | JWT: `/auth/register`, `/auth/login` |
| `api/premium.py` | Alertas técnicas + análisis (tier premium/pro) |
| `api/stripe.py` | Checkout, webhooks — tier actualizado solo por webhook |
| `services/alerts_engine.py` | Worker APScheduler: evalúa alertas 17:35 Madrid |
| `services/technical_analyzer.py` | SMA20, SMA50, RSI14, MACD via yfinance |
| `frontend/dashboard.html` | SPA vanilla: auth, indicadores, alertas, upgrade |

## Portfolio Tracker Global (Sprint 2 — operativo, tier gratuito)

Accesible para cualquier usuario autenticado (free, premium, pro).

| Módulo | Rol |
|---|---|
| `services/portfolio_tracker.py` | `add_position`, `get_positions`, `update_position`, `delete_position`, `portfolio_summary` — multi-asset |
| `api/portfolio.py` | Blueprint `/api/v1/portfolio/*` — 5 endpoints REST, JWT sin tier |
| `db/migrations/002_portfolio_global.sql` | Migración: `asset_type`, `exchange`, `user_id`, `created_at` en `portfolio_positions` |

**Asset types:** `stock`, `etf`, `crypto`, `commodity`. Precios via `get_quote()` de `market_data.py`.
**Benchmarks:** `^GSPC` (S&P 500, por defecto), `^IBEX`, `^IXIC`. Configurable en `/summary?benchmark=`.
**Migración Railway:** `psql $DATABASE_URL < db/migrations/002_portfolio_global.sql`

## Tier PRO (Fase 3 — operativa)

Funcionalidades exclusivas para `tier = 'pro'`. No afectan al pipeline principal.

| Módulo | Rol |
|---|---|
| `services/backtester.py` | `backtest(symbol, strategy_dict, days)` — determinista, JSON |
| `services/fundamental_analyzer.py` | `fundamental_data(symbol)` + `data_quality_score()` |
| `services/reporter.py` | `generate_weekly_report(user_id)` → PDF |

**Formato de estrategia (JSON, no lambdas):**
```json
{"buy": {"indicator": "rsi", "operator": "below", "value": 30},
 "sell": {"indicator": "rsi", "operator": "above", "value": 70}}
```
**Límite:** 3 backtests/mes por usuario PRO. Verificado en DB antes de ejecutar.

## Producción en Railway (Fase 4 — operativa)

| Módulo/Archivo | Rol |
|---|---|
| `railway.toml` + `Procfile` | 2 servicios: `web` (gunicorn) + `worker` (alerts_engine) |
| `services/monitoring.py` | `send_error_alert()` + `@monitor_errors` — rate limit 1h en memoria |
| `api/admin.py` | `GET /admin/metrics` — protegido por header `X-Admin-Key` |
| `frontend/admin_dashboard.html` | KPIs en tiempo real, actualización cada 5 min |
| `DEPLOY.md` | Guía de primer deploy en Railway (7 pasos) |

**Job semanal:** `alerts_engine.py` genera reportes PRO cada lunes 08:00 Madrid. Fallo individual no aborta el batch.
**`/health`** devuelve `{status, db, sendgrid, stripe, timestamp}` — nunca lanza excepción.

## Estructura de directorios

```
.claude/
  skills/             # Instrucciones de cada agente (*_instructions.md)
  CLAUDE.md           # Este archivo
  architecture.md     # Diagrama del pipeline y módulos
  decisions.md        # Log de decisiones de diseño (023-028: auditoría 2026-04-28)
  estado_actual.md    # Estado operativo actual
  best_practices.md   # Referencia multi-agente
agents/               # Módulos Python de cada agente (Recopilador, Analista, Redactor, Orquestador)
db/                   # Modelos SQLAlchemy (PostgreSQL) + get_db_session()
services/             # email_formatter, email_sender, technical_analyzer, alerts_engine,
                      # monitoring, backtester, fundamental_analyzer, portfolio_tracker, reporter
api/
  flask_app.py        # App factory — registra 6 blueprints, JWT fail-fast en arranque
  helpers.py          # get_db(), require_premium(), require_pro()
  auth.py             # Blueprint /auth/*
  newsletter.py       # Blueprint /register, /api/v1/newsletter/latest, /health
  premium.py          # Blueprint alertas + análisis técnico (tier premium/pro)
  pro.py              # Blueprint estrategias, backtests, portfolios, reporte (tier pro)
  stripe.py           # Blueprint /stripe/*
  admin.py            # Blueprint /admin/metrics (X-Admin-Key)
frontend/             # dashboard.html (usuarios) + admin_dashboard.html (admin)
tests/                # pytest — 48 tests (smoke + fase 3)
data/                 # raw/ y analysis/ (datos cacheados + newsletter JSON)
output/               # PDFs diarios + reportes semanales PRO
logs/                 # run_YYYY-MM-DD.log por ejecución
railway.toml          # Configuración Railway (2 servicios)
Procfile              # Fallback Railway
DEPLOY.md             # Guía de primer deploy
```

## Convenciones importantes

- `check_market_hours()` en `main.py` controla si se ejecuta; `FORCE_RUN=true` para pruebas
- `_run_newsletter()` falla silenciosamente — ver logs `[NEWSLETTER]` para diagnóstico
- Estrategias de backtesting: siempre JSON, nunca lambdas (no serializables)
- `@monitor_errors` de `services/monitoring.py`: usar en jobs nuevos de APScheduler — re-lanza la excepción, no la silencia
- `ADMIN_API_KEY` controla `/admin/metrics`; si no está configurada el endpoint devuelve 503
- **`JWT_SECRET_KEY`** debe tener ≥ 32 chars — `create_app()` lanza `RuntimeError` si no está definida
- **Tokens JWT expiran en 30 días** — el frontend no implementa refresh; un 401 requiere nuevo login
- **Sesiones DB en servicios:** usar `from db.models import get_db_session` con import lazy (dentro de la función, no a nivel de módulo) — `db/models.py` requiere `DATABASE_URL` al importarse
- **Logger en `LeaderAgent`:** usar `self.logger` dentro de los métodos de la clase, no el `logger` global del módulo
- Ver `DEPLOY.md` para lista completa de variables de entorno requeridas por servicio
- Ver `.claude/best_practices.md` para referencia de subagentes, skills y hooks
