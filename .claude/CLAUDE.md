# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Propósito del proyecto

Sistema multi-agente para generar informes automáticos de la bolsa española. Un agente orquestador coordina tres subagentes especializados:

1. **Agente Recopilador** — obtiene datos de mercado (precios, volúmenes, noticias)
2. **Agente Analista** — análisis técnico y fundamental sobre los datos recopilados
3. **Agente Redactor** — genera el texto del informe en formato legible

El **Orquestador** coordina el pipeline y además actúa como validador: verifica coherencia, datos y calidad del informe final antes de darlo por completado. Pipeline: Recopilador → Analista → Redactor → (validación por el Orquestador).

## Arquitectura de agentes

Los agentes se definen en `.claude/agents/`. Cada uno tiene herramientas y modelo asignados según su función:

- El **Recopilador** y el **Analista** son de solo lectura (herramientas: `Read`, `Bash`, `WebFetch`)
- El **Redactor** puede escribir archivos de salida (agrega `Write`)
- El **Orquestador** usa `Opus` para razonamiento más riguroso y validación final; los subagentes usan `Sonnet`
- El orquestador lanza Recopilador y Analista en paralelo cuando no hay dependencias, luego Redactor en secuencia, y finalmente valida el resultado él mismo

Los subagentes **no heredan** el historial de conversación del padre. El prompt de cada invocación debe incluir el contexto necesario (ticker, fecha, datos previos relevantes).

## Reglas de orquestación

- El orquestador nunca escribe datos directamente; delega toda escritura al Redactor
- Los subagentes de análisis son read-only: si un subagente de análisis intenta modificar archivos, es un error de diseño
- Cada invocación de subagente debe incluir criterios de éxito explícitos y formato de salida esperado
- Usar `isolation: worktree` solo si dos agentes deben editar archivos distintos en paralelo

## Estructura de directorios (convención)

```
.claude/
  agents/          # Definiciones de subagentes (.md con frontmatter)
  skills/          # Skills / slash commands del proyecto
  hooks/           # Scripts de hooks
  best_practices.md  # Documentación de referencia de multi-agente
informes/          # Informes generados (output del Redactor)
datos/             # Datos de mercado cacheados (output del Recopilador)
```

## Mejores prácticas aplicadas a este proyecto

- Ver `.claude/best_practices.md` para referencia completa sobre subagentes, skills, hooks y CLAUDE.md
- El sistema de memoria automática de Claude se mantiene activo; no sobreescribir con información efímera
- Mantener este CLAUDE.md bajo 200 líneas; mover reglas específicas a `.claude/rules/` si crecen

## Selección de modelo por agente

| Agente | Modelo | Razón |
|---|---|---|
| Recopilador | Sonnet | Tarea estructurada, no requiere razonamiento complejo |
| Analista | Sonnet | Análisis estándar |
| Redactor | Sonnet | Generación de texto |
| Orquestador / Validador | Opus | Coordinación general + razonamiento crítico y detección de inconsistencias |
