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

## 005 — GitHub Actions como scheduler (no cron local)

**Decisión:** La ejecución diaria se dispara desde GitHub Actions, no desde un cron en un servidor propio.

**Por qué:** Sin infraestructura propia que mantener. GitHub Actions tiene logs integrados, reintentos y notificaciones de fallo. El coste es prácticamente cero para una ejecución diaria de pocos minutos.

**Limitación conocida:** GitHub Actions puede tener retrasos de hasta 10-15 min en el disparo del cron. Para el caso de uso (informe de cierre de mercado) es aceptable.

---

## 006 — Doble cron para garantizar 18:30 Madrid todo el año

**Decisión:** Dos entradas de cron en GitHub Actions (`30 16` y `30 17` UTC) en lugar de una sola.

**Por qué:** GitHub Actions cron es UTC fijo y no entiende de horario de verano/invierno. Con una sola entrada, el informe salía a las 18:00 en verano y a las 17:00 en invierno (fuera de la ventana y con datos sin consolidar). La única forma de garantizar exactamente las 18:30 Madrid en ambas estaciones es con dos entradas.

**Cómo aplicar:** Ambos crons se disparan siempre. El segundo run del día es absorbido por la guardia en `main.py` (comprueba si `output/informe_YYYY-MM-DD.pdf` ya existe antes de arrancar el pipeline).

---

## 007 — PostgreSQL obligatorio (no SQLite) para el newsletter

**Decisión:** `db/models.py` requiere `DATABASE_URL` con PostgreSQL. SQLite está explícitamente descartado.

**Por qué:** Railway (plataforma de producción) tiene filesystem efímero — cada redeploy borra el disco. SQLite perdería todos los suscriptores en cada deploy. PostgreSQL persiste en un addon separado del filesystem.

**Cómo aplicar:** Si `DATABASE_URL` no está en el entorno, `db/models.py` lanza `EnvironmentError` inmediatamente con mensaje claro. `_run_newsletter()` en `main.py` comprueba la variable antes de intentar conectar, y si no está presente omite el envío sin crashear el pipeline.

---

## 008 — Newsletter no bloquea el pipeline principal

**Decisión:** `_run_newsletter()` en `main.py` captura todas las excepciones y las loguea sin relanzarlas. El pipeline termina con `sys.exit(0)` aunque el newsletter falle.

**Por qué:** El valor primario del sistema es el informe PDF diario. Un fallo de SendGrid, de red o de la DB no debe impedir que el PDF se genere. Los dos sistemas son independientes en cuanto a criticidad.

**Cómo aplicar:** Si el newsletter falla, aparece `[NEWSLETTER] Error inesperado` en el log pero el proceso termina con código 0.

---

## 010 — JWT con flask-jwt-extended (no sesiones de servidor)

**Decisión:** Auth basada en JWT stateless. El servidor no guarda sesiones — cada request incluye el token en `Authorization: Bearer <token>`.

**Por qué:** La API corre en Railway como proceso stateless. Las sesiones de servidor requieren almacenamiento compartido (Redis o DB) entre workers. JWT elimina esa dependencia y escala horizontalmente sin configuración extra.

**Cómo aplicar:** `JWT_SECRET_KEY` debe tener al menos 32 chars. Si se cambia la clave, todos los tokens existentes quedan invalidados — avisar a los usuarios antes de rotar en producción.

---

## 011 — bcrypt para hashes de contraseña (no werkzeug)

**Decisión:** Fase 2 usa `bcrypt` directamente en lugar de `werkzeug.security.generate_password_hash`.

**Por qué:** bcrypt tiene factor de trabajo configurable y es el estándar de la industria para contraseñas. werkzeug usa PBKDF2 por defecto, que es aceptable pero bcrypt es más resistente a ataques de GPU. El endpoint `/register` de Fase 1 sigue usando werkzeug por compatibilidad con usuarios ya registrados.

**Cómo aplicar:** Los usuarios de Fase 1 (hash werkzeug) y Fase 2 (hash bcrypt) coexisten en la misma tabla. El login detecta el formato automáticamente porque los prefijos son distintos (`pbkdf2:sha256:...` vs `$2b$...`). Nota: si se migran usuarios, hay que rehashear en el primer login.

---

## 012 — Stripe webhooks como fuente de verdad del tier

**Decisión:** El tier del usuario se actualiza **únicamente** a través del webhook `checkout.session.completed`, nunca por el redirect de éxito del checkout.

