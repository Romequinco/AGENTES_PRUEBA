# Guía de Deploy — Railway.app

Guía paso a paso para hacer el primer deploy del sistema IBEX 35 en Railway.
No se asume experiencia previa con Railway.

---

## Variables de entorno necesarias

### Servicio `web` (API Flask)

| Variable | Dónde obtenerla | Obligatoria |
|---|---|---|
| `DATABASE_URL` | Railway → PostgreSQL plugin → Connect → `DATABASE_URL` | Sí |
| `JWT_SECRET_KEY` | Genera una cadena aleatoria ≥ 32 chars (`python -c "import secrets; print(secrets.token_hex(32))"`) | Sí |
| `SENDGRID_API_KEY` | SendGrid → Settings → API Keys → Create API Key (Full Access) | Sí |
| `SENDGRID_FROM_EMAIL` | Email verificado en SendGrid → Settings → Sender Authentication | Sí |
| `STRIPE_SECRET_KEY` | Stripe Dashboard → Developers → API Keys → Secret key (`sk_live_...`) | Sí (pagos) |
| `STRIPE_WEBHOOK_SECRET` | Stripe Dashboard → Developers → Webhooks → Signing secret (`whsec_...`) | Sí (pagos) |
| `STRIPE_PREMIUM_PRICE_ID` | Stripe Dashboard → Products → Plan Premium → Price ID (`price_...`) | Sí (pagos) |
| `STRIPE_PRO_PRICE_ID` | Stripe Dashboard → Products → Plan PRO → Price ID (`price_...`) | Sí (pagos) |
| `ADMIN_API_KEY` | Genera una cadena aleatoria segura | Sí (admin) |
| `ADMIN_EMAIL` | Tu email de administrador (donde recibirás alertas de error) | Sí (monitoring) |
| `STRIPE_SUCCESS_URL` | URL completa de la app Railway + `/dashboard.html` | Opcional |
| `STRIPE_CANCEL_URL` | URL completa de la app Railway + `/dashboard.html` | Opcional |

### Servicio `worker` (motor de alertas)

Las mismas variables que `web` (Railway permite compartirlas entre servicios del mismo proyecto).

Variables adicionales del worker (opcionales, tienen valor por defecto):

| Variable | Default | Descripción |
|---|---|---|
| `ALERTS_TIMEZONE` | `Europe/Madrid` | Timezone del scheduler |
| `ALERTS_HOUR` | `17` | Hora de evaluación de alertas |
| `ALERTS_MINUTE` | `35` | Minuto de evaluación de alertas |

### GitHub Actions (secrets del repositorio)

Añadir en GitHub → repositorio → Settings → Secrets and variables → Actions → New repository secret:

| Secret | Valor |
|---|---|
| `ANTHROPIC_API_KEY` | Clave API de Anthropic |
| `DATABASE_URL` | La misma que en Railway |
| `SENDGRID_API_KEY` | La misma que en Railway |
| `SENDGRID_FROM_EMAIL` | La misma que en Railway |
| `GMAIL_USER` | Tu cuenta Gmail para envío de PDF (existente en el workflow) |
| `GMAIL_APP_PASSWORD` | App password de Gmail (existente en el workflow) |

---

## Paso 1 — Crear el proyecto en Railway

