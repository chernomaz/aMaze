"""
WebSocket endpoint for live session streaming.

Subscribes to Redis pub/sub channel session:{id}:events and forwards
each message as a JSON string to the connected WebSocket client.
"""

import asyncio
import logging
from uuid import UUID

import redis.asyncio as aioredis
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter(tags=["websocket"])
logger = logging.getLogger(__name__)

# Reuse the pool from deps — import lazily to avoid circular imports
import os

redis_url = os.environ.get("REDIS_URL", "redis://redis:6379")


@router.websocket("/sessions/{session_id}/stream")
async def session_stream(websocket: WebSocket, session_id: UUID):
    await websocket.accept()
    channel = f"session:{session_id}:events"

    client = aioredis.from_url(redis_url, decode_responses=True)
    pubsub = client.pubsub()
    await pubsub.subscribe(channel)

    try:
        # Send a connected confirmation
        await websocket.send_json({"event_type": "connected", "session_id": str(session_id)})

        async def reader():
            async for message in pubsub.listen():
                if message["type"] == "message":
                    await websocket.send_text(message["data"])

        reader_task = asyncio.create_task(reader())

        # Keep alive — wait for client disconnect
        try:
            while True:
                # receive_text raises WebSocketDisconnect on close
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            reader_task.cancel()

    except Exception as exc:
        logger.error("WebSocket error for session %s: %s", session_id, exc)
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()
        await client.aclose()
        try:
            await websocket.close()
        except Exception:
            pass
