#!/bin/sh
# Restaura, al arrancar el stack, cualquier backup que alguien haya dejado en
# backups/tenants/ — un solo paso para el usuario: copiar el archivo ahi. Nada mas.
#
# No hace falta un segundo archivo, un nombre especifico NI una extension especifica: quien
# exporta un tenant con `pg_dump -Fc` no siempre lo llama ".dump" (herramientas como pgAdmin
# suelen ofrecer ".sql" como nombre por defecto aunque el contenido sea formato CUSTOM), asi
# que en vez de filtrar por extension se prueba cada archivo con `pg_restore -l`: si lo puede
# leer, es un dump valido sin importar como se llame. De ahi mismo sale el nombre de su base
# (";  dbname: abacus_t_<slug>"), y con esa misma lectura se arma la fila que le falta en
# abacus_meta.tenants — sin eso, el login responde "Tenant not found" aunque la base del
# tenant ya tenga todos los datos, porque son dos cosas independientes (ver login.py en
# auth-service).
#
# Idempotente y no destructivo: si abacus_t_<slug> ya existe (porque ya se restauro antes, o
# porque ya se genero trabajo nuevo), esa base se deja intacta. Si backups/tenants/ esta vacia
# o no existe, no hace nada: un `docker compose up` sin esa carpeta (cualquier ambiente real)
# es una operacion vacia.
set -eu

HOST="${DATABASE_HOST:-abacus_db}"
PORT="${DATABASE_PORT:-5432}"
DB_USER="${DATABASE_USER:-master}"
export PGPASSWORD="${DATABASE_PASSWORD:-master}"

TENANTS_DIR=/backups/tenants

log() { echo "[restore-backups] $*"; }

# abacus_meta la crea auth-service al arrancar (ver services/auth-service/app/main.py);
# aunque el propio Postgres ya este healthy, esa base puntual puede tardar un poco mas en
# aparecer, asi que se espera activamente en vez de asumir que ya esta.
log "Esperando a que abacus_meta exista..."
i=0
until psql -h "$HOST" -p "$PORT" -U "$DB_USER" -d abacus_meta -c 'select 1' >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -ge 60 ]; then
    log "abacus_meta no aparecio tras 2 minutos — abortando sin tocar nada."
    exit 1
  fi
  sleep 2
done

if [ ! -d "$TENANTS_DIR" ] || [ -z "$(find "$TENANTS_DIR" -maxdepth 1 -type f -print -quit 2>/dev/null)" ]; then
  log "No hay ningun archivo en $TENANTS_DIR — nada que restaurar."
  exit 0
fi

for dump in "$TENANTS_DIR"/*; do
  [ -f "$dump" ] || continue
  db=$(pg_restore -l "$dump" 2>/dev/null | sed -n 's/^; *[Dd]bname: *//p' | head -1)
  if [ -z "$db" ]; then
    log "$(basename "$dump"): no se pudo leer el nombre de la base del propio archivo — se omite."
    continue
  fi
  slug=$(echo "$db" | sed -n 's/^abacus_t_//p')
  if [ -z "$slug" ]; then
    log "$(basename "$dump"): la base '$db' no sigue el patron abacus_t_<slug> — se omite."
    continue
  fi

  exists=$(psql -h "$HOST" -p "$PORT" -U "$DB_USER" -d postgres -tAc \
    "select 1 from pg_database where datname = '${db}'")
  if [ "$exists" = "1" ]; then
    log "$db ya existe — se deja intacta (no se pisan datos nuevos)."
    continue
  fi

  log "Restaurando $db desde $(basename "$dump")..."
  pg_restore -h "$HOST" -p "$PORT" -U "$DB_USER" -d postgres --create --clean --if-exists "$dump"

  log "Registrando el tenant '$slug' en abacus_meta..."
  psql -h "$HOST" -p "$PORT" -U "$DB_USER" -d abacus_meta -v ON_ERROR_STOP=1 -c "
    INSERT INTO tenants (id, slug, display_name, email_domain, is_active, created_at)
    VALUES (gen_random_uuid(), '${slug}', '${slug}', NULL, true, now())
    ON CONFLICT (slug) DO NOTHING;
  "
done

log "Listo."
