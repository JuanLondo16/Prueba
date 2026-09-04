# CLAUDE.md

Guía de contexto para Claude Code en este repositorio.

## Comandos rápidos

```bash
# Levantar todos los servicios
docker-compose up --build

# Levantar un solo servicio
docker-compose up --build xml-processor

# Ejecutar tests de un servicio
docker run --rm -v "$(pwd)/services/xml-processor:/app" --workdir //app python:3.9-slim \
  bash -c "pip install -r requirements.txt -q && python -m pytest tests/ -v"

# Instalar dependencias localmente (desarrollo)
pip install -r services/<servicio>/requirements.txt

# Crear un tenant de demostración con credenciales conocidas (SOLO DESARROLLO — no arranca
# con un `docker-compose up` normal, hay que activar el profile "dev" a propósito). Deja el
# proyecto usable justo después de clonarlo, sin restaurar ningún respaldo de base de datos
# a mano. Ver DEMO_TENANT_* en .env.example para cambiar slug/credenciales.
docker-compose --profile dev up demo-tenant-init

# Arrancar con tenants y documentos DIAN reales (no el tenant demo vacío de arriba): copiar
# UN SOLO archivo .dump (el nombre no importa) a backups/tenants/ (ver README.md §
# "Arrancar con datos reales") y correr el `docker-compose up -d --build` normal — el
# servicio backup-restore (scripts/restore-backups.sh) lee el nombre de la base desde el
# propio .dump, la restaura y registra el tenant en abacus_meta, sin --profile ni pasos
# manuales ni un segundo archivo. Idempotente: no toca una base de tenant que ya exista.
```

## Arquitectura — Microservicios

El proyecto sigue una arquitectura de **microservicios**, cada uno con su propia
estructura hexagonal (Ports & Adapters). Los servicios se comunican vía **HTTP síncrono** (httpx).

```
Cliente
  │
  └─ :8000  ──►  gateway (Nginx)
                   │   Enruta por prefijo. NO valida el JWT: cada servicio lo valida
                   │   con la clave pública y resuelve su base por tenant_slug.
                   │
                   ├─ POST /api/v1/auth/login ──►  auth-service :8008
                   │  POST /api/v1/auth/refresh     (JWT RS256, tenants, usuarios)
                   │  GET|POST /api/v1/tenants
                   │  GET  /api/v1/users
                   │
                   ├─ POST /api/v1/documents  ──►  xml-processor :8001
                   │  GET  /api/v1/documents        │  procesa ZIP/XML → PostgreSQL
                   │  GET  /api/v1/receivers         └─ POST /api/v1/chunks  ──►  rag-service :8002
                   │  GET  /api/v1/issuers                                          (indexa embedding)
                   │  GET  /api/v1/catalog
                   │  POST /api/v1/batch-jobs       ├─ POST /api/v1/siigo/purchase-invoices
                   │  GET  /api/v1/batch-logs       │     ──► siigo-service (RF-05/06)
                   │  POST /api/v1/documents/       └─ S3 Lambda (PDF/XML · RF-03)
                   │       accounting-entries
                   │
                   ├─ POST /api/v1/query      ──►  llm-service :8003
                   │  POST /api/v1/analyses          │  POST /api/v1/chunks/search ──► rag-service :8002
                   │  POST /api/v1/accounting        │  GET  /api/v1/integrations/chart-accounts
                   │                                 │         ──► integration-config-service :8007
                   │                                 └─ OpenAI API (asigna cuenta PUC por ítem)
                   │
                   ├─ POST /api/v1/chunks     ──►  rag-service :8002  (debug/admin)
                   │
                   ├─ GET|POST /api/v1/odoo   ──►  odoo-service :8005
                   │                                (sync facturas compra, asientos)
                   │
                   ├─ GET|POST /api/v1/siigo  ──►  siigo-service :8006
                   │                                (credenciales, plan de cuentas,
                   │                                 factura de compra · RF-05)
                   │                                 └─ SIIGO API (api.siigo.com)
                   │
                   ├─ GET|POST /api/v1/integrations ──►  integration-config-service :8007
                   │                                      (credenciales, catálogos, import)
                   │
                   ├─ POST /api/v1/dian       ──►  session-proxy :8004
                   │  POST /api/v1/proxy            (auth DIAN, descarga ZIPs, cola arq)
                   │
                   └─ GET  /health/*
```

## Estructura de carpetas

