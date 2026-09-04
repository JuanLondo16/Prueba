# AbacusPlus API

Plataforma de procesamiento de facturas electrónicas DIAN y causación contable automática. Recibe facturas en formato XML/ZIP, las indexa para búsqueda semántica, y asigna cuentas del Plan Único de Cuentas (PUC) a cada línea de detalle mediante IA.

## Arquitectura

8 microservicios detrás de un gateway Nginx:

```
Cliente
  │
  └─ :8000  ──►  gateway (Nginx)
                   │   (enruta por prefijo; NO valida el JWT: lo hace cada servicio)
                   │
                   ├─ POST /api/v1/auth/login ──►  auth-service :8008
                   │  GET|POST /api/v1/tenants      (emite JWT RS256, tenants, usuarios)
                   │  GET  /api/v1/users
                   │
                   ├─ POST /api/v1/documents  ──►  xml-processor :8001
                   │  GET  /api/v1/documents        │  parsea ZIP/XML → PostgreSQL
                   │  GET  /api/v1/receivers         └─ POST /api/v1/chunks  ──►  rag-service :8002
                   │  GET  /api/v1/catalog                                          (indexa embedding)
                   │  POST /api/v1/batch-jobs
                   │
                   ├─ POST /api/v1/query      ──►  llm-service :8003
                   │  POST /api/v1/accounting        │  POST /chunks/search ──► rag-service :8002
                   │                                 │  GET  /integrations/chart-accounts
                   │                                 │         ──► integration-config-service :8007
                   │                                 └─ OpenAI API (asigna PUC por ítem)
                   │
                   ├─ GET|POST /api/v1/siigo  ──►  siigo-service :8006
                   │                                (credenciales, plan de cuentas,
                   │                                 factura de compra → SIIGO API · RF-05)
                   │
                   ├─ GET|POST /api/v1/integrations ──►  integration-config-service :8007
                   │
                   ├─ POST /api/v1/dian       ──►  session-proxy :8004
                   │
                   └─ GET|POST /api/v1/odoo   ──►  odoo-service :8005
```

## Servicios

| Servicio | Puerto | Responsabilidad |
|----------|--------|-----------------|
| [gateway](services/gateway/) | 8000 | Entrada única — proxy Nginx a todos los servicios |
| [xml-processor](services/xml-processor/README.md) | 8001 | Parseo ZIP/XML DIAN, persistencia, enriquecimiento de líneas |
| [rag-service](services/rag-service/README.md) | 8002 | Indexación y búsqueda vectorial con pgvector + Ollama |
| [llm-service](services/llm-service/README.md) | 8003 | Consultas RAG y asignación PUC por ítem vía OpenAI |
| [session-proxy](services/session-proxy/README.md) | 8004 | Autenticación DIAN, descarga de ZIPs, cola de trabajos |
| [odoo-service](services/odoo-service/README.md) | 8005 | Sincronización de facturas de compra desde Odoo |
| [siigo-service](services/siigo-service/README.md) | 8006 | Autenticación y sincronización de plan de cuentas SIIGO |
| [integration-config-service](services/integration-config-service/README.md) | 8007 | Catálogo agnóstico: cuentas PUC, centros de costo, credenciales |

## Quickstart

```bash
# Copiar y configurar variables de entorno
cp .env.example .env   # editar al menos OPENAI_API_KEY

# Generar el par de claves JWT (obligatorio: .env.example trae un placeholder, no una clave
# real, y sin esto el login falla con un error de la librería de criptografía). Pegar cada
# salida en JWT_PRIVATE_KEY / JWT_PUBLIC_KEY dentro de .env, en una sola línea, sin comillas
# y con \n literal entre renglones — es el mismo formato en el que ya está el placeholder.
openssl genrsa -out private.pem 2048
openssl rsa -in private.pem -pubout -out public.pem
awk 'NF {sub(/\r/, ""); printf "%s\\n",$0;}' private.pem   # copiar en JWT_PRIVATE_KEY
awk 'NF {sub(/\r/, ""); printf "%s\\n",$0;}' public.pem    # copiar en JWT_PUBLIC_KEY
rm private.pem public.pem

# Generar INTERNAL_SECRET (obligatorio: .env.example trae el placeholder "change-me", no un
# secreto real. Todos los endpoints /internal/* de cada microservicio lo exigen para aceptar
# llamadas entre servicios — incluyendo el aprovisionamiento de tenants nuevos. Con el
# placeholder, cada servicio rechaza esas llamadas con 403 sin avisar por qué, y un tenant
# recién creado queda con tablas a medio crear). Reemplazar INTERNAL_SECRET en .env con:
openssl rand -hex 32

# Levantar todos los servicios
docker-compose up --build

# Documentación interactiva (Swagger)
open http://localhost:8000/docs

# Health check
curl http://localhost:8000/health
```

