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
| `db/models.py` | Base de datos | Modelos SQLAlchemy: `User`, `NewsletterSubscriber`. Requiere `DATABASE_URL` (PostgreSQL) |
| `services/email_formatter.py` | Formateador | `format_newsletter_html()` → HTML mobile-friendly para el newsletter |
| `services/email_sender.py` | Envío email | `send_bulk_newsletter()` via SendGrid Personalizations API (batch, no loop) |
| `api/flask_app.py` | API REST | Endpoints: `POST /register`, `GET /api/v1/newsletter/latest`, `GET /health` |

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
| `DATABASE_URL` | Sí (newsletter) | URL PostgreSQL. Sin ella el newsletter se omite (no crashea el pipeline) |
| `SENDGRID_API_KEY` | Sí (newsletter) | Clave API de SendGrid |
| `SENDGRID_FROM_EMAIL` | Sí (newsletter) | Email remitente verificado en SendGrid |
| `FINNHUB_API_KEY` | Opcional | Para noticias adicionales |
| `FORCE_RUN` | Opcional | `true` para ignorar horario de mercado |

## Ejecución automática

- **GitHub Actions** dispara el workflow en días laborables a las 17:35 Madrid
- Variables de entorno necesarias: definidas en `.env` local o secrets de GitHub
- Para ejecutar fuera de horario: `FORCE_RUN=true python main.py`

## Informe generado (estructura actual — 7 páginas)

1. Cabecera macro (10 indicadores)
2. Tabla resumen IBEX 35 (precio, variación, volumen, señal técnica)
3. Mapa de calor sectorial
4. Gráfico 52 semanas
5. Atribución de rentabilidad
6. Ideas de mercado
7. Calendario económico