```
api/
├── services/
│   ├── gateway/                # Puerto 8000 (entrada única)
│   │   └── nginx.conf
│   │
│   ├── xml-processor/          # Puerto 8001
│   │
│   ├── xml-processor/          # Puerto 8001
│   │   ├── app/
│   │   │   ├── adapters/api/routers/   xml.py · documents.py · receivers.py
│   │   │   ├── application/use_cases/  process_xml.py · query_documents.py · query_receivers.py
│   │   │   ├── application/dto/        document.py · receiver.py
│   │   │   ├── domain/                 entities/ · exceptions/ · ports/ · value_objects/
│   │   │   ├── infrastructure/
│   │   │   │   ├── clients/            rag_client.py · llm_client.py · integration_config_client.py
│   │   │   │   ├── config/             database.py · logging.py
│   │   │   │   └── persistence/        models/ · repositories/
│   │   │   └── utils/                  xml_parser.py · zip_handler.py · dian_dv.py · smart_match.py
│   │   ├── tests/
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   │
│   ├── rag-service/            # Puerto 8002
│   │   ├── app/
│   │   │   ├── adapters/api/routers/   chunks.py  (POST /chunks, POST /chunks/search)
│   │   │   ├── application/use_cases/  index_chunk.py · search_chunks.py
│   │   │   ├── application/dto/        chunk.py
│   │   │   ├── domain/
│   │   │   │   ├── entities/           chunk.py  (ChunkEntity)
│   │   │   │   └── ports/              repositories.py · services.py (EmbeddingServicePort)
│   │   │   └── infrastructure/
│   │   │       ├── ai/                 ollama_service.py  ← OllamaEmbeddingService
│   │   │       ├── config/             database.py · logging.py
│   │   │       └── persistence/
│   │   │           ├── models/         chunk.py  (DocumentChunk + Vector(768))
│   │   │           └── repositories/   chunk_repository.py  (pgvector cosine search)
│   │   ├── tests/
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   │
│   ├── llm-service/            # Puerto 8003
│   │   ├── app/
│   │   │   ├── adapters/api/routers/   analyze.py · query.py · accounting.py
│   │   │   ├── application/use_cases/  analyze_with_ai.py · query_with_rag.py · assign_account_codes.py
│   │   │   ├── application/dto/        ai.py · query.py · accounting.py
│   │   │   ├── domain/ports/           services.py  (AIServicePort · RagClientPort)
│   │   │   └── infrastructure/
│   │   │       ├── ai/                 openai_service.py
│   │   │       ├── clients/            rag_client.py · document_client.py · integration_config_client.py
│   │   │       └── config/             logging.py
│   │   ├── tests/
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   │
│   ├── session-proxy/          # Puerto 8004
│   │   ├── app/
│   │   │   ├── adapters/api/routers/   dian.py · proxy.py
│   │   │   ├── application/use_cases/  auth.py · download.py
│   │   │   ├── domain/                 entities/ · ports/
│   │   │   └── infrastructure/
│   │   │       ├── browser/            playwright_client.py
│   │   │       ├── config/             settings.py
│   │   │       └── workers/            download_worker.py  ← arq worker
│   │   ├── tests/
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   │
│   ├── odoo-service/           # Puerto 8005
│   │   ├── app/
│   │   │   ├── adapters/api/routers/   odoo.py
│   │   │   ├── application/use_cases/  sync_invoices.py · match_entries.py
│   │   │   ├── domain/                 entities/ · ports/
│   │   │   └── infrastructure/
│   │   │       ├── clients/            odoo_client.py
│   │   │       ├── config/             database.py
│   │   │       └── persistence/        models/ · repositories/
│   │   ├── tests/
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   │
│   ├── siigo-service/          # Puerto 8006
│   │   ├── app/
│   │   │   ├── adapters/api/routers/   chart_accounts.py · credentials.py · purchase_invoice_parameters.py
│   │   │   ├── application/use_cases/  sync_chart_accounts.py · authenticate.py
│   │   │   ├── domain/                 entities/ · ports/
│   │   │   └── infrastructure/
│   │   │       ├── clients/            siigo_client.py
│   │   │       ├── config/             database.py
│   │   │       └── persistence/        models/ · repositories/
│   │   ├── tests/
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   │
│   └── integration-config-service/  # Puerto 8007
│       ├── app/
│       │   ├── adapters/api/routers/   credentials.py · cost_centers.py · chart_accounts.py · purchase_invoice_parameters.py
│       │   ├── application/use_cases/  manage_credentials.py · import_cost_centers.py · import_chart_accounts.py
│       │   ├── domain/                 entities/ · ports/
│       │   └── infrastructure/
│       │       ├── config/             database.py
│       │       └── persistence/        models/ · repositories/
│       ├── tests/
│       ├── Dockerfile
│       └── requirements.txt
│
├── scripts/
│   └── init-db.sql             # CREATE EXTENSION IF NOT EXISTS vector
├── docker-compose.yml
├── .env
└── CLAUDE.md
```

## Flujo de datos

### Procesamiento de una factura
1. Cliente sube ZIP/XML → `gateway :8000` → `xml-processor`
2. `xml-processor` parsea, valida, guarda en PostgreSQL
3. `xml-processor` enriquece cada línea: asigna `tax_id` y `cost_center_id` por historial — **best-effort**
4. `xml-processor` llama a `llm-service` (`POST /api/v1/accounting/code-assignments/{id}`) — **best-effort**
5. `llm-service` consulta el PUC en `integration-config-service`, llama a OpenAI y escribe `code`/`type` en cada línea de `document_details`

**Procesar NO indexa nada en el RAG** (RF-08): ver el flujo de aprendizaje más abajo.

### Aprendizaje del RAG (RF-08)
El RAG solo aprende de causaciones **contabilizadas en SIIGO**:
1. El documento se contabiliza (RF-05) o se cierra por reconciliación (RF-06) → estado `Contabilizada` (400) con `siigo_id`
2. `AccountingKnowledgePublisher` construye el texto de la **causación final enviada a SIIGO** —cuentas, tercero, centro de costo y retenciones con tipo, concepto, base, tarifa y valor—
3. Lo indexa en `rag-service` (`POST /internal/chunks` con `is_validated=true` y `siigo_id`) — **best-effort**
4. `llm-service` recupera solo ese conocimiento (`only_validated=true`) para sugerir cuentas (RF-01) y retenciones (RF-02)

**Búsqueda híbrida.** Cada caso se indexa con `metadata` JSONB (NIT del emisor, municipio,
cuentas, tipos de retención). La recuperación filtra primero por esos rasgos —el embedding no
sabe qué es un NIT— y ordena por similitud dentro del resultado. Dos pasadas: mismo proveedor
y, si no hay historial suyo, mismo concepto con otros proveedores (marcado como tal).

**Jerarquía de fuentes.** La evidencia llega al prompt separada y rotulada, nunca mezclada:

| # | Fuente | Fuerza | Dónde vive |
|---|--------|--------|------------|
| 0 | Catálogo de **Impuestos** | Vinculante · define qué existe | `integration_taxes` (integration-config-service) |
| 1 | Tarifas de ReteFuente / ReteICA | Vinculante · define cuánto | `retention_*_rates` (xml-processor) |
| 2 | Perfil fiscal de la empresa | Vinculante · define si procede | `tenant_fiscal_profile` |
| 3 | Criterios del contador | Orientativo | `retention_criteria` (integration-config-service) |
| 4 | Casos contabilizados similares | Precedente, no norma | `document_chunks` (rag-service) |
| 5 | Conocimiento conceptual de ReteICA | Contextual · educativo · **nunca** fuente de tarifas | `llm-service · domain/knowledge/reteica_knowledge.py` |

