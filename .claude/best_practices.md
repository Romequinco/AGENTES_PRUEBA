# Best Practices: Multi-Agent Systems con Claude Code

> Documentación basada en fuentes oficiales de Anthropic (docs.anthropic.com, code.claude.com/docs).
> Fecha de investigación: Abril 2026.

---

## 1. ARQUITECTURA MULTI-AGENTE

### Patrones principales

Claude Code ofrece dos patrones de multi-agente:

#### A) Subagentes (recomendado para la mayoría de casos)
- Se ejecutan en un contexto aislado dentro de la sesión padre
- El padre invoca mediante la herramienta `Agent`; el subagente devuelve un único mensaje de resultado
- **No** heredan el historial de conversación del padre ni su system prompt
- **Sí** reciben: el prompt que les envías, el CLAUDE.md del proyecto (carga automática), y las herramientas que les asignes
- No pueden invocar otros subagentes
- Coste menor: solo el resumen del subagente vuelve al padre

#### B) Agent Teams (experimental — requiere `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS`)
- Instancias separadas de Claude Code con lista de tareas compartida
- Un "team lead" coordina; los teammates se comunican directamente entre sí con `SendMessage`
- Coste mayor: cada teammate tiene su propio contexto completo
- Útiles **solo** cuando la discusión entre agentes aporta valor real (revisiones, investigación, hipótesis contradictorias)

### Cuándo usar cada patrón

| Situación | Patrón recomendado |
|---|---|
| Tareas independientes y paralelas | Subagentes en paralelo |
| Investigación / exploración de código | Subagente Explore |
| Revisión con múltiples perspectivas | Agent Team (3-5 miembros) |
| Tareas secuenciales con dependencias | Sesión única |
| Análisis de seguridad / calidad | Subagente especializado |
| Trabajo rutinario | Sesión única (más barato) |

**Regla de oro:** Usa subagentes cuando solo importa el resultado. Usa agent teams cuando la discusión entre agentes genera valor.

### Comunicación entre agentes

- **Subagentes:** no se comunican entre sí; el flujo es padre → subagente → padre
- **Agent Teams:** mensajes directos entre teammates; evitar `broadcast` (escala con el tamaño del equipo)
- El padre agrega y sintetiza los resultados de todos los subagentes

---

## 2. CLAUDE.MD — MEJORES PRÁCTICAS

### Qué incluir (solo lo que Claude no puede inferir)
- Comandos de build, test y deploy específicos del proyecto
- Reglas de estilo que difieren de los defaults del lenguaje
- Decisiones arquitectónicas no evidentes en el código
- Convenciones de rama, PR y commit
- Variables de entorno requeridas y cómo gestionarlas
- Comportamientos no obvios o gotchas del sistema
- Cómo ejecutar un test individual

### Qué NO incluir
- Nada que Claude pueda deducir leyendo el código
- Convenciones estándar del lenguaje que Claude ya conoce
- Documentación de API detallada (enlaza en su lugar)
- Información que cambia frecuentemente
- Prácticas genéricas de desarrollo ("escribe código limpio")
- Descripciones archivo por archivo de la estructura
- Instrucciones obvias sobre seguridad, tests o manejo de errores

### Tamaño y estructura
- **Objetivo:** menos de 200 líneas por archivo CLAUDE.md
- Usa headers markdown para agrupar instrucciones relacionadas
- Bullets concisos y accionables
- Front-load el caso de uso más importante en las descripciones

### Jerarquía de CLAUDE.md (mayor prioridad primero)
1. CLAUDE.md de política administrada (organización)
2. CLAUDE.md raíz del proyecto (compartido en git)
3. `~/.claude/CLAUDE.md` del usuario (todos los proyectos)
4. `CLAUDE.local.md` (personal, en .gitignore)
5. CLAUDE.md de subdirectorios (carga bajo demanda)

### Modularización con imports
```markdown
Ver @README.md para visión general.
Reglas de API: @.claude/rules/api-conventions.md
```

### Reglas con scope de path (en `.claude/rules/`)
```markdown
---
paths:
  - "src/api/**/*.py"
---
# Reglas específicas para módulos de API
- Toda ruta debe incluir validación de input
- Usar formato estándar de respuesta de error
```

---

## 3. SKILLS / SLASH COMMANDS

### Cuándo crear un skill
- Repites el mismo procedimiento multi-paso frecuentemente
- Una sección del CLAUDE.md ha crecido hasta convertirse en un procedimiento, no en un hecho
- Quieres que Claude cargue el contenido solo cuando sea relevante (ahorra contexto)

