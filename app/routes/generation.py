from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from app.config import Settings
from app.deps import get_dispatcher, get_pool, settings
from app.schemas import GenerateRequest, GenerateResponse
from app.services.dispatcher import Dispatcher
from app.services.ledger import balance_with_tail
from app.services.tasks import create_task, get_task

router = APIRouter()


@router.post("/generate", status_code=202, response_model=GenerateResponse)
async def generate(
    request: GenerateRequest,
    pool: asyncpg.Pool = Depends(get_pool),
    dispatcher: Dispatcher = Depends(get_dispatcher),
    config: Settings = Depends(settings),
) -> GenerateResponse:
    task_id, attempt_id = await create_task(
        pool,
        config,
        user_id=request.user_id,
        model_slug=request.model_slug,
        prompt=request.prompt,
        ref_images=request.ref_images,
    )
    dispatcher.spawn(attempt_id)
    return GenerateResponse(task_id=str(task_id))


@router.get("/tasks/{task_id}")
async def read_task(task_id: UUID, pool: asyncpg.Pool = Depends(get_pool)) -> dict:
    task = await get_task(pool, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return task


@router.get("/users/{user_id}/balance")
async def read_balance(
    user_id: int,
    pool: asyncpg.Pool = Depends(get_pool),
    config: Settings = Depends(settings),
) -> dict:
    balance = await balance_with_tail(pool, user_id, config.ledger_tail)
    if balance is None:
        raise HTTPException(status_code=404, detail="user not found")
    return balance