**La sección Impuestos es la fuente 0, y se lee clasificada.** Es el único origen del
`tax_id` y del porcentaje de cualquier retención: el modelo elige de esa lista y de ninguna
otra. Entraba como lista plana de la que solo se descartaba el IVA por el texto de su tipo, y
en el catálogo real del cliente eso ofrecía `Impoconsumo 8%` y `autorretencion` como
retenciones proponibles —el impoconsumo es un impuesto del documento, y la autorretención se
calcula sobre las ventas propias, no sobre las compras—. Ahora
`domain/services/tax_catalog.py` clasifica cada fila (`retefuente` · `reteica` · `reteiva` ·
`iva` · `impoconsumo` · `autorretencion`, con la misma nomenclatura que usa el xml-processor
al indexar), deja pasar solo las tres que un comprador practica y colapsa las filas gemelas
que deja combinar la sincronización de SIIGO con una importación de Excel («IVA 19%» / «IVA
19%.»), quedándose siempre con el `id` menor para que la sugerencia sea repetible.

**Los impuestos del propio documento son contexto actual, no historial.** Cada línea llega ya
enlazada al catálogo por su `tax_id`, pero eso no salía del backend: al modelo solo le llegaba
`total_taxes`, que suma **todos** los impuestos del documento. Usarlo como base de la ReteIVA
retiene sobre un importe que no es el IVA en cuanto la factura trae impoconsumo o INC de
bolsas. El prompt recibe ahora `documento.impuestos` —IVA real, desglose por clase y por
renglón— y la base de la ReteIVA sale de ahí. Si ninguna línea está enlazada al catálogo, el
IVA viaja como `null` y el modelo debe declararlo en `missing_information` en vez de suponerlo.

**El conocimiento conceptual de ReteICA es la fuente 5, y no puede ascender.** El ICA es
territorial y su tarifa la fija cada municipio por actividad; el prompt decía *qué hacer* con
la tabla pero no *qué es* el tributo, y sin esa noción el modelo trataba la tabla como un
catálogo cualquiera: cuando la fila del concepto no aparecía, tendía a rellenar el hueco con
una tarifa «razonable» de conocimiento general en vez de declarar el faltante. El corpus
—curado a partir del artículo de Siigo «¿Qué es el ReteICA y cuándo se aplica?»— explica la
territorialidad, el papel de la actividad económica/CIIU, las bases mínimas, la base gravable
y la aritmética del cálculo. Sus **cifras viajan en un campo `ejemplo_ilustrativo` separado
del cuerpo conceptual** (que no contiene ningún número, fijado por prueba): el 0,772 % del
artículo ilustra la conversión a decimal, no es una tarifa aplicable a ninguna empresa. El
bloque no puede ser `evidence` de una sugerencia, y solo entra en el prompt cuando ReteICA es
candidata. No se indexa en el RAG a propósito: ese índice solo contiene causaciones
contabilizadas (`is_validated=true` con `siigo_id`), y meter doctrina ahí exigiría romper esa
invariante o inventarle un `siigo_id` a un artículo.

**Un precedente contabilizado solo informa si las condiciones son comparables.** Cada caso
llega con un bloque `comparabilidad` calculado de forma determinística —no a cargo del
modelo— que dice si su municipio es uno de aquellos donde la empresa retiene ICA y enumera lo
que hay que verificar antes de reutilizarlo: municipio, actividad, tipo de tercero,
naturaleza de la operación y tarifa vigente. `municipio_comparable` es tri-estado: `null`
significa «no consta» —el indexador se abstiene de etiquetar cuando la empresa retiene en
varios municipios— y nunca «coincide».

La inferencia del modelo va por debajo de todas ellas. Un caso histórico **nunca** sobreescribe
una tarifa vigente, y el prompt lo declara explícitamente.

**Los criterios del contador son datos por empresa**, no una constante: cada contador tiene los
suyos y cambian con la norma. Se siembran al aprovisionar (no destructivo) y se editan con
`PUT /api/v1/integrations/retention-criteria` sin desplegar. Se cargan **todos** en cada
sugerencia, nunca por similitud: son reglas que gobiernan cada factura, y hacerlas depender de
una búsqueda semántica significaría que algún día no llegan sin que nadie lo note.

**Nada tributario se actualiza desplegando.** Las tarifas viven en `retention_*_rates` y se
cargan desde Excel, así que un año nuevo —o un decreto a mitad de año, como el 572 de 2025
que volvió a regir el 1 de julio de 2026— se resuelve importando la tabla. La **UVT** sigue el
mismo camino en lugar de ser una constante: se deduce de la propia tabla importada, cuyas
columnas de tope en UVT y en pesos, divididas, dan la UVT con que el contador la construyó
(mediana, para que una fila mal escrita no desplace el cálculo). `_UVT_POR_ANIO` queda como
último recurso cuando la tabla no permite deducirla. Y ante dos formas del mismo tope, manda
el importe que el contador cargó: convertir desde UVT es el respaldo para lo que la tabla no
trae —como la de ReteICA, que solo guarda el tope en UVT—.

**Validación determinística.** Lo que el modelo devuelve pasa por
`domain/services/retention_validation.py` antes de mostrarse y antes de persistirse. El
prompt le pide tomar la tarifa de la tabla, respetar la base mínima y no retenerle a un
autorretenedor; esa capa comprueba que lo hizo, porque una instrucción no es una garantía y
lo que está en juego no es una respuesta peor sino dinero retenido de más a un tercero real.
Se descarta —nunca se corrige— la sugerencia cuyo porcentaje no aparece en la tabla oficial
correspondiente, cuya base no alcanza el tope más bajo de las filas compatibles, la
ReteFuente a un emisor con O-15 (autorretenedor) en el RUT y la ReteIVA de una factura sin
IVA. Cada descarte viaja en `warnings` con su motivo.

**Trazabilidad.** Cada sugerencia devuelve `evidence` (qué fuente la sustenta) y `confidence`;
la respuesta trae `evidence_used` con los comprobantes SIIGO de los precedentes consultados y
`missing_information` con lo que al modelo le faltó para decidir.