### Estructura de archivos
```
.claude/skills/<nombre>/
├── SKILL.md          # Instrucciones principales (requerido)
├── template.md       # Plantilla opcional
└── examples/         # Ejemplos opcionales
```

### Frontmatter completo de SKILL.md
```yaml
---
name: nombre-skill
description: Descripción específica (max 250 chars, front-load el caso de uso)
disable-model-invocation: true    # Solo el usuario puede invocar (para side effects)
user-invocable: false             # Solo Claude puede invocar (conocimiento de fondo)
allowed-tools: Bash(npm *) Read Write
context: fork                     # Ejecutar en subagente aislado
agent: Explore                    # Tipo de agente con context:fork
model: opus                       # Override de modelo para este skill
paths:                            # Solo cargar para archivos que coincidan
  - "src/api/**/*.py"
---
```

### Control de invocación

| Configuración | Usuario puede invocar | Claude puede invocar |
|---|---|---|
| Default | Sí | Sí |
| `disable-model-invocation: true` | Sí | No |
| `user-invocable: false` | No | Sí |

### Inyección dinámica de contexto
```markdown
## Contexto del PR
- Diff: !`gh pr diff`
- Comentarios: !`gh pr view --comments`
```
Los comandos se ejecutan antes de que Claude vea el skill; la salida reemplaza el placeholder.

### Substituciones disponibles
- `$ARGUMENTS` — todos los argumentos pasados al skill
- `$0`, `$1`, `$N` — argumento específico por índice
- `${CLAUDE_SESSION_ID}` — ID de sesión actual
- `${CLAUDE_SKILL_DIR}` — directorio del skill

---

## 4. HOOKS

### Tipos de hooks
| Tipo | Descripción | Mejor para |
|---|---|---|
| `command` | Ejecuta shell script | Validación determinista, logging |
| `http` | POST a servicio externo | Webhooks, logging remoto |
| `prompt` | LLM evalúa sí/no | Validación que requiere razonamiento |
| `agent` | Lanza subagente con herramientas | Verificación compleja |

### Eventos principales
- **Sesión:** `SessionStart`, `SessionEnd`
- **Turno:** `UserPromptSubmit`, `Stop`, `StopFailure`
- **Herramientas:** `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `PermissionRequest`, `PermissionDenied`
- **Subagentes:** `SubagentStart`, `SubagentStop`
- **Tareas:** `TaskCreated`, `TaskCompleted`
- **Archivos:** `FileChanged`
- **Compactación:** `PreCompact`, `PostCompact`

### Estructura de configuración (settings.json)
```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": ".claude/hooks/validate-bash.sh",
            "timeout": 30,
            "statusMessage": "Validando comando...",
            "if": "Bash(rm *)"
          }
        ]
      }
    ]
  }
}
```

### Patrones de matcher
- `"*"` o vacío: coincide con todos los eventos
- Texto simple: coincidencia exacta o lista separada por `|`
- Caracteres especiales: regex de JavaScript

### Códigos de salida
- **0:** Éxito, parsear JSON de stdout
- **2:** Error bloqueante, usar stderr como mensaje
- **1 u otro:** No bloqueante, log de stderr, continuar

### Output estándar del hook
```json
{
  "continue": true,
  "stopReason": "Por qué parar",
  "systemMessage": "Mensaje al usuario",
  "additionalContext": "Contexto adicional para Claude",
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "allow|deny|ask|defer"
  }
}
```

---

## 5. HERRAMIENTA AGENT — USO AVANZADO

### Qué hereda y qué no hereda un subagente

**Recibe:**
- El prompt enviado por el padre (instrucciones de la invocación)
- CLAUDE.md del proyecto (carga automática)
- Las herramientas asignadas en la definición o heredadas

**NO recibe:**
- Historial de conversación del padre
- System prompt del padre
- Skills (a menos que se listen explícitamente)
- Resultados de herramientas anteriores del padre

### Modos de aislamiento

| Modo | Descripción | Cuándo usar |
|---|---|---|
| In-context (default) | Dentro de la sesión padre | Tareas pequeñas y rápidas |
| `context: fork` | Ventana de contexto propia | Exploraciones largas |
| `isolation: worktree` | Worktree git separado | Modificaciones de archivos en paralelo |

### Definición de subagente custom (`.claude/agents/<nombre>.md`)
```markdown
---
name: security-reviewer
description: Revisa código en busca de vulnerabilidades de seguridad
tools: Read, Grep, Glob
model: opus
---

