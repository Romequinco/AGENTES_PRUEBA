# IBEX 35 — Informe Diario Automático + Newsletter

Sistema multi-agente que genera informes del mercado español de forma automática cada día hábil a las 17:35 (Madrid), usando la API de Claude. Incluye una capa de newsletter por email con API REST.

---

## Arquitectura

```mermaid
flowchart TD
    GH["GitHub Actions\nlun–vie 17:35 Madrid"]
    M["main.py\nValidación horario + mercado"]
    L["Leader Agent\nOpus — Orquestador y validador"]
    R["Researcher Agent\nSonnet — sin escritura de output"]
    A["Analyst Agent\nSonnet — sin escritura de output"]
    W["Writer Agent\nSonnet — único con acceso a output/"]
    OUT["Informe 7 páginas\noutput/"]
    NL["_run_newsletter()\nno bloquea el pipeline"]
    DB["PostgreSQL\nusuarios y suscriptores"]
    SG["SendGrid\nemail batch"]

    GH --> M --> L
    L --> R & A
    R -->|datos raw JSON| A
    A -->|análisis JSON| W
    W --> OUT
    L -->|valida| OUT
    OUT --> NL
    NL --> DB
    NL --> SG
```

| Agente | Módulo | Modelo | Escribe |
|---|---|---|---|
| **Leader** | `agents/leader.py` | Opus | No |
| **Researcher** | `agents/researcher.py` | Sonnet | `data/raw/` |
| **Analyst** | `agents/analyst.py` | Sonnet | `data/analysis/` |
| **Writer** | `agents/writer.py` | Sonnet | `output/` |

El Researcher y el Analyst se lanzan **en paralelo**. El Writer arranca solo cuando ambos terminan. El Leader valida el informe final antes de darlo por completado. Tras el PDF, `_run_newsletter()` envía el email a los suscriptores activos — si falla, no afecta al pipeline.

---

## Estructura

```
├── agents/
│   ├── leader.py        # Orquestador y validador final
│   ├── researcher.py    # Recopilación de datos de mercado (yfinance + RSS)
│   ├── analyst.py       # Análisis técnico y fundamental con LLM
│   ├── writer.py        # Generación del informe con gráficos + generate_newsletter_data()
│   ├── ibex_data.py     # Composición y caché del IBEX 35
│   └── utils.py         # Helpers compartidos (logging, limpieza de runs)
├── db/
│   └── models.py        # Modelos SQLAlchemy: User, NewsletterSubscriber (PostgreSQL)
├── services/
│   ├── email_formatter.py  # HTML mobile-friendly del newsletter
│   └── email_sender.py     # Envío batch via SendGrid Personalizations API
├── api/
│   └── flask_app.py     # POST /register, GET /api/v1/newsletter/latest, GET /health
├── .claude/
│   ├── CLAUDE.md        # Contexto y reglas para Claude Code
│   ├── architecture.md  # Diagrama detallado del pipeline
│   ├── decisions.md     # Log de decisiones de diseño
│   └── estado_actual.md # Estado operativo actual del sistema
├── data/
│   ├── raw/             # JSONs de mercado (output del Researcher)
│   └── analysis/        # JSONs de análisis + newsletter_YYYY-MM-DD.json
├── output/              # Informes generados, un archivo por día
├── logs/                # Logs de ejecución: run_YYYY-MM-DD.log
└── main.py              # Punto de entrada
```

---

## Informe generado (7 páginas)

1. Cabecera macro — 10 indicadores clave
2. Tabla resumen IBEX 35 — precio, variación, volumen, señal técnica
3. Mapa de calor sectorial
4. Gráfico de 52 semanas
5. Atribución de rentabilidad
6. Ideas de mercado
7. Calendario económico

---

## Instalación

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env   # rellenar variables (ver sección de variables de entorno)

# Crear tablas en PostgreSQL (solo la primera vez)
python -c "from dotenv import load_dotenv; load_dotenv(); from db.models import create_tables; create_tables()"
```

---

## Uso

```bash
# Ejecución normal (respeta horario de mercado: 17:35–19:00 Madrid)
python main.py

# Forzar ejecución fuera de horario (tests, desarrollo)
FORCE_RUN=true python main.py          # bash/Linux
$env:FORCE_RUN="true"; python main.py  # PowerShell

# Arrancar la API Flask
python api/flask_app.py
```

---

## CI/CD — GitHub Actions

El workflow `.github/workflows/ibex35_report.yml` se ejecuta automáticamente:
- **Automático:** lunes a viernes a las **18:30 Madrid** todo el año — dos entradas de cron (`30 16` y `30 17` UTC) para cubrir verano (UTC+2) e invierno (UTC+1). El segundo disparo del día es absorbido por una guardia en `main.py` que detecta si el informe ya fue generado.
- **Manual:** `workflow_dispatch` con opción `force_run=true`

El informe generado se sube como artefacto del workflow.

**Secrets requeridos:** `ANTHROPIC_API_KEY`, `DATABASE_URL`, `SENDGRID_API_KEY`, `SENDGRID_FROM_EMAIL`.

---

## Variables de entorno

| Variable | Obligatoria | Descripción |
|---|---|---|
| `ANTHROPIC_API_KEY` | Sí | Clave API de Anthropic |
| `DATABASE_URL` | Sí (newsletter) | URL PostgreSQL. Sin ella el newsletter se omite silenciosamente |
| `SENDGRID_API_KEY` | Sí (newsletter) | Clave API de SendGrid |
| `SENDGRID_FROM_EMAIL` | Sí (newsletter) | Email remitente verificado en SendGrid |
| `MODEL_LEADER` | No | Modelo del orquestador (default: haiku) |
| `MODEL_ANALYST` | No | Modelo del analista (default: haiku) |
| `MODEL_WRITER` | No | Modelo del redactor (default: haiku) |
| `FORCE_RUN` | No | `true` para ignorar validación de horario |
| `MAX_RETRIES` | No | Reintentos por agente (default: 3) |
| `IBEX_CACHE_DAYS` | No | Días de validez de la caché del IBEX (default: 7) |
| `FINNHUB_API_KEY` | No | Para noticias adicionales |

---

## Stack

`Python 3.11` · `anthropic` · `yfinance` · `pandas` · `matplotlib` · `reportlab` · `feedparser` · `SQLAlchemy` · `psycopg2` · `Flask` · `sendgrid`
