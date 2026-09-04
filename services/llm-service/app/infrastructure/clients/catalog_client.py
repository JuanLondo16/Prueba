import logging

from app.infrastructure.clients.catalog_cache import catalog_cache
from app.infrastructure.clients.http_pool import get_client

logger = logging.getLogger(__name__)


class CatalogClient:
    """Cliente HTTP para obtener datos de catálogo desde xml-processor."""

    def __init__(self, base_url: str, bearer_token: str = "", tenant_slug: str = ""):
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {bearer_token}"} if bearer_token else {}
        self._tenant_slug = tenant_slug

    async def _get(self, path: str) -> list[dict]:
        client = await get_client()
        response = await client.get(
            f"{self._base_url}{path}", headers=self._headers, timeout=10.0
        )
        response.raise_for_status()
        return response.json()

    async def get_cost_centers(self) -> list[dict]:
        return await self._get("/api/v1/catalog/cost-centers")

    async def get_puc_accounts(self) -> list[dict]:
        return await self._get("/api/v1/catalog/puc-accounts")

    async def get_retention_fuente_rates(self) -> list[dict]:
        """Tarifas oficiales de ReteFuente por concepto y tipo de contribuyente.

        Cacheado por empresa (`catalog_cache`, TTL 120s): es la misma tabla oficial para
        todos los documentos del mismo tenant y RF-08 la pedía entera, sin caché, en cada
        sugerencia — un round trip completo repetido para un dato que casi nunca cambia
        entre una sugerencia y la siguiente.
        """

        async def _cargar() -> list[dict]:
            return await self._get("/api/v1/catalog/retention-fuente-rates")

        return await catalog_cache.get_or_load(
            self._tenant_slug, "retention_fuente_rates", _cargar
        )

    async def get_retention_ica_rates(self) -> list[dict]:
        """Tarifas de ReteICA de `retention_ica_rates` (xml-processor).

        Desde la migración del 2026-08-31, `SuggestRetentionsUseCase` (RF-08) ya NO llama a
        este método: las tarifas de ReteICA se leen de `integration_retentions`
        (`IntegrationConfigClient.get_retentions()`), que fusionó esta misma información en la
        tabla del catálogo de retenciones — cada candidata trae ya su municipio, concepto y
        base mínima, así que cruzarla con esta tabla aparte dejó de hacer falta. Se conserva
        el método por si algún otro consumidor todavía necesita leer la tabla legado.
        """
        return await self._get("/api/v1/catalog/retention-ica-rates")
