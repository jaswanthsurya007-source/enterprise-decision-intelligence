"""Explainability client -- write :class:`Decision` records to the governance service.

Unlike audit/lineage (which fan out over the bus), explainability is written
*synchronously over HTTP* to the governance service so the caller knows the
immutable evidence snapshot landed before it returns a narrative to a user. The
SDK owns no database: it POSTs a :class:`~edis_contracts.governance.Decision` to
``POST /v1/explain/decisions`` and lets the governance service persist it.

The client is importable without a live service and creates no connection at
import time: an injected :class:`httpx.AsyncClient` is reused as-is, otherwise one
is built lazily on first use (and owned/closed by this client).
"""

from __future__ import annotations

import httpx

from edis_contracts.governance import Decision

#: Path on the governance service that accepts explainability decisions.
DECISIONS_PATH = "/v1/explain/decisions"


class ExplainabilityClient:
    """POSTs :class:`Decision` records to the governance explainability endpoint.

    Parameters
    ----------
    base_url:
        Root URL of the governance service, e.g. ``http://governance:8000``.
    client:
        Optional pre-built :class:`httpx.AsyncClient`. When supplied it is reused
        as-is and *not* closed by this object (the caller owns its lifecycle).
        When omitted, a client is created lazily on first request and closed by
        :meth:`aclose` / context-manager exit.
    timeout:
        Per-request timeout (seconds) used only for the lazily-created client.
    """

    def __init__(
        self,
        base_url: str,
        client: httpx.AsyncClient | None = None,
        *,
        timeout: float = 10.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client
        self._owns_client = client is None
        self._timeout = timeout

    def _get_client(self) -> httpx.AsyncClient:
        """Return the http client, creating an owned one lazily on first use."""

        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def write_decision(self, decision: Decision) -> None:
        """POST ``decision`` to the governance service; raise on a non-2xx status.

        The body is the contract JSON (``Decision.model_dump_json``), so the
        immutable evidence snapshot is transmitted verbatim. Raises
        :class:`httpx.HTTPStatusError` if the service rejects the write.
        """

        client = self._get_client()
        response = await client.post(
            f"{self._base_url}{DECISIONS_PATH}",
            content=decision.model_dump_json(),
            headers={"content-type": "application/json"},
        )
        response.raise_for_status()

    async def aclose(self) -> None:
        """Close the http client if (and only if) this object created it."""

        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "ExplainabilityClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()