Un documento en `Procesado`, `Causado` o `Aprobado`, o cuyo envío a SIIGO falló, **no genera
conocimiento**. Si una causación contabilizada deja de ser válida, se retira con
`POST /internal/chunks/revoke`. El backfill `POST /internal/documents/reindex` del
xml-processor reconstruye el estado correcto: indexa los contabilizados y retira el resto.

### Consulta RAG
1. Cliente envía pregunta → `gateway :8000` → `llm-service`
2. `llm-service` llama a `rag-service` (`POST /api/v1/chunks/search`)
3. `rag-service` genera embedding de la query y retorna top-k chunks por similitud coseno
4. `llm-service` construye prompt aumentado y llama a OpenAI
5. Retorna respuesta + chunks utilizados + usage

## Endpoints por servicio

### gateway (:8000) — entrada única para clientes
| Método | Path | Destino |
|--------|------|---------|
| POST | `/api/v1/documents` | xml-processor |
| GET  | `/api/v1/documents/` | xml-processor |
| GET  | `/api/v1/documents/{id}` | xml-processor |
| GET  | `/api/v1/documents/{id}/full` | xml-processor |
| PATCH| `/api/v1/documents/{id}` | xml-processor |
| PATCH| `/api/v1/documents/{id}/approve` | xml-processor |
| GET  | `/api/v1/receivers` | xml-processor |
| GET  | `/api/v1/issuers/{nit}` | xml-processor |
| GET  | `/api/v1/catalog/*` | xml-processor |
| POST | `/api/v1/batch-jobs/*` | xml-processor |
| GET  | `/api/v1/batch-logs` | xml-processor |
| POST | `/api/v1/query` | llm-service |
| POST | `/api/v1/analyses` | llm-service |
| POST | `/api/v1/accounting/*` | llm-service |
| GET  | `/api/v1/accounting/*` | llm-service |
| PATCH| `/api/v1/accounting/*` | llm-service |
| POST | `/api/v1/chunks` | rag-service |
| POST | `/api/v1/chunks/search` | rag-service |
| GET  | `/api/v1/odoo/*` | odoo-service |
| POST | `/api/v1/odoo/*` | odoo-service |
| GET  | `/api/v1/siigo/*` | siigo-service |
| POST | `/api/v1/siigo/*` | siigo-service |
| GET  | `/api/v1/integrations/*` | integration-config-service |
| POST | `/api/v1/integrations/*` | integration-config-service |
| PUT  | `/api/v1/integrations/*` | integration-config-service |
| POST | `/api/v1/dian/*` | session-proxy |
| GET  | `/api/v1/dian/*` | session-proxy |
| DELETE | `/api/v1/dian/*` | session-proxy |
| POST | `/api/v1/proxy/*` | session-proxy |
| GET  | `/health` | gateway |
| GET  | `/health/xml-processor` | xml-processor |
| GET  | `/health/rag-service` | rag-service |
| GET  | `/health/llm-service` | llm-service |
| GET  | `/health/session-proxy` | session-proxy |
| GET  | `/health/odoo-service` | odoo-service |
| GET  | `/health/siigo-service` | siigo-service |
| GET  | `/health/integration-config-service` | integration-config-service |

### xml-processor (:8001) — interno
| Método | Path | Descripción |
|--------|------|-------------|
| POST | `/api/v1/documents` | Procesa ZIP o XML DIAN, crea documento |
| GET  | `/api/v1/documents` | Lista documentos (`?from_date=&to_date=`) |
| GET  | `/api/v1/documents/{id}` | Detalle de un documento |
| GET  | `/api/v1/documents/{id}/full` | Documento + líneas de detalle con cuentas PUC asignadas |
| PATCH| `/api/v1/documents/{id}/details` | Actualiza cuentas PUC en líneas de detalle (llamado por llm-service) |
| PATCH| `/api/v1/documents/{id}/approve` | Aprueba documento (Causado → Aprobado) |
| PATCH| `/api/v1/documents/{id}` | Actualiza estado (`{"status": 200}` revierte a Causado) |
| PATCH| `/api/v1/documents/{id}/cost-center` | RF-07: centro de costo del documento (opcional) |
| PATCH| `/api/v1/documents/{id}/payment-type` | Medio de pago del documento |
| GET·POST | `/api/v1/documents/{id}/taxes` | RF-02: retenciones del documento |
| GET·PATCH·DELETE | `/api/v1/documents/{id}/taxes/{tax_id}` | RF-02: retención individual |
| POST | `/api/v1/documents/{id}/accounting-entries` | **RF-05**: contabiliza un documento en SIIGO, síncrono (`?force=` salta el cerrojo; solo tras verificar en SIIGO) |
| POST | `/api/v1/documents/accounting-entries` | **RF-05**: **encola** un lote y responde `202` con `batch_id` |
| GET  | `/api/v1/documents/accounting-batches/{batch_id}` | **RF-05**: progreso del lote encolado |
| GET  | `/api/v1/documents/{id}/accounting-attempts` | **RF-05**: auditoría — cada intento contra SIIGO y cada corrección manual |
| POST | `/api/v1/documents/{id}/file-links` | RF-03: sube PDF/XML a S3 y guarda el enlace |
| POST | `/api/v1/documents/file-links` | RF-03: publicación por lotes |
| GET  | `/api/v1/documents/{id}/pdf` | Descarga el PDF almacenado |
| GET  | `/api/v1/documents/{id}/xml` | Descarga el XML oficial |
| GET  | `/api/v1/receivers` | Lista receptores |
| GET  | `/api/v1/issuers/{nit}` | Datos del emisor por NIT |
| GET  | `/api/v1/catalog/cost-centers` | Centros de costo activos |
| GET  | `/api/v1/catalog/puc-accounts` | Cuentas PUC activas |
| GET  | `/api/v1/catalog/retention-fuente-rates` | Tasas retención en la fuente |
| GET  | `/api/v1/catalog/retention-ica-rates` | Tasas retención ICA. **Fuente única de los municipios donde la empresa retiene ICA**. Clave `(municipio, concepto)`: un municipio trae una fila por concepto (servicios, compras, honorarios…), porque la tarifa la fija la actividad. Cada fila lleva además su `minimum_base_uvt`: el ICA es territorial y cada municipio fija su propio tope (Bogotá 4/27, Cali 3/15, Bucaramanga 25/50) |
| GET  | `/api/v1/catalog/taxes` | Catálogo de impuestos |
| POST | `/api/v1/catalog/retention-rates/imports` | Importa tasas desde .xlsx. `sheet=fuente\|ica` acota la importación a una hoja y rechaza el archivo si no la trae — evita que subir el archivo equivocado cargue la tabla que no es |
| GET  | `/api/v1/catalog/retention-rates/template` | Plantilla .xlsx. `?sheet=fuente\|ica` devuelve solo esa hoja (cada tabla de la interfaz descarga la suya) |
| POST | `/api/v1/batch-jobs/downloads` | Encolar ZIPs del directorio downloads |
| POST | `/api/v1/batch-jobs/file` | Encolar un ZIP específico |
| GET  | `/api/v1/batch-logs` | Historial de procesamiento batch |
| GET  | `/health` | Health check |