## Arrancar con datos reales (tenants + documentos DIAN ya procesados)

Si alguien te pasó un backup (por ejemplo, para que veas exactamente los mismos datos que
otro miembro del equipo, en vez de un tenant vacío), el flujo completo es:

1. Pegar en `.env` las claves `JWT_PRIVATE_KEY`, `JWT_PUBLIC_KEY` e `INTERNAL_SECRET` que te
   pasen manualmente (ver arriba — siguen siendo 100% manuales, no hay generación automática).
2. Copiar el backup (**un solo archivo `.dump`**, el nombre no importa) a `backups/tenants/`.
   `backups/` está en `.gitignore`, igual que `.env`: nunca viaja por git.
3. `docker compose up -d --build`.

Nada más — un solo archivo, tres pasos. Al arrancar, el servicio `backup-restore` (ver
`scripts/restore-backups.sh`) lee el nombre de la base directamente del propio `.dump`
(`pg_restore -l`), restaura esa base y registra el tenant en `abacus_meta` — sin `docker cp`,
sin `psql -f`, sin `pg_restore` a mano, y sin que el archivo necesite un nombre específico. Es
idempotente: si una base de tenant ya existe (porque ya se restauró antes, o porque ya
generaste datos nuevos trabajando), la deja intacta.

Si `backups/` está vacío o no existe, este paso simplemente no hace nada — un
`docker compose up` normal, sin backup, arranca con la base vacía de siempre.

## Variables de entorno esenciales

```env
# Base de datos (PostgreSQL + pgvector)
DATABASE_HOST=database
DATABASE_PORT=5432
DATABASE_USER=master
DATABASE_PASSWORD=master
DATABASE_NAME=abacus

# OpenAI — requerido para asignación PUC y consultas RAG
OPENAI_API_KEY=sk-...

# Ollama — embeddings locales (levanta automáticamente con docker-compose)
OLLAMA_HOST=http://ollama:11434
OLLAMA_EMBED_MODEL=nomic-embed-text

# SIIGO (opcional, solo si se usa integración SIIGO)
SIIGO_BASE_URL=https://api.siigo.com
```

Ver `.env` para la lista completa de variables.

## Testing

Cada servicio tiene su propio directorio `tests/`. Los tests no requieren servicios externos levantados (usan mocks o SQLite in-memory).

```bash
# xml-processor
docker run --rm -v "$(pwd)/services/xml-processor:/app" --workdir //app python:3.9-slim \
  bash -c "pip install -r requirements.txt -q && python -m pytest tests/ -v"

# rag-service
docker run --rm -v "$(pwd)/services/rag-service:/app" --workdir //app python:3.9-slim \
  bash -c "pip install -r requirements.txt -q && python -m pytest tests/ -v"

# llm-service
docker run --rm -v "$(pwd)/services/llm-service:/app" --workdir //app python:3.9-slim \
  bash -c "pip install -r requirements.txt -q && python -m pytest tests/ -v"
```

## Infraestructura

- **PostgreSQL 16 + pgvector** — base de datos principal y búsqueda vectorial (`<=>` coseno)
- **Ollama** (`nomic-embed-text`, 768 dims) — embeddings locales, descarga automática al primer `up`
- **Redis** — cola de trabajos para descargas DIAN en batch (arq worker)
- **Playwright** — automatización del portal DIAN en session-proxy