Eres un ingeniero de seguridad senior. Analiza el código para detectar:
SQL injection, XSS, command injection, manejo inseguro de datos,
autenticación débil. Proporciona referencias de línea específicas y
niveles de severidad.
```

### Ejecución paralela de subagentes
Enviar múltiples bloques `Agent` en un solo mensaje lanza todos los subagentes simultáneamente. Cada uno procesa en su propia ventana de contexto de forma independiente.

---

## 6. GESTIÓN DE CONTEXTO

### Técnicas principales
- `/clear` — entre tareas no relacionadas
- `/compact <instrucciones>` — resumir partes irrelevantes del contexto
- `/rewind` — volver a un estado anterior
- Usar subagentes para investigaciones (exploran en contexto separado)

### Señales de que el contexto se está degradando
- Claude comete errores básicos en código que ya conoce
- Respuestas repetitivas o inconsistentes
- Ignora instrucciones que antes seguía

---

## 7. ERRORES COMUNES A EVITAR

### En CLAUDE.md
- **CLAUDE.md excesivo** (>200 líneas): Claude ignora reglas importantes perdidas en el ruido
- **Conflictos de reglas**: comportamiento arbitrario al resolver contradicciones
- **Reglas que Claude ya sigue**: desperdician espacio de contexto

### En delegación a subagentes
- **Prompts vagos**: "investiga esto" sin alcance → subagente lee cientos de archivos
- **Sin criterios de verificación**: confiar sin poder verificar el resultado
- **Sin contexto suficiente**: el subagente no hereda el historial; incluye detalles relevantes en el prompt
- **Conflictos de archivos**: dos agentes editando el mismo archivo → usar worktrees

### En agent teams
- **Coordinación que supera el beneficio**: para trabajo secuencial, una sesión sola es más eficiente
- **Demasiados teammates** (>5): overhead de coordinación desproporcional
- **Sin criterios de finalización claros**: acumulan tokens indefinidamente

### En skills y hooks
- **Descripción del skill demasiado vaga**: Claude no lo invoca cuando debería
- **Matcher del hook demasiado amplio/estrecho**: bloquea trabajo legítimo o falla en detectar problemas
- **Formato de output del hook incorrecto**: confusión entre códigos de salida 1 y 2

### En selección de modelo
- **Haiku para razonamiento complejo**: resultados incorrectos
- **Opus para tareas rutinarias**: coste innecesariamente alto
- **Modelo fijo para todos los agentes**: usar Opus para revisiones críticas, Sonnet para desarrollo general

---

## 8. PATRONES RECOMENDADOS

### Patrón 1: Orquestador + Subagentes especializados
```
Agente Orquestador (Sonnet)
├── Subagente Investigador (Explore) — solo lectura, contexto aislado
├── Subagente Implementador (Sonnet) — worktree isolation
├── Subagente Revisor de Seguridad (Opus) — solo lectura
└── Subagente de Tests (Sonnet) — ejecuta y valida
```

### Patrón 2: Pipeline de calidad con hooks
```
Edit/Write → PostToolUse hook → linter automático
Bash → PreToolUse hook → validación de comandos peligrosos
SessionEnd → hook → resumen de cambios
```

### Patrón 3: Skill de workflow completo
```yaml
---
name: fix-issue
disable-model-invocation: true
allowed-tools: Bash(gh *) Read Edit Write Bash(npm test)
---
1. gh issue view $ARGUMENTS
2. Buscar código relevante
3. Implementar fix
4. Escribir tests
5. npm test
6. Crear PR
```

### Criterios de éxito para la delegación
Siempre incluir en el prompt del subagente:
- Qué archivos/directorios examinar
- Qué criterios definen el éxito
- Formato esperado del resultado
- Restricciones de alcance

---

## 9. SELECCIÓN DE MODELO POR TAREA

| Tarea | Modelo recomendado |
|---|---|
| Decisiones arquitectónicas | Opus |
| Revisiones de seguridad | Opus |
| Desarrollo general | Sonnet |
| Implementación de features | Sonnet |
| Verificación simple / lint | Haiku |
| Búsquedas y exploración | Haiku o Sonnet |

---

## 10. REFERENCIAS OFICIALES

- Subagentes: https://code.claude.com/docs/en/sub-agents.md
- Skills: https://code.claude.com/docs/en/skills.md
- Hooks: https://code.claude.com/docs/en/hooks.md
- Memory/CLAUDE.md: https://code.claude.com/docs/en/memory.md
- Agent Teams: https://code.claude.com/docs/en/agent-teams.md
- Best Practices: https://code.claude.com/docs/en/best-practices.md