**Por qué:** El redirect de éxito puede no ejecutarse (usuario cierra el navegador, fallo de red). El webhook es fiable y llega siempre. Confiar en el redirect como fuente de verdad genera usuarios que pagaron pero siguen en `free`.

**Cómo aplicar:** El `user_id` se incluye en `metadata` al crear la sesión de checkout. El webhook lo lee, busca al usuario en la DB y actualiza `tier` y la tabla `subscriptions`. El redirect de éxito solo muestra una pantalla de confirmación, no tiene lógica de negocio.

---

## 013 — APScheduler con SQLAlchemy job store para el motor de alertas

**Decisión:** `alerts_engine.py` usa APScheduler con job store en la misma PostgreSQL, no `schedule` con `while True`.

**Por qué:** Un `while True` + `time.sleep()` muere silenciosamente en Railway sin que nadie lo sepa — el proceso crashea y no hay reinicio automático. APScheduler con SQLAlchemy job store persiste los jobs en la DB, sobrevive reinicios y Railway puede gestionar el proceso como worker con restart policy.

**Cómo aplicar:** Arrancar como proceso separado: `python services/alerts_engine.py`. En Railway, añadir como segundo servicio (worker) en el mismo proyecto, con la misma `DATABASE_URL`.

---

## 014 — StripeObject no es un dict — usar json.loads(str(obj))

**Decisión:** En `_handle_stripe_event`, el objeto del evento se convierte con `json.loads(str(obj))` antes de usar `.get()`.

**Por qué:** `stripe.Webhook.construct_event()` devuelve un `StripeObject`, no un dict Python. Llamar `.get()` sobre él lanza `AttributeError`. La conversión via `str()` + `json.loads()` produce un dict plano estándar.

**Alternativa descartada:** Acceder con `obj["key"]` directamente — funciona para claves que existen pero sigue lanzando `KeyError` para opcionales, haciendo el código frágil.

---

## 015 — Estrategias de backtesting como JSON (no lambdas Python)

**Decisión:** Las condiciones de compra/venta se definen como JSON (`{"indicator": "rsi", "operator": "below", "value": 30}`) y se guardan tal cual en la columna `JSON` de PostgreSQL. No se usan lambdas ni funciones Python serializadas.

**Por qué:** Las lambdas Python no son serializables (no se pueden guardar en DB ni reconstruir después). Una estrategia guardada como JSON puede ser recuperada, mostrada al usuario, versionada y reproducida de forma idéntica en cualquier momento. Es el requisito de determinismo: misma estrategia + mismos datos = mismo resultado siempre.

**Indicadores soportados:** `rsi`, `sma20`, `sma50`, `macd_histogram`, `price`. Si el usuario pasa uno no soportado, `validate_strategy()` lanza `ValueError` con mensaje que lista los válidos — la API devuelve 400.

**Cómo aplicar:** Toda la lógica de evaluación vive en `services/backtester.py`. Si se añade un indicador nuevo, hay que actualizar `VALID_INDICATORS` y `_indicator_series()` — en ningún otro sitio.

---

## 016 — Límite de backtests por mes contado en DB (no en memoria)

**Decisión:** El límite de 3 backtests/mes para usuarios PRO se verifica consultando `backtest_results` en PostgreSQL (filtrando por `user_id` y `ran_at >= inicio_del_mes`), no con un contador en memoria o caché.

**Por qué:** La API corre como proceso stateless — no hay memoria compartida entre requests. Un contador en memoria se resetea con cada deploy o restart. La DB es la única fuente de verdad persistente.

**Cómo aplicar:** El endpoint `POST /api/v1/backtest` hace el `COUNT` antes de ejecutar. Si el count >= 3, devuelve 429. El registro se inserta en `backtest_results` solo si el backtest termina con éxito.

---

## 009 — SendGrid Personalizations API (batch, no loop)

**Decisión:** `send_bulk_newsletter()` construye un único payload con todas las `personalizations` y hace un solo request HTTP, no un loop de `send_per_user`.

**Por qué:** Un loop de N usuarios hace N requests HTTP — lento, más puntos de fallo y agota el rate limit de SendGrid con listas grandes. La API de Personalizations acepta hasta 1000 destinatarios por request; para listas mayores se usa batching interno automático.

**Cómo aplicar:** Si en el futuro se necesita personalizar el contenido por usuario (nombre, ticker favorito), se añaden campos dentro de cada objeto de `personalizations` — no hace falta cambiar la arquitectura.