1. Ve a [railway.app](https://railway.app) y accede con tu cuenta (o crea una).
2. Click en **New Project**.
3. Selecciona **Deploy from GitHub repo**.
4. Autoriza Railway a acceder a tu cuenta de GitHub si aún no lo has hecho.
5. Busca y selecciona el repositorio `AGENTES_PRUEBA` (o el nombre que tenga en tu cuenta).
6. Railway detectará el `railway.toml` automáticamente y creará los servicios `web` y `worker`.

---

## Paso 2 — Provisionar PostgreSQL

1. En tu proyecto Railway, click en **+ New** (arriba a la derecha).
2. Selecciona **Database** → **Add PostgreSQL**.
3. Railway crea la base de datos y la adjunta al proyecto.
4. En el plugin PostgreSQL, ve a la pestaña **Connect**.
5. Copia el valor de `DATABASE_URL` (empieza por `postgresql://...`).

---

## Paso 3 — Configurar variables de entorno

1. En el servicio `web`, ve a la pestaña **Variables**.
2. Añade todas las variables de la tabla "Servicio web" de arriba.
   - Railway inyecta `DATABASE_URL` automáticamente desde el plugin PostgreSQL —
     puedes referenciarla con `${{Postgres.DATABASE_URL}}` o añadirla manualmente.
   - `PORT` lo asigna Railway automáticamente — **no lo configures manualmente**.
3. Repite el mismo proceso para el servicio `worker` con las mismas variables.

---

## Paso 4 — Inicializar la base de datos

La primera vez, las tablas deben crearse manualmente. En Railway:

1. En el servicio `web`, ve a la pestaña **Deploy** → busca el botón **Run command** (o usa el Railway CLI).
2. Ejecuta:
   ```
   python -c "from dotenv import load_dotenv; load_dotenv(); from db.models import Base, engine; Base.metadata.create_all(engine); print('Tablas creadas')"
   ```
   O con Railway CLI local:
   ```bash
   railway run python -c "from db.models import Base, engine; Base.metadata.create_all(engine); print('Tablas creadas')"
   ```

---

## Paso 5 — Añadir secrets a GitHub Actions

1. Ve a tu repositorio en GitHub → **Settings** → **Secrets and variables** → **Actions**.
2. Click en **New repository secret** para cada uno de los secrets de la tabla "GitHub Actions" de arriba.
3. Los tres secrets nuevos de Fase 4 (`DATABASE_URL`, `SENDGRID_API_KEY`, `SENDGRID_FROM_EMAIL`) son necesarios para que el pipeline diario envíe la newsletter.

---

## Paso 6 — Configurar el webhook de Stripe

1. En Stripe Dashboard → **Developers** → **Webhooks** → **Add endpoint**.
2. URL del endpoint: `https://<tu-dominio>.railway.app/stripe/webhook`
   (el dominio lo encuentras en Railway → servicio web → Settings → Domains).
3. Eventos a escuchar:
   - `checkout.session.completed`
   - `customer.subscription.deleted`
   - `invoice.payment_failed`
4. Copia el **Signing secret** (`whsec_...`) y añádelo como `STRIPE_WEBHOOK_SECRET` en Railway.

---

## Paso 7 — Verificar el deploy

Una vez Railway haya terminado el deploy (barra de progreso en verde):

### Health check de la API
```bash
curl https://<tu-dominio>.railway.app/health
```
Respuesta esperada:
```json
{
  "status": "ok",
  "db": "connected",
  "sendgrid": "configured",
  "stripe": "configured",
  "timestamp": "2026-04-23T..."
}
```
Si `status` es `"degraded"`, revisa que `DATABASE_URL` esté bien configurada.

### Panel de administración
Abre en el navegador:
```
https://<tu-dominio>.railway.app/admin/metrics
```
Accede con el valor de `ADMIN_API_KEY` que configuraste. Deberías ver los KPIs del sistema.

### Logs del worker
En Railway → servicio `worker` → pestaña **Logs**. Deberías ver:
```
[ALERTS] Iniciando motor de alertas...
[ALERTS] Jobs programados: ['Evaluar alertas a las 17:35:00 Europe/Madrid', 'Generar reportes semanales PRO (lunes 08:00 Madrid)']
```

---

## Notas importantes

- **SSL/HTTPS**: Railway proporciona HTTPS automáticamente en dominios `.railway.app`. No necesitas configurar certificados.
- **Filesystem efímero**: Railway no persiste archivos entre deploys. Los PDFs generados (`output/`) se pierden al redesplegar. Si necesitas persistencia, sube los PDFs a S3 o similar.
- **Redeploy automático**: Railway redespliega automáticamente cuando haces push a la rama principal.
- **Scaling**: Con `--workers 2` en gunicorn, la API puede manejar hasta ~2 requests concurrentes por worker. Para más carga, aumenta el número de workers o activa el plan de Railway que permite múltiples réplicas.
