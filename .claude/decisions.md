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

## 017 — API Flask dividida en blueprints (no monolito)

**Decisión:** `api/flask_app.py` es una app factory que solo registra blueprints. Cada dominio vive en su propio archivo: `auth.py`, `newsletter.py`, `premium.py`, `pro.py`, `stripe.py`. Los helpers compartidos (`get_db`, `require_premium`, `require_pro`) están en `helpers.py`.

**Por qué:** Con Fases 1-3, `flask_app.py` llegó a 950 líneas mezclando auth, newsletter, alertas, Stripe, estrategias, portfolios y reportes. Tocar Stripe implicaba abrir el mismo archivo que el backtester. Los blueprints separan responsabilidades, reducen conflictos en git y hacen cada archivo navegable de forma independiente.

**Cómo aplicar:** Añadir un endpoint nuevo → identificar su blueprint, añadirlo allí. Añadir un dominio nuevo → crear `api/nuevo_dominio.py` con su Blueprint y registrarlo en `create_app()`. Nunca añadir lógica de negocio directamente en `flask_app.py`.

**Nota:** Las URLs externas no cambiaron — los blueprints no tienen `url_prefix` salvo `/auth` y `/stripe`, que ya formaban parte de las rutas originales.

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

## 018 — Rate limiting del monitoring en memoria (no en DB)

**Decisión:** `services/monitoring.py` guarda los timestamps de los últimos emails de error en un dict Python en memoria (`_last_sent`). No usa Redis, no usa la DB.

**Por qué:** El objetivo es evitar que un error en bucle mande cientos de emails al administrador. Para eso, un dict en memoria es suficiente y tiene coste cero. La DB añadiría latencia y dependencia circular (si la DB falla, el monitoring que informa del fallo también fallaría). La pérdida del rate limit al reiniciar el proceso es aceptable — en el peor caso llega un email extra tras un restart.

**Cómo aplicar:** `_RATE_LIMIT_SECONDS = 3600` (1 hora). La clave es `f"{error_message[:120]}:{context[:60]}"`. Cambiar si hace falta granularidad por tipo de error o por usuario.

---

## 019 — ADMIN_API_KEY como auth del panel de admin (no JWT)

**Decisión:** `/admin/metrics` se protege con un header `X-Admin-Key` que debe coincidir con la variable de entorno `ADMIN_API_KEY`. No usa JWT ni sesiones.

**Por qué:** El panel de admin es un endpoint de uso interno, no de usuarios. JWT requiere un flujo de login previo y tiene sentido para usuarios con identidades distintas. Una API key de admin es más simple, auditable y suficiente para el caso de uso: una sola persona con acceso total. Si la key no está configurada, el endpoint devuelve 503 (no 401) para distinguir "mal configurado" de "key incorrecta".

**Cómo aplicar:** Nunca exponer `ADMIN_API_KEY` en el frontend público. El `admin_dashboard.html` guarda la key en memoria de sesión JS (no en `localStorage`) para que se borre al cerrar la pestaña.

---

## 020 — gunicorn como servidor WSGI en producción (no Flask dev server)

**Decisión:** En Railway, la API arranca con `gunicorn api.flask_app:app --workers 2 --bind 0.0.0.0:$PORT`. El servidor de desarrollo de Flask (`app.run()`) no se usa en producción.

**Por qué:** El servidor de Flask es single-threaded y no está diseñado para carga real. gunicorn gestiona múltiples workers, maneja señales de OS correctamente, tiene mejor gestión de timeouts y es el estándar para Flask/WSGI en producción. Railway asigna `$PORT` automáticamente — gunicorn debe leer esa variable, nunca hardcodear el puerto.

**Cómo aplicar:** `--workers 2` es conservador para el plan básico de Railway. Con más RAM disponible, aumentar a `2 * CPU + 1`. El `--timeout 120` previene que requests que llaman a yfinance (lento) maten al worker.

---

## 021 — Reportes semanales PRO en el mismo worker de alertas (no proceso separado)

**Decisión:** El job de generación de reportes semanales PRO (`_generate_weekly_reports`) se añade al mismo APScheduler de `alerts_engine.py`, no como un cron de GitHub Actions ni como un proceso separado.

**Por qué:** Ya existe un worker de APScheduler corriendo en Railway. Añadir un job al scheduler existente es gratis en términos de infraestructura. Un cron de GitHub Actions requeriría un `DATABASE_URL` y acceso a yfinance desde el runner de CI, mezclaría responsabilidades (CI es para el pipeline de informe, no para tareas de usuarios) y añadiría latencia impredecible. Un proceso separado duplicaría la complejidad de Railway sin beneficio.

**Cómo aplicar:** Si un reporte individual falla, se loguea el error y se continúa con el siguiente usuario — el job nunca aborta por un fallo parcial. El scheduler tiene `@monitor_errors` para avisar si el job completo falla al arrancar.

---

## 022 — /health como contrato de Railway (nunca lanza excepción)

**Decisión:** El endpoint `/health` captura cualquier excepción interna y devuelve `{"status": "degraded"}` en lugar de propagar el error. Nunca devuelve 5xx.

**Por qué:** Railway usa `/health` (configurado en `railway.toml`) para decidir si el servicio está sano y si debe reiniciarlo. Si `/health` devuelve 500, Railway entra en bucle de reinicios aunque el resto de la app funcione correctamente. Un 200 con `status: degraded` informa al administrador del problema sin causar reinicios innecesarios.

**Cómo aplicar:** Si se añaden nuevas dependencias al sistema (Redis, un servicio externo), añadir su check al `/health`. El campo `db` comprueba la conexión con un `SELECT 1`. Los campos `sendgrid` y `stripe` solo comprueban que las keys estén configuradas, no hacen requests reales (evita latencia y throttling).

---

## 009 — SendGrid Personalizations API (batch, no loop)

**Decisión:** `send_bulk_newsletter()` construye un único payload con todas las `personalizations` y hace un solo request HTTP, no un loop de `send_per_user`.

**Por qué:** Un loop de N usuarios hace N requests HTTP — lento, más puntos de fallo y agota el rate limit de SendGrid con listas grandes. La API de Personalizations acepta hasta 1000 destinatarios por request; para listas mayores se usa batching interno automático.

**Cómo aplicar:** Si en el futuro se necesita personalizar el contenido por usuario (nombre, ticker favorito), se añaden campos dentro de cada objeto de `personalizations` — no hace falta cambiar la arquitectura.