### rag-service (:8002) — interno
| Método | Path | Descripción |
|--------|------|-------------|
| POST | `/api/v1/chunks` | Indexa un fragmento de texto con embedding (rechaza `is_validated`) |
| POST | `/api/v1/chunks/search` | Búsqueda semántica top-k (`only_validated` filtra a RF-08) |
| POST | `/internal/chunks` | **RF-08**: indexación servicio-a-servicio; única vía del conocimiento validado |
| POST | `/internal/chunks/revoke` | **RF-08**: retira el conocimiento de un documento |
| GET  | `/health` | Health check |

### llm-service (:8003) — interno
| Método | Path | Descripción |
|--------|------|-------------|
| POST | `/api/v1/query` | Consulta RAG-aumentada con OpenAI |
| POST | `/api/v1/analyses` | Prompt directo a OpenAI sin RAG |
| POST | `/api/v1/accounting/code-assignments/{document_id}` | Asigna cuentas PUC a cada línea de detalle usando OpenAI |
| GET  | `/api/v1/accounting/system-prompts` | Lista prompts del sistema |
| POST | `/api/v1/accounting/system-prompts` | Crea nuevo prompt del sistema |
| PATCH| `/api/v1/accounting/system-prompts/{id}` | Activa un prompt (`{"is_active": true}`) |
| GET  | `/health` | Health check |

### session-proxy (:8004) — interno
| Método | Path | Descripción |
|--------|------|-------------|
| POST | `/api/v1/dian/sessions` | Autentica con token en portal DIAN, crea sesión local |
| DELETE | `/api/v1/dian/sessions/{session_id}` | Elimina sesión local |
| POST | `/api/v1/dian/sessions/company` | Login vía browser (Playwright), crea sesión |
| POST | `/api/v1/dian/sessions/debug` | [DEBUG] Intento de login sin crear sesión |
| POST | `/api/v1/dian/downloads` | Consulta DIAN y encola descargas de ZIPs |
| GET  | `/api/v1/dian/documents/batches/{batch_id}` | Estado del lote de descarga |
| GET  | `/api/v1/dian/documents/jobs/{job_id}` | Estado de un job individual |
| POST | `/api/v1/proxy/request` | Reenvía request HTTP al portal externo |
| GET  | `/health` | Health check |

### odoo-service (:8005) — interno
| Método | Path | Descripción |
|--------|------|-------------|
| POST | `/api/v1/odoo/syncs` | Sincroniza facturas de compra desde Odoo |
| GET  | `/api/v1/odoo/entries` | Lista asientos locales (filtros: fecha, tipo, estado) |
| POST | `/api/v1/odoo/entry-matches` | Vincula asientos Odoo con documentos DIAN |
| GET  | `/api/v1/odoo/entries/document/{document_id}` | Último asiento vinculado a documento |
| GET  | `/api/v1/odoo/entries/{entry_id}` | Detalle de asiento con líneas |
| GET  | `/health` | Health check |

### siigo-service (:8006) — interno
| Método | Path | Descripción |
|--------|------|-------------|
| POST | `/api/v1/siigo/sessions` | Autentica en SIIGO, persiste token |
| POST | `/api/v1/siigo/chart-accounts/syncs` | Sincroniza plan de cuentas desde SIIGO |
| POST | `/api/v1/siigo/purchase-invoice-parameters` | Guarda plantilla para facturas de compra |
| GET  | `/api/v1/siigo/purchase-invoice-parameters` | Lista plantillas locales (`account_key` opcional; sin él, todas) |
| POST | `/api/v1/siigo/purchase-invoices` | **RF-05**: crea la factura de compra en SIIGO (`POST /v1/purchases`) |
| POST | `/internal/siigo/purchase-invoices` | **RF-05**: la misma creación para los workers de la cola (`X-Internal-Secret` + `X-Tenant-Slug`), que no tienen token de usuario |
| GET  | `/internal/siigo/purchase-invoice-parameters` | **RF-05**: plantilla de parámetros para los workers |
| POST | `/api/v1/siigo/journal-entries` | Comprobante contable |
| GET  | `/health` | Health check |

### auth-service (:8008) — interno
| Método | Path | Descripción |
|--------|------|-------------|
| POST | `/api/v1/auth/login` | Autentica y emite access + refresh (JWT RS256). El tenant sale del header `X-Tenant-Slug`, del body o del dominio del email. **Limitado**: varios fallos seguidos sobre el mismo correo o IP devuelven 429 durante unos minutos |
| POST | `/api/v1/auth/refresh` | Renueva el access token (rotación; el consumo del `jti` es atómico) |
| GET  | `/api/v1/tenants` | Devuelve **el tenant del token**, no la lista de clientes. Requiere token |
| POST | `/api/v1/tenants` | Crea un cliente y aprovisiona su base. Requiere `X-Internal-Secret` (bootstrap): no puede pedir token porque el primer administrador nace de esta llamada |
| GET  | `/api/v1/users` | Lista los usuarios **del tenant del token**. Requiere rol `tenant_admin` |
| POST | `/api/v1/users/invite` | Invita un usuario **al tenant del token**. Requiere rol `tenant_admin` |
| GET  | `/health` | Health check |

