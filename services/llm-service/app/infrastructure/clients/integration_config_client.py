import logging
from typing import Optional

from app.infrastructure.clients.catalog_cache import catalog_cache
from app.infrastructure.clients.http_pool import get_client

logger = logging.getLogger(__name__)


class IntegrationConfigClient:
    """Cliente HTTP para consultar catálogos del integration-config-service.

    `tenant_slug` viene del token ya validado y solo se usa como clave de la caché de
    catálogos: **no** se envía como cabecera ni sustituye a la autorización, que sigue siendo
    el token del usuario. Sin él, el cliente funciona igual pero sin caché.
    """

    def __init__(self, base_url: str, bearer_token: str = "", tenant_slug: str = ""):
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {bearer_token}"} if bearer_token else {}
        self._tenant_slug = tenant_slug

    async def _get_json(self, path: str, params: Optional[dict] = None, timeout: float = 5.0):
        client = await get_client()
        response = await client.get(
            f"{self._base_url}{path}",
            params=params or {},
            headers=self._headers,
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()

    async def get_chart_accounts(self, active_only: bool = True) -> list[dict]:
        """Retorna el plan de cuentas configurado.

        Cacheado por empresa: es un catálogo de configuración, idéntico para todos los
        documentos del mismo cliente, y se pedía entero una vez por documento causado.

        Llamada best-effort: retorna lista vacía si el servicio no está disponible. Ese vacío
        nunca se cachea (ver `catalog_cache`), para que una caída momentánea no se prolongue.
        """
        params = {"active": "true"} if active_only else {}

        async def _cargar() -> list[dict]:
            try:
                return await self._get_json("/api/v1/integrations/chart-accounts", params=params)
            except Exception as exc:
                logger.warning(
                    "No se pudo obtener plan de cuentas de integration-config-service: %s", exc
                )
                return []

        return await catalog_cache.get_or_load(
            self._tenant_slug, f"chart_accounts:{active_only}", _cargar
        )

    async def get_taxes(self) -> list[dict]:
        """RF-08: catálogo de impuestos y retenciones sincronizado con SIIGO.

        Es la fuente autorizada de los porcentajes: el modelo elige qué retención aplica,
        pero la tarifa se toma siempre de aquí. Llamada best-effort, como el resto.
        """

        async def _cargar() -> list[dict]:
            try:
                return await self._get_json("/api/v1/integrations/taxes")
            except Exception as exc:
                logger.warning("No se pudo obtener el catálogo de impuestos: %s", exc)
                return []

        return await catalog_cache.get_or_load(self._tenant_slug, "taxes", _cargar)

    async def get_retentions(self) -> list[dict]:
        """RF-08: catálogo de retenciones (ReteFuente, ReteICA, ReteIVA, Autorretención).

        Separado de `get_taxes()` desde la migración del 2026-08-31: antes las retenciones
        vivían mezcladas con los impuestos reales del documento en `integration_taxes`; ahora
        tienen su propia tabla física (`integration_retentions`), y cada fila `type='reteica'`
        trae además su municipio, concepto y base mínima en la misma fila — antes esa
        información solo existía en una tabla paralela del xml-processor
        (`retention_ica_rates`) que casi nunca coincidía en porcentaje con el catálogo plano,
        así que muchas tarifas reales no podían proponerse. La tarifa sigue siendo la fuente
        autorizada del porcentaje; el modelo solo elige cuál aplica.

        Se piden TODAS (activas e inactivas), igual que `get_taxes()`: quien filtra por activo
        es `retention_candidates()`, y las inactivas siguen haciendo falta para resolver el
        tipo de una retención ya registrada aunque su fila se haya desactivado después
        (`_excluding_registered_types`). Llamada best-effort, como el resto.
        """

        async def _cargar() -> list[dict]:
            try:
                return await self._get_json("/api/v1/integrations/retentions")
            except Exception as exc:
                logger.warning("No se pudo obtener el catálogo de retenciones: %s", exc)
                return []

        return await catalog_cache.get_or_load(self._tenant_slug, "retentions", _cargar)

    async def get_fiscal_profile(self) -> Optional[dict]:
        """Perfil fiscal del tenant (el COMPRADOR): define si la empresa es agente de retención.

        Es autoritativo sobre el `TaxLevelCode` del receptor en el XML. Best-effort: si no está
        disponible, se devuelve None y la decisión cae al dato del XML.

        Cacheado por empresa: es un dato de configuración de la empresa, no del documento, y
        se pedía en cada sugerencia sin caché igual que el plan de cuentas antes de este fix.
        Un `None` (falla o perfil inexistente) nunca se cachea, igual que el resto de este
        cliente: ver `catalog_cache`.
        """

        async def _cargar() -> Optional[dict]:
            try:
                return await self._get_json("/api/v1/integrations/fiscal-profile")
            except Exception as exc:
                logger.warning("No se pudo obtener el perfil fiscal del tenant: %s", exc)
                return None

        return await catalog_cache.get_or_load(self._tenant_slug, "fiscal_profile", _cargar)

    async def get_cost_centers(self) -> list[dict]:
        """Retorna los centros de costo configurados.

        Su nombre da contexto al modelo sobre el área que consume el gasto (p. ej.
        «Gastos de Personal» o «Desarrollo de plataformas»), que ayuda a desambiguar
        descripciones genéricas. Llamada best-effort: la asignación no depende de esto.

        Cacheado por empresa por la misma razón que el plan de cuentas.
        """

        async def _cargar() -> list[dict]:
            try:
                return await self._get_json("/api/v1/integrations/cost-centers")
            except Exception as exc:
                logger.warning(
                    "No se pudo obtener centros de costo de integration-config-service: %s", exc
                )
                return []

        return await catalog_cache.get_or_load(self._tenant_slug, "cost_centers", _cargar)

    async def get_retention_criteria(self) -> list[dict]:
        """RF-08: criterios del contador de ESTA empresa sobre cómo determinar retenciones.

        Son datos por tenant, no una configuración global: cada contador tiene su criterio y
        estos cambian con la norma o con su interpretación. Por eso se consultan al servicio
        en cada sugerencia en vez de estar escritos en el código de este servicio, donde
        cambiar uno exigiría un despliegue y se aplicaría a todos los clientes por igual.

        Se piden TODOS y entran todos al prompt: no es una recuperación por relevancia. Son
        reglas que gobiernan cada factura, y hacerlas depender de una búsqueda semántica
        significaría que algún día no llegan y el modelo decide sin ellas sin que se note.

        Best-effort, como el resto del cliente: sin criterios la sugerencia se apoya en las
        tablas oficiales y el perfil fiscal, que son las fuentes vinculantes.

        Cacheado por empresa (TTL 120s, igual que el resto de este cliente): se seguían
        pidiendo TODOS en cada sugerencia sin caché — el TTL no cambia que se pidan completos
        ni que entren todos al prompt, solo evita repetir la llamada de red cuando nada
        cambió desde la sugerencia anterior.
        """

        async def _cargar() -> list[dict]:
            try:
                payload = await self._get_json("/api/v1/integrations/retention-criteria")
                return payload.get("criterios", [])
            except Exception as exc:
                logger.warning(
                    "RF-08: no se pudieron obtener los criterios del contador: %s", exc
                )
                return []

        return await catalog_cache.get_or_load(
            self._tenant_slug, "retention_criteria", _cargar
        )
