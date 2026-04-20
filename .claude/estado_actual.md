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

## Trabajo reciente completado (última sesión)

- Reestructuración del informe de 11 → 7 páginas (vocabulario institucional)
- Corrección de leyenda duplicada en mapa de calor
- Función de limpieza de runs del día para evitar datos parciales corruptos
- `utils.py` centraliza helpers compartidos entre agentes
- **Timing del cron corregido:** doble cron (`30 16` y `30 17` UTC) para garantizar 18:30 Madrid en verano e invierno
- **Ventana horaria ampliada:** 17:30–19:30 Madrid (antes 17:35–18:59) para cubrir ambas estaciones y retrasos de Actions
- **Guardia anti-doble-run** en `main.py`: si el PDF del día ya existe, el segundo cron sale sin acción
- **Email de aviso de fallo:** si no se generó informe hoy, envía email con asunto ⚠️ y lista de causas posibles
- **Node.js 24** activado en el workflow (`FORCE_JAVASCRIPT_ACTIONS_TO_NODE24`) antes de la deprecación de junio 2026
- Documentación `.claude/` creada: `architecture.md`, `decisions.md`, `estado_actual.md`

## Pendiente / Próximos pasos

- Las carpetas `.claude/agents/`, `.claude/skills/` y `.claude/hooks/` están vacías — los agentes Claude están implementados como módulos Python en `agents/`, no como definiciones `.md`
- Evaluar si migrar las definiciones de agentes a `.claude/agents/*.md` para aprovechar el sistema nativo de subagentes de Claude Code
- No hay tests automatizados del output del informe (solo tests del código en `tests/`)

## Limitaciones conocidas

- GitHub Actions puede tener retrasos de 10-15 min en el cron (absorbido por la ventana 17:30–19:30)
- yfinance puede tardar 30-60 min tras el cierre en reflejar el OHLCV del día → `get_last_market_date()` maneja este edge case
- En las semanas de cambio de horario (marzo/octubre) ambos crons caen fuera de la estación correcta durante 1-2 días — efecto mínimo y autoresolutivo

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