> **El tenant sale del token, nunca del header.** En los endpoints de usuarios y tenants,
> `X-Tenant-Slug` ya no decide sobre qué base se opera: lo hace el claim `tenant_slug`. Nginx
> sigue inyectando el header desde el subdominio, pero un cliente que lo falsifique no puede
> alcanzar los datos de otra empresa.

### integration-config-service (:8007) — interno
| Método | Path | Descripción |
|--------|------|-------------|
| GET·PUT | `/api/v1/integrations/retention-criteria` | **RF-08**: criterios del contador sobre cómo determinar retenciones (por empresa, editables sin desplegar) |
| PUT  | `/api/v1/integrations/credentials` | Crear/actualizar credenciales de integración |
| GET  | `/api/v1/integrations/credentials` | Lista credenciales (sin secretos) |
| POST | `/api/v1/integrations/cost-centers/imports` | Importa centros de costo desde .xlsx |
| GET  | `/api/v1/integrations/chart-accounts` | Lista plan de cuentas local (agnóstico al proveedor) |
| POST | `/api/v1/integrations/chart-accounts/imports` | Importa plan de cuentas desde .xlsx |
| POST | `/api/v1/integrations/purchase-invoice-parameters` | Guarda plantilla proveedor-agnóstica |
| GET  | `/api/v1/integrations/purchase-invoice-parameters` | Lista plantillas (filtros: provider, account_key) |
| GET  | `/health` | Health check |

## Infraestructura

### PostgreSQL + pgvector
- Imagen: `pgvector/pgvector:pg16`
- La extensión `vector` se habilita automáticamente vía `scripts/init-db.sql`
- Driver: `psycopg2-binary`
- Tabla de vectores: `document_chunks` con columna `embedding Vector(768)`
- Búsqueda: operador coseno `<=>` de pgvector

### Ollama (embeddings locales)
- Imagen: `ollama/ollama` — contenedor `abacus_ollama`
- Modelo: `nomic-embed-text` (768 dimensiones, multilingual)
- El servicio `ollama-init` descarga el modelo automáticamente al primer `up`
- URL interna: `http://ollama:11434`

### Comunicación entre servicios
- **Protocolo**: HTTP síncrono con `httpx`
- `xml-processor` → `rag-service`: indexación best-effort (fallo no bloquea el XML)
- `xml-processor` → `llm-service`: trigger asignación PUC best-effort (fallo no bloquea el XML)
- `xml-processor` → `integration-config-service`: obtiene catálogo de impuestos al procesar (best-effort)
- `llm-service` → `integration-config-service`: obtiene PUC para construir el prompt (best-effort)
- `llm-service` → `xml-processor`: lee documento y escribe codes en details
- `llm-service` → `rag-service`: búsqueda semántica (fallo propaga excepción)

## Testing

Cada servicio tiene su propio directorio `tests/`.
- **xml-processor**: usa SQLite in-memory (conftest.py). Cubre xml_parser, zip_handler, dian_dv, smart_match, nit, repositorio de documentos.
- **rag-service**: mocks de repositorio y embedding service. No requiere PostgreSQL ni Ollama.
- **llm-service**: mocks de AIService y RagClient. No requiere OpenAI ni rag-service.

```bash
# Correr tests de rag-service
docker run --rm -v "$(pwd)/services/rag-service:/app" --workdir //app python:3.9-slim \
  bash -c "pip install -r requirements.txt -q && python -m pytest tests/ -v"

# Correr tests de llm-service
docker run --rm -v "$(pwd)/services/llm-service:/app" --workdir //app python:3.9-slim \
  bash -c "pip install -r requirements.txt -q && python -m pytest tests/ -v"
```

## Variables de entorno (.env)

```
# PostgreSQL
# Cada tenant tiene su propia base de datos: abacus_t_{slug}
# La base por defecto de desarrollo es: abacus
DATABASE_HOST=database
DATABASE_PORT=5432
DATABASE_USER=master
DATABASE_PASSWORD=master
DATABASE_NAME=abacus

# OpenAI (llm-service)
OPENAI_API_KEY=sk-...
# Tope de espera de una llamada al modelo. El SDK trae 600 s, que en la sugerencia de
# retenciones significa dejar la interfaz girando diez minutos y arrastrar el lote entero.
OPENAI_TIMEOUT_SECONDS=60
OPENAI_MAX_RETRIES=2

# Ollama (rag-service) — SOLO si no hay OPENAI_API_KEY.
# ATENCIÓN: `document_chunks.embedding` tiene dimensión fija (1536, text-embedding-3-small).
# Ollama produce 768, así que ese camino exige migrar la columna. Sin migrarla, la indexación
# falla con un mensaje explícito en vez de perderse en un warning (RF-08).
OLLAMA_HOST=http://ollama:11434
OLLAMA_EMBED_MODEL=nomic-embed-text

# URLs inter-servicio (sobreescritas en docker-compose)
RAG_SERVICE_URL=http://rag-service:8002
LLM_SERVICE_URL=http://llm-service:8003
XML_PROCESSOR_URL=http://xml-processor:8001

# Odoo (odoo-service)
ODOO_URL=http://localhost:8069
ODOO_DB=odoo
ODOO_USER=admin
ODOO_PASSWORD=admin

# SIIGO (siigo-service)
SIIGO_BASE_URL=https://api.siigo.com
SIIGO_CHART_ACCOUNTS_PATH=/v1/accounts

# session-proxy
EXTERNAL_BASE_URL=https://catalogo-vpfe.dian.gov.co
EXTERNAL_LOGIN_PATH=/User/AuthToken
SESSION_TTL_SECONDS=3600

# Redis (session-proxy + xml-processor batch)
REDIS_URL=redis://redis:6379

# integration-config-service (usado por xml-processor y llm-service)
INTEGRATION_CONFIG_URL=http://integration-config-service:8007

# RF-05 · cola de contabilización (xml-processor). Ninguno es obligatorio: los valores por
# defecto son conservadores y equivalen al comportamiento secuencial.
#
# `ACCOUNTING_RATE_LIMIT_PER_MINUTE` debe bajarse a 10 contra empresas de PRUEBA de SIIGO:
# su límite documentado es 10 req/min en pruebas y 100 en producción.
ACCOUNTING_MAX_CONCURRENCY=1
ACCOUNTING_RATE_LIMIT_PER_MINUTE=100
ACCOUNTING_MAX_ATTEMPTS=5
ACCOUNTING_BACKOFF_BASE_SECONDS=2
ACCOUNTING_BACKOFF_MAX_SECONDS=300
ACCOUNTING_SIIGO_TIMEOUT_SECONDS=120
ACCOUNTING_POLL_INTERVAL_SECONDS=5
ACCOUNTING_RECONCILE_DELAY_SECONDS=10
ACCOUNTING_BATCH_MAX_SIZE=200
ACCOUNTING_STALE_JOB_SECONDS=900
```

