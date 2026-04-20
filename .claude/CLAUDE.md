# CLAUDE.md

Guía de contexto para Claude Code en este repositorio. Ver también:
- [`.claude/architecture.md`](.claude/architecture.md) — diagrama del pipeline y módulos Python
- [`.claude/decisions.md`](.claude/decisions.md) — registro de decisiones de diseño
- [`.claude/estado_actual.md`](.claude/estado_actual.md) — qué funciona, qué no, próximos pasos

## Propósito del proyecto

Sistema multi-agente para generar informes automáticos del IBEX 35. Un orquestador coordina tres subagentes especializados que se ejecutan diariamente vía GitHub Actions a las 17:35 (cierre del mercado Madrid).

Pipeline: **Recopilador → Analista → Redactor → Validación por el Orquestador**

## Arquitectura de agentes

Definidos en `.claude/agents/` (frontmatter con modelo y herramientas). Los módulos Python equivalentes están en `agents/`.

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
- Cada invocación debe incluir criterios de éxito explícitos y formato de salida esperado
- Usar `isolation: worktree` solo si dos agentes deben editar archivos distintos en paralelo

## Estructura de directorios

```
.claude/
  agents/            # Definiciones de subagentes Claude (.md con frontmatter)
  skills/            # Slash commands del proyecto (/skill-name)
  hooks/             # Scripts ejecutados en eventos del ciclo de vida
  CLAUDE.md          # Este archivo
  architecture.md    # Diagrama del pipeline y módulos
  decisions.md       # Log de decisiones de diseño
  estado_actual.md   # Estado operativo actual del sistema
  best_practices.md  # Referencia multi-agente
agents/              # Módulos Python de cada agente
data/                # Datos de mercado cacheados (raw/ y analysis/)
output/              # Informes generados (PDF/HTML)
logs/                # Logs de cada ejecución diaria
```

## Convenciones importantes

- Ver `.claude/best_practices.md` para referencia completa sobre subagentes, skills y hooks
- El sistema de memoria automática de Claude está activo; no sobreescribir con información efímera
- Mantener este CLAUDE.md bajo 200 líneas; mover reglas específicas a `.claude/rules/` si crecen
- `check_market_hours()` en `main.py` controla si se ejecuta; usar `FORCE_RUN=true` para pruebas fuera de horario
