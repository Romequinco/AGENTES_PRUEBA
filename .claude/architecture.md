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
```

## Módulos Python

| Archivo | Rol | Descripción |
|---|---|---|
| `main.py` | Entry point | Controla horario, logging, directorios, orquesta el pipeline |
| `agents/leader.py` | Orquestador | Coordina subagentes, valida el informe final |
| `agents/researcher.py` | Recopilador | Descarga precios, volúmenes y noticias vía yfinance/WebFetch |
| `agents/analyst.py` | Analista | Análisis técnico (RSI, medias, señales), macro, atribución sectorial |
| `agents/writer.py` | Redactor | Genera el informe PDF/HTML con gráficos |
| `agents/ibex_data.py` | Utilidad | Helpers para obtener datos del IBEX 35 y sus componentes |
| `agents/utils.py` | Utilidad | Funciones compartidas (logging, formato, limpieza de runs previos) |

## Datos y outputs

```
data/
  raw/          ← JSONs descargados por el Recopilador (precios, noticias)
  analysis/     ← JSONs procesados por el Analista (señales, métricas)

output/         ← Informes finales (PDF o HTML), un archivo por día
logs/           ← Log de cada ejecución: run_YYYY-MM-DD.log
```

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