## Reglas de desarrollo

### Estándares REST API (obligatorio en todos los endpoints)

Todo endpoint nuevo o modificado **debe** cumplir con los estándares REST:

- **Rutas orientadas a recursos**: usar sustantivos, nunca verbos. `POST /documents` ✅ — `POST /uploadDocument` ❌
- **Plurales para colecciones**: `/documents`, `/entries`, `/sessions` — no `/document`, `/entry`, `/session`
- **Métodos HTTP semánticos**: `POST` crea, `GET` lee, `PUT`/`PATCH` actualiza, `DELETE` elimina
- **Sub-recursos para relaciones**: `/documents/{id}/full`, `/entries/{id}/recalculations`
- **Filtros en query params**: `GET /entries?document_id=1&from_date=2024-01-01` — nunca en el path (`/entries/by-document/{id}` ❌)
- **Sin verbos de acción en paths**: evitar `/sync`, `/auth`, `/logout`, `/enqueue`, `/import-excel` como segmentos finales; reemplazar con el sustantivo del recurso creado (`/syncs`, `/sessions`, `/downloads`, `/imports`)
- **Transiciones de estado vía PATCH con body**: `PATCH /documents/{id}` con `{"status": 300}` — no `PATCH /documents/{id}/approve-action` ❌
- **Códigos de estado correctos**: `201 Created` al crear recursos, `200 OK` al leer/actualizar, `204 No Content` al eliminar, `404` recurso no existe, `409` conflicto de estado, `422` datos inválidos
- **POST con body para búsqueda**: aceptable cuando los parámetros de búsqueda son complejos o exceden límites de URL (RFC 9110 §9.3.1) — documentar la razón en la descripción del endpoint
- **Excepción documentada**: `PATCH /{id}/approve` es idiomático para transiciones de estado (RFC 5023) y se mantiene; solo evitar la negación (`/unapprove` ❌)

### Documentación Swagger (obligatoria en todos los endpoints)

Cada endpoint FastAPI **debe** incluir:

```python
@router.post(
    "/ruta",
    response_model=MiResponse,
    status_code=201,
    summary="Título corto visible en la lista de endpoints",
    description=(
        "Descripción larga en markdown. Explicar:\n"
        "- Qué hace el endpoint.\n"
        "- Flujo interno si es relevante.\n"
        "- Reglas de negocio importantes.\n"
        "- Cuándo usar este endpoint vs otros similares."
    ),
    response_description="Qué retorna en caso exitoso.",
    responses={
        404: {"description": "Cuándo ocurre este error."},
        409: {"description": "Cuándo ocurre este error."},
    },
)
```

Cada DTO Pydantic **debe** documentar sus campos con `Field(description=..., examples=[...])`:

```python
class MiRequest(BaseModel):
    campo: str = Field(..., description="Para qué sirve este campo.", examples=["valor-ejemplo"])
    model_config = {
        "json_schema_extra": {"example": {"campo": "valor-ejemplo"}}
    }
```

**Acceso a la documentación:**
- **Centralizada (gateway):** `http://localhost:8000/docs` — selector de servicio en la parte superior
- Por servicio (desarrollo): `http://localhost:800{1,2,3}/docs`

Los specs OpenAPI de cada servicio también se exponen en el gateway:
- `http://localhost:8000/openapi/xml-processor.json`
- `http://localhost:8000/openapi/llm-service.json`
- `http://localhost:8000/openapi/rag-service.json`
- `http://localhost:8000/openapi/session-proxy.json`
- `http://localhost:8000/openapi/odoo-service.json`
- `http://localhost:8000/openapi/siigo-service.json`
- `http://localhost:8000/openapi/integration-config-service.json`

---

## Decisiones de diseño

- **Los roles se comprueban en cada escritura, en todos los servicios**: `tenant_admin` y
  `operator` escriben; `viewer` solo lee. La dependencia `require_write`
  (`infrastructure/config/auth_dependency.py`) es el punto único por el que pasan las
  escrituras y está declarada en cada endpoint que muta algo: documentos, catálogos,
  credenciales de integración, perfil fiscal, sesiones DIAN y creación de comprobantes en
  SIIGO. Las lecturas quedan abiertas a cualquier usuario autenticado del cliente, que es lo
  que `viewer` significa. Un token sin ningún rol se deniega: todos los usuarios reciben uno
  al crearse, así que su ausencia indica un token manipulado, no un caso legítimo.
  El frontend refleja la misma regla con `authStore.canWrite` para no ofrecer botones que
  acabarían en 403, pero **quien decide es el servidor**.

- **Cinco estados funcionales, y solo cinco (RF-05)**: `PROCESADO → CAUSADO → APROBADO →
  CONTABILIZADA`, o `ERROR`. Cualquier fallo de contabilización deja el documento en `ERROR`,
  con el motivo en `accounting_error`. Lo que distingue un fallo de otro **no es el estado**
  sino una clasificación interna (`domain/services/siigo_error_classifier.py`), que se reduce
  a dos booleanos antes de salir por la API: `accounting_can_edit` y `accounting_can_retry`.
  El contador nunca ve clases de error; ve el estado, el mensaje y dos botones. Añadir un
  error nuevo es **una fila** en la tabla de reglas, sin tocar la API ni el frontend.
