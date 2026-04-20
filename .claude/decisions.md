# Decision Log

Registro de decisiones de diseño no obvias. El código muestra el *qué*; este archivo explica el *por qué*.

---

## 001 — Opus solo para el Orquestador

**Decisión:** El orquestador (`leader.py`) usa Claude Opus; los tres subagentes usan Sonnet.

**Por qué:** El orquestador hace dos cosas que requieren razonamiento crítico: coordinar el pipeline detectando fallos y validar coherencia del informe final (datos contradictorios, secciones vacías, formato incorrecto). Sonnet es suficiente para tareas estructuradas como descargar datos o generar texto con plantilla.

**Alternativa descartada:** Opus para todos — coste innecesario sin ganancia de calidad en tareas deterministas.

---

## 002 — Informe reducido de 11 a 7 páginas

**Decisión:** El informe pasó de 11 secciones a 7 en el commit `78a85e7`.

**Por qué:** Las 11 páginas incluían secciones redundantes y demasiado granulares para un informe de cierre diario. El objetivo es un documento institucional de lectura rápida (~2 min), no un análisis exhaustivo. Se consolidó la tabla de valores, se compactaron las señales técnicas y se eliminaron secciones de bajo valor informativo.

**Alternativa descartada:** Mantener 11 páginas con opción de resumen — añade complejidad al Redactor sin beneficio claro.

---

## 003 — Recopilador y Analista son read-only

**Decisión:** Los agentes Recopilador y Analista no tienen permiso de escritura. Solo el Redactor puede escribir archivos.

**Por qué:** Separación de responsabilidades. Si un agente de análisis escribe directamente al output, se pierde la validación del Orquestador y se rompe el pipeline. Es un invariante de diseño, no una limitación técnica.

**Cómo aplicar:** Si necesitas que el Analista persista datos intermedios, debe escribirlos en `data/analysis/` — eso sí está permitido implícitamente (es un artefacto intermedio, no el output final).

---

## 004 — Limpieza de datos del run anterior al inicio de cada ejecución

**Decisión:** Al arrancar, `utils.py` elimina los datos del run del mismo día antes de volver a generarlos (commit `189d51b`).

**Por qué:** Sin limpieza, si un run falla a mitad, los datos parciales del día quedan en `data/`. El siguiente intento los encuentra y los usa como si fueran válidos, corrompiendo el informe silenciosamente.

**Alternativa descartada:** Versionar los runs por timestamp — añade complejidad en la gestión de archivos y el Redactor necesitaría saber qué versión usar.

---

## 006 — Doble cron para garantizar 18:30 Madrid todo el año

**Decisión:** Dos entradas de cron en GitHub Actions (`30 16` y `30 17` UTC) en lugar de una sola.

**Por qué:** GitHub Actions cron es UTC fijo y no entiende de horario de verano/invierno. Con una sola entrada, el informe salía a las 18:00 en verano y a las 17:00 en invierno (fuera de la ventana y con datos sin consolidar). La única forma de garantizar exactamente las 18:30 Madrid en ambas estaciones es con dos entradas.

**Cómo aplicar:** Ambos crons se disparan siempre. El segundo run del día es absorbido por la guardia en `main.py` (comprueba si `output/informe_YYYY-MM-DD.pdf` ya existe antes de arrancar el pipeline).

---

## 005 — GitHub Actions como scheduler (no cron local)

**Decisión:** La ejecución diaria se dispara desde GitHub Actions, no desde un cron en un servidor propio.

**Por qué:** Sin infraestructura propia que mantener. GitHub Actions tiene logs integrados, reintentos y notificaciones de fallo. El coste es prácticamente cero para una ejecución diaria de pocos minutos.

**Limitación conocida:** GitHub Actions puede tener retrasos de hasta 10-15 min en el disparo del cron. Para el caso de uso (informe de cierre de mercado) es aceptable.
