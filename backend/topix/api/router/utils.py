"""Router for utils."""
import ipaddress
import logging
import socket
from typing import Annotated, Literal
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from topix.api.utils.decorators import with_standard_response
from topix.api.utils.rate_limit.entitlements import resolve_entitlement_context
from topix.api.utils.rate_limit.policy import resolve_allowed_model_tiers
from topix.api.utils.security import get_current_user_uid
from topix.config.services import service_config
from topix.utils.images.search import fetch_images, search_iconify_icons
from topix.utils.images.web import search_linkup, search_serper

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/utils",
    tags=["utils"],
    responses={404: {"description": "Not found"}},
)


@router.get("/ping", include_in_schema=False, status_code=204)
async def ping() -> Response:
    """Cheap liveness probe used by the client's connection-state detector.

    No DB, no auth, no logging — by design, this endpoint must remain
    fast enough to poll aggressively without skewing real metrics.
    """
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Link reachability check (SSRF-guarded)
# ---------------------------------------------------------------------------


class CheckLinkRequest(BaseModel):
    url: str = Field(..., description="Absolute http(s) URL to probe.")


class CheckLinkResponse(BaseModel):
    ok: bool
    status_code: int | None = None
    reason: str


_BLOCKED_HOSTS = {"localhost", "metadata.google.internal"}


def _is_safe_url(url: str) -> tuple[bool, str]:
    """Validate the URL scheme + resolve the host to block private/loopback IPs (SSRF guard)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False, "only http/https allowed"
    host = parsed.hostname
    if not host or host in _BLOCKED_HOSTS:
        return False, "blocked host"
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False, "dns resolution failed"
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False, "private/loopback address"
    return True, "ok"


@router.post("/check-link", include_in_schema=False)
@router.post("/check-link/")
@with_standard_response
async def check_link(
    body: CheckLinkRequest,
    user_id: Annotated[str, Depends(get_current_user_uid)],
) -> dict:
    """Probe a URL's reachability (HEAD, fallback GET) with an SSRF guard + short timeout.

    Used by the board's Sources overlay so the user can tell live links
    from dead ones without copy-pasting into another browser. Blocks
    private/loopback targets so a malicious node can't turn the server
    into an internal-network probe.
    """
    safe, reason = _is_safe_url(body.url)
    if not safe:
        return {"ok": False, "status_code": None, "reason": reason}
    try:
        # Do NOT follow redirects automatically: a public host that 302's
        # to an internal/loopback target would bypass _is_safe_url. We
        # probe only the validated URL and surface the redirect status
        # (the client can re-check the Location itself if it wants).
        async with httpx.AsyncClient(
            timeout=8.0,
            follow_redirects=False,
            headers={"User-Agent": "dim0-link-check/1.0"},
        ) as client:
            try:
                resp = await client.head(body.url)
                # Some servers reject HEAD with 405/400 — fall back to GET.
                if resp.status_code in (405, 400, 501):
                    resp = await client.get(body.url)
            except httpx.RequestError:
                resp = await client.get(body.url)
        ok = resp.status_code < 400
        return {
            "ok": ok,
            "status_code": resp.status_code,
            "reason": "ok" if ok else f"http {resp.status_code}",
        }
    except httpx.RequestError as e:
        return {"ok": False, "status_code": None, "reason": f"unreachable: {type(e).__name__}"}
    except Exception as e:  # noqa: BLE001 — defensive: never 500 on a link check
        logger.warning("check-link failed for %s: %s", body.url, e)
        return {"ok": False, "status_code": None, "reason": "check failed"}


@router.get("/icons/search/", include_in_schema=False)
@router.get("/icons/search")
@with_standard_response
async def search_icons(query: str, limit: int = 100):
    """Search for icons."""
    results = await search_iconify_icons(query, limit)
    return {
        "icons": [res.model_dump(exclude_none=True) for res in results]
    }


@router.get("/images/search/", include_in_schema=False)
@router.get("/images/search")
@with_standard_response
async def search_images(
    query: str,
    limit: int = 5,
    engine: Literal["unsplash", "serper", "linkup"] = "unsplash",
):
    """Search for images."""
    match engine:
        case "unsplash":
            results = await fetch_images(query, limit)
        case "serper":
            res = await search_serper(query, num_results=limit)
            results = [{"url": url} for url in res]
        case "linkup":
            res = await search_linkup(query, num_results=limit)
            results = [{"url": url} for url in res]
        case _:
            raise HTTPException(status_code=400, detail="Invalid image search engine.")

    return {
        "images": results
    }


@router.get("/services/", include_in_schema=False)
@router.get("/services")
@with_standard_response
async def get_services(
    request: Request,
    user_id: Annotated[str, Depends(get_current_user_uid)],
) -> dict:
    """Get available services.

    `llm` is the catalog of reachable models (keyed by canonical id) with the
    display metadata the frontend needs to render the picker; the id is what the
    client sends back as the chosen model. The list is filtered to the model
    tiers the requester's billing plan is entitled to.
    """
    entitlement = await resolve_entitlement_context(request, user_id)
    allowed_tiers = resolve_allowed_model_tiers(entitlement.plan)
    return {
        "llm": [
            {
                "id": m.id,
                "label": m.label,
                "family": m.family,
                "tier": m.tier,
                "provider": m.provider,
            }
            for m in service_config.llm
            if m.tier in allowed_tiers
        ],
        "search": [search.name for search in service_config.search],
        "navigate": [navigate.name for navigate in service_config.navigate],
        "code": [code.name for code in service_config.code],
        "image_generation": [img_gen.name for img_gen in service_config.image_generation],
    }