- **El cerrojo de contabilización es una columna, no un estado (RF-05)**: `accounting_locked`.
  La API de SIIGO **no admite `Idempotency-Key` en `/v1/purchases`** —su documentación lo
  habilita solo en `/v1/invoices`, `/v1/credit-notes`, `/v1/journals` y `/v1/vouchers`—, así
  que un envío cuyo desenlace se desconoce (timeout, 5xx, 2xx sin id) **no puede reenviarse**:
  la factura pudo quedar creada y el reenvío duplicaría un asiento contable real. Ese
  documento se ve en `ERROR` como cualquier otro, pero con el cerrojo puesto, y solo lo abre
  la reconciliación con verificación humana (`GET /documents/{id}/siigo-invoices`). Nunca se
  abre por tiempo ni automáticamente.
- **Ante la duda, incierto**: clasificar de menos cuesta una verificación manual; clasificar
  de más cuesta un asiento duplicado en la contabilidad real de un cliente. Los `5xx` y el
  `408` de SIIGO se tratan como inciertos precisamente por eso: no dicen en qué momento falló.
- **La contabilización por lotes se encola, no se ejecuta en la petición**: el backend
  responde `202` con un `batch_id` y los workers envían en segundo plano
  (`infrastructure/queue/accounting_worker.py`). La cola está **en base de datos**
  (`accounting_jobs`) y no en memoria: un reinicio no puede perder el rastro de un documento
  que ya se envió a SIIGO. Tres barreras independientes impiden el doble envío: el cerrojo
  del documento, un `SELECT ... FOR UPDATE` al tomarlo, y un índice único parcial sobre los
  trabajos activos de cada documento.
- **Concurrencia configurable, por defecto 1**: SIIGO documenta un límite de peticiones por
  minuto (100 producción / 10 pruebas) pero **no** documenta ningún límite de concurrencia.
  Subir `ACCOUNTING_MAX_CONCURRENCY` debe apoyarse en pruebas contra el ambiente real, no en
  una suposición. Ningún valor operativo está incrustado en el código.
- **Auditoría append-only (RF-05)**: `accounting_attempts` guarda cada intento con su
  request, su response, el código HTTP y la clasificación; `document_field_changes`, cada
  corrección manual con quién la hizo y desde qué valor. Ninguna de las dos se modifica jamás.
- **Hexagonal por servicio**: cada microservicio tiene su propio dominio, puertos y adaptadores. No se comparte código entre servicios.
- **Municipios de ReteICA, una sola fuente**: los municipios donde la empresa retiene ICA son los de `retention_ica_rates`, la única tabla que además lleva la tarifa. El perfil fiscal (`tenant_fiscal_profile`) solo dice **si** la empresa es agente de ICA, no **dónde**. Tenían las dos listas y podían discrepar: un municipio del perfil sin tarifa no habilita la retención (el sistema se niega a estimar el porcentaje), y uno con tarifa fuera del perfil quedaba invisible para el modelo.
- **El RAG solo aprende de lo contabilizado (RF-08)**: una causación se convierte en conocimiento reutilizable únicamente cuando SIIGO la acepta y el documento queda en `Contabilizada`. Aprobar no basta: entre aprobar y contabilizar todavía puede fallar el tercero, el comprobante o la cuenta, y esos casos son justo los que no deben servir de ejemplo. Lo que se indexa es la causación **final enviada a SIIGO**, no la primera sugerencia de la IA.
- **Best-effort en indexación**: si el rag-service no está disponible al contabilizar, el xml-processor loguea un warning y continúa. La factura ya existe en SIIGO y nada puede deshacerla; el conocimiento perdido se repone con `POST /internal/documents/reindex`.
- **LLM asigna cuentas PUC por ítem, no asientos completos**: el rol del LLM es asignar un código del Plan Único de Cuentas (PUC) a cada línea de detalle de la factura. Los asientos contables completos quedan a cargo del software de destino (SIIGO/Odoo). El historial de asignaciones vive en `document_details.code`.
- **Best-effort en asignación de cuentas**: si el llm-service no está disponible tras procesar un XML, se loguea warning y el documento queda guardado sin codes. Se puede reasignar manualmente con `POST /api/v1/accounting/code-assignments/{id}`.
- **Enriquecimiento automático de líneas**: al procesar cada XML, xml-processor asigna automáticamente `tax_id` (lookup en integration-config-service), `cost_center_id` (historial de la misma empresa) y `payment_type_id` (del emisor) antes de guardar el documento.
- **Dominio independiente**: las entidades de dominio no se comparten entre servicios. Cada uno define sus propios contratos.
- **Multi-tenant**: cada tenant tiene su propia base de datos PostgreSQL (`abacus_t_{slug}`). El endpoint `POST /internal/provision-tenant` crea/migra las tablas de un servicio en la base del tenant. Orden recomendado: provisionar `integration-config-service` antes que `xml-processor` (FK cruzadas).
- **Ningún fallo de RF-08 puede ser silencioso**: la indexación del conocimiento y la
  recuperación de precedentes son best-effort —no pueden tumbar una contabilización que SIIGO
  ya aceptó—, y esa misma tolerancia es lo que las hace peligrosas: un error ahí no rompe
  nada visible, solo deja el RAG vacío. Por eso la dimensión del embedding se comprueba antes
  de insertar (y falla nombrando las dos dimensiones), y el fallo de recuperación se registra
  con traza completa. Un `except` de una línea tapó durante meses que la búsqueda híbrida
  nunca se ejecutaba.
- **pgvector nativo**: la búsqueda vectorial usa el operador `<=>` directamente en SQL para máximo rendimiento.
- **Ollama en contenedor separado**: permite cambiar el modelo de embeddings sin tocar el código de rag-service (solo variable `OLLAMA_EMBED_MODEL`).
