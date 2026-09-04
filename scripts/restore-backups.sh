#!/bin/sh
# Restaura, al arrancar el stack, cualquier backup que alguien haya dejado en ./backups/ —
# sin que nadie tenga que correr `docker cp`/`psql -f`/`pg_restore` a mano.
#
# Convencion:
#   backups/meta.sql        (opcional) INSERTs de abacus_meta.tenants (pg_dump --data-only)
#   backups/tenants/<slug>.dump   (0 o mas) dump -Fc de la base abacus_t_<slug>
#
# Idempotente y no destructivo: si abacus_t_<slug> ya existe (porque ya se restauro antes,
# o porque alguien ya genero datos nuevos trabajando), esa base se deja intacta — nunca se
# pisa. Si backups/ no tiene ningun .dump, no hace nada: un `docker compose up` sin esa
# carpeta (cualquier ambiente real) es una operacion vacia.
set -eu

HOST="${DATABASE_HOST:-abacus_db}"
PORT="${DATABASE_PORT:-5432}"
DB_USER="${DATABASE_USER:-master}"
export PGPASSWORD="${DATABASE_PASSWORD:-master}"

BACKUPS_DIR=/backups
META_FILE="$BACKUPS_DIR/meta.sql"
TENANTS_DIR="$BACKUPS_DIR/tenants"

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

if [ ! -d "$TENANTS_DIR" ] || [ -z "$(find "$TENANTS_DIR" -maxdepth 1 -name '*.dump' -print -quit 2>/dev/null)" ]; then
  log "No hay dumps en $TENANTS_DIR — nada que restaurar."
  exit 0
fi

restored_any=0
for dump in "$TENANTS_DIR"/*.dump; do
  slug=$(basename "$dump" .dump)
  db="abacus_t_${slug}"
  exists=$(psql -h "$HOST" -p "$PORT" -U "$DB_USER" -d postgres -tAc \
    "select 1 from pg_database where datname = '${db}'")
  if [ "$exists" = "1" ]; then
    log "$db ya existe — se deja intacta (no se pisan datos nuevos)."
    continue
  fi
  log "Restaurando $db desde $(basename "$dump")..."
  pg_restore -h "$HOST" -p "$PORT" -U "$DB_USER" -d postgres --create --clean --if-exists "$dump"
  restored_any=1
done

if [ "$restored_any" = "1" ] && [ -f "$META_FILE" ]; then
  log "Registrando tenant(s) restaurado(s) en abacus_meta..."
  # ON_ERROR_STOP=0: si alguna fila ya existiera (restauracion parcial previa), se sigue de
  # largo en vez de abortar — el mismo espiritu idempotente que demo-tenant-init.
  psql -h "$HOST" -p "$PORT" -U "$DB_USER" -d abacus_meta -v ON_ERROR_STOP=0 -f "$META_FILE"
fi

log "Listo."
