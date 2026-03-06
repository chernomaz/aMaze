"""Read-only proxy to the Registry service."""

import os

import httpx
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/registry", tags=["registry"])

REGISTRY_URL = os.environ.get("REGISTRY_URL", "http://registry:8002")


@router.get("/capabilities")
async def list_capabilities(
    capability_type: str | None = Query(None),
    tag: str | None = Query(None),
    is_healthy: bool | None = Query(None),
    name: str | None = Query(None),
):
    params = {k: v for k, v in {
        "capability_type": capability_type,
        "tag": tag,
        "is_healthy": is_healthy,
        "name": name,
    }.items() if v is not None}

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(f"{REGISTRY_URL}/capabilities", params=params)
            resp.raise_for_status()
            return resp.json()
        except httpx.RequestError as e:
            raise HTTPException(status_code=503, detail=f"Registry unavailable: {e}")


@router.get("/capabilities/{name}")
async def get_capability(name: str):
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(f"{REGISTRY_URL}/capabilities/{name}")
            if resp.status_code == 404:
                raise HTTPException(status_code=404, detail="Capability not found")
            resp.raise_for_status()
            return resp.json()
        except httpx.RequestError as e:
            raise HTTPException(status_code=503, detail=f"Registry unavailable: {e}")
