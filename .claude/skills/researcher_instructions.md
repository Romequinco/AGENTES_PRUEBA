# Researcher — Sistema IBEX 35

## Objetivo
El Agente Researcher es el primer eslabón del pipeline. Su misión es recopilar todos los datos de mercado del día y guardarlos en ficheros estructurados para que los agentes posteriores puedan procesarlos.

> **Nota:** Este agente está implementado en Python puro (sin LLM). Este fichero documenta su comportamiento esperado como referencia.

## Inputs esperados
- Ninguno (el agente obtiene los datos de fuentes externas)
- Configuración: tickers IBEX 35, URLs de RSS, fecha del día

## Outputs esperados
1. `data/raw/ibex35_prices_YYYY-MM-DD.csv` — datos OHLC de los 35 tickers
2. `data/raw/ibex35_news_YYYY-MM-DD.json` — noticias de RSS de Expansión y Cinco Días

## Reglas obligatorias
- Siempre generar el CSV con exactamente 35 filas (una por ticker), aunque haya errores
- Si un ticker falla, registrar el error en la columna `error` y continuar
- Si un feed RSS falla, continuar con los demás feeds disponibles
- Solo abortar si TODOS los tickers fallan (mercado cerrado o error de red total)
- Máximo 3 reintentos por ticker o feed

## Formato del CSV
Columnas: `ticker, name, open, high, low, close, volume, prev_close, change_abs, change_pct, market_cap, week_52_high, week_52_low, fetch_timestamp, error`

## Formato del JSON de noticias
Ver esquema completo en el plan del proyecto. Máximo 50 noticias, ordenadas por fecha descendente.

## Errores comunes a evitar
- No mezclar datos de días distintos
- No omitir tickers aunque fallen
- No inventar datos si yfinance devuelve valores inesperados
