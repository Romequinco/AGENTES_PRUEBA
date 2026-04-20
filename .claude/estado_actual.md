# Estado actual del sistema

> Última actualización: 2026-04-20

## Estado general: FUNCIONAL

El pipeline completo está operativo. GitHub Actions ejecuta el sistema diariamente en días laborables a las 17:35 (cierre IBEX 35).

---

## Qué funciona

- Pipeline completo: Recopilador → Analista → Redactor → Validación
- Informe de 7 páginas con estructura institucional
- Gráfico de 52 semanas del IBEX
- Mapa de calor sectorial (con leyenda corregida)
- Limpieza automática de datos del run anterior al inicio de cada ejecución
- Ejecución fuera de horario con `FORCE_RUN=true`
- GitHub Actions con variables de entorno/secrets configurados

## Trabajo reciente completado

- Reestructuración del informe de 11 → 7 páginas (vocabulario institucional)
- Corrección de leyenda duplicada en mapa de calor
- Función de limpieza de runs del día para evitar datos parciales corruptos
- `utils.py` centraliza helpers compartidos entre agentes

## Pendiente / Próximos pasos

- Las carpetas `.claude/agents/`, `.claude/skills/` y `.claude/hooks/` están vacías — los agentes Claude están implementados como módulos Python en `agents/`, no como definiciones `.md`
- Evaluar si migrar las definiciones de agentes a `.claude/agents/*.md` para aprovechar el sistema nativo de subagentes de Claude Code
- No hay tests automatizados del output del informe (solo tests del código en `tests/`)

## Limitaciones conocidas

- GitHub Actions puede tener retrasos de 10-15 min en el cron (aceptable para informes de cierre)
- yfinance puede tardar 30-60 min tras el cierre en reflejar el OHLCV del día → `get_last_market_date()` maneja este edge case
- El sistema no tiene alertas si el informe no se genera (fallo silencioso en GitHub Actions sin notificación activa)

## Cómo ejecutar en local

```bash
# Instalación
pip install -r requirements.txt
cp .env.example .env  # y rellenar variables

# Ejecución normal (respeta horario de mercado)
python main.py

# Forzar ejecución fuera de horario
FORCE_RUN=true python main.py
```
