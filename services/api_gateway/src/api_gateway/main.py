from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api_gateway.routers import agents, graphs, policies, registry, sessions, ws

app = FastAPI(
    title="aMaze API Gateway",
    version="0.1.0",
    description="REST + WebSocket facade for the aMaze UI",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(agents.router)
app.include_router(policies.router)
app.include_router(graphs.router)
app.include_router(sessions.router)
app.include_router(registry.router)
app.include_router(ws.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
