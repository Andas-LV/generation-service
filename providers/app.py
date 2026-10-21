import asyncio
import json
import logging
import os
import random
import uuid
from contextlib import asynccontextmanager
from typing import Literal

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.signing import sign

NAME = os.environ.get("PROVIDER_NAME", "alpha")
SECRET = os.environ.get("WEBHOOK_SECRET", "dev-secret")

logging.basicConfig(level=logging.INFO, format=f"%(asctime)s %(levelname)s {NAME} %(message)s")
logger = logging.getLogger(NAME)


class Behavior(BaseModel):
    failure_rate: float = Field(0.0, ge=0, le=1)
    failure_type: Literal["technical", "moderation", "payload"] = "technical"
    sync_failure_rate: float = Field(0.0, ge=0, le=1)
    sync_failure: Literal["503", "timeout"] = "503"
    hang_seconds: float = Field(30.0, ge=0)
    duplicate_rate: float = Field(0.0, ge=0, le=1)
    drop_rate: float = Field(0.0, ge=0, le=1)
    delay_min: float = Field(2.0, ge=0)
    delay_max: float = Field(20.0, ge=0)


class Config(BaseModel):
    default: Behavior = Field(default_factory=Behavior)
    scenarios: dict[str, Behavior] = Field(default_factory=dict)


class TaskRequest(BaseModel):
    reference: str
    prompt: str
    ref_images: list[str] = Field(default_factory=list)
    callback_url: str


def initial_config() -> Config:
    raw = os.environ.get("PROVIDER_BEHAVIOR")
    if not raw:
        return Config()
    return Config(default=Behavior.model_validate_json(raw))


config = initial_config()
deliveries: set[asyncio.Task] = set()
client: httpx.AsyncClient | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global client
    client = httpx.AsyncClient(timeout=10)
    try:
        yield
    finally:
        for job in list(deliveries):
            job.cancel()
        await client.aclose()


app = FastAPI(title=f"Fake provider {NAME}", lifespan=lifespan)


def behavior_for(prompt: str) -> Behavior:
    return config.scenarios.get(prompt, config.default)


async def send(url: str, body: bytes) -> None:
    try:
        response = await client.post(
            url,
            content=body,
            headers={"Content-Type": "application/json", "X-Signature": sign(body, SECRET)},
        )
        logger.info("webhook %s -> %s", url, response.status_code)
    except httpx.HTTPError as exc:
        logger.warning("webhook %s failed: %s", url, exc)


async def deliver(request: TaskRequest, external_id: str, behavior: Behavior) -> None:
    delay_max = max(behavior.delay_min, behavior.delay_max)
    await asyncio.sleep(random.uniform(behavior.delay_min, delay_max))
    if random.random() < behavior.drop_rate:
        logger.info("dropping webhook for %s", external_id)
        return
    payload = {"external_id": external_id, "reference": request.reference}
    if random.random() < behavior.failure_rate:
        payload |= {"status": "failed", "error_type": behavior.failure_type}
    else:
        payload |= {"status": "completed", "result_url": f"https://files.{NAME}.example/{external_id}.png"}
    body = json.dumps(payload).encode()
    copies = 2 if random.random() < behavior.duplicate_rate else 1
    await asyncio.gather(*(send(request.callback_url, body) for _ in range(copies)))


@app.post("/tasks", status_code=202)
async def create_task(request: TaskRequest):
    behavior = behavior_for(request.prompt)
    if random.random() < behavior.sync_failure_rate:
        if behavior.sync_failure == "503":
            return JSONResponse(status_code=503, content={"error": "service unavailable"})
        await asyncio.sleep(behavior.hang_seconds)
    external_id = f"{NAME}-{uuid.uuid4().hex}"
    job = asyncio.create_task(deliver(request, external_id, behavior))
    deliveries.add(job)
    job.add_done_callback(deliveries.discard)
    return {"external_id": external_id}


@app.get("/config")
async def read_config() -> Config:
    return config


@app.put("/config")
async def replace_config(new: Config) -> Config:
    config.default = new.default
    config.scenarios = new.scenarios
    return config


@app.put("/scenarios/{key}")
async def put_scenario(key: str, behavior: Behavior) -> Behavior:
    config.scenarios[key] = behavior
    return behavior


@app.delete("/scenarios/{key}", status_code=204)
async def delete_scenario(key: str) -> None:
    config.scenarios.pop(key, None)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "provider": NAME}
