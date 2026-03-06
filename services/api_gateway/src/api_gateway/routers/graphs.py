from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from amaze_shared.models.graph import ExecutionGraph, ExecutionStep
from api_gateway.deps import DB

router = APIRouter(prefix="/graphs", tags=["graphs"])


# ─── Schemas ──────────────────────────────────────────────────────────────────


class StepCreate(BaseModel):
    step_id: int
    label: str = ""
    call_type: str          # llm_call | mcp_call | agent_call | terminal
    callee_id: str | None = None
    next_step_ids: list[int] = []
    max_loops: int = 1
    token_cap: int | None = None


class GraphCreate(BaseModel):
    name: str
    description: str = ""
    start_step_id: int
    on_violation: str = "block"
    steps: list[StepCreate]


class StepUpdate(BaseModel):
    label: str | None = None
    call_type: str | None = None
    callee_id: str | None = None
    next_step_ids: list[int] | None = None
    max_loops: int | None = None
    token_cap: int | None = None


class GraphUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    start_step_id: int | None = None
    on_violation: str | None = None
    steps: list[StepCreate] | None = None  # full replacement when provided


class StepResponse(BaseModel):
    id: UUID
    step_id: int
    label: str
    call_type: str
    callee_id: str | None
    next_step_ids: list[int]
    max_loops: int
    token_cap: int | None

    model_config = {"from_attributes": True}


class GraphResponse(BaseModel):
    id: UUID
    name: str
    description: str
    start_step_id: int
    on_violation: str
    steps: list[StepResponse]

    model_config = {"from_attributes": True}


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _validate_graph(body: GraphCreate | GraphUpdate) -> None:
    if not hasattr(body, "steps") or body.steps is None:
        return
    step_ids = {s.step_id for s in body.steps}
    for step in body.steps:
        for nxt in step.next_step_ids:
            if nxt not in step_ids:
                raise HTTPException(
                    status_code=422,
                    detail=f"Step {step.step_id} references unknown next_step_id {nxt}",
                )


# ─── Routes ───────────────────────────────────────────────────────────────────


@router.get("", response_model=list[GraphResponse])
async def list_graphs(db: DB):
    result = await db.execute(select(ExecutionGraph))
    return result.scalars().all()


@router.get("/{graph_id}", response_model=GraphResponse)
async def get_graph(graph_id: UUID, db: DB):
    graph = await db.get(ExecutionGraph, graph_id)
    if not graph:
        raise HTTPException(status_code=404, detail="Graph not found")
    return graph


@router.post("", response_model=GraphResponse, status_code=201)
async def create_graph(body: GraphCreate, db: DB):
    _validate_graph(body)
    graph = ExecutionGraph(
        name=body.name,
        description=body.description,
        start_step_id=body.start_step_id,
        on_violation=body.on_violation,
    )
    db.add(graph)
    await db.flush()

    for s in body.steps:
        db.add(ExecutionStep(
            graph_id=graph.id,
            step_id=s.step_id,
            label=s.label,
            call_type=s.call_type,
            callee_id=s.callee_id,
            next_step_ids=s.next_step_ids,
            max_loops=s.max_loops,
            token_cap=s.token_cap,
        ))

    await db.commit()
    await db.refresh(graph)
    return graph


@router.put("/{graph_id}", response_model=GraphResponse)
async def update_graph(graph_id: UUID, body: GraphUpdate, db: DB):
    _validate_graph(body)  # type: ignore[arg-type]
    graph = await db.get(ExecutionGraph, graph_id)
    if not graph:
        raise HTTPException(status_code=404, detail="Graph not found")

    for field, value in body.model_dump(exclude_none=True, exclude={"steps"}).items():
        setattr(graph, field, value)

    if body.steps is not None:
        for step in list(graph.steps):
            await db.delete(step)
        await db.flush()
        for s in body.steps:
            db.add(ExecutionStep(
                graph_id=graph.id,
                step_id=s.step_id,
                label=s.label,
                call_type=s.call_type,
                callee_id=s.callee_id,
                next_step_ids=s.next_step_ids,
                max_loops=s.max_loops,
                token_cap=s.token_cap,
            ))

    await db.commit()
    await db.refresh(graph)
    return graph


@router.delete("/{graph_id}", status_code=204)
async def delete_graph(graph_id: UUID, db: DB):
    graph = await db.get(ExecutionGraph, graph_id)
    if not graph:
        raise HTTPException(status_code=404, detail="Graph not found")
    await db.delete(graph)
    await db.commit()
