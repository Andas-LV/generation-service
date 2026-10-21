import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError

from app.config import Settings
from app.deps import get_dispatcher, get_pool, settings, signed_body
from app.schemas import PaymentWebhook, ProviderWebhook
from app.services.dispatcher import Dispatcher
from app.services.payments import handle_payment
from app.services.tasks import handle_provider_webhook

router = APIRouter(prefix="/webhooks")


def _parse(model, body: bytes):
    try:
        return model.model_validate_json(body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors(include_url=False)) from exc


@router.post("/providers/{name}")
async def provider_webhook(
    name: str,
    body: bytes = Depends(signed_body),
    pool: asyncpg.Pool = Depends(get_pool),
    dispatcher: Dispatcher = Depends(get_dispatcher),
    config: Settings = Depends(settings),
) -> dict:
    if name not in config.provider_urls:
        raise HTTPException(status_code=404, detail="unknown provider")
    payload = _parse(ProviderWebhook, body)
    outcome, next_attempt = await handle_provider_webhook(pool, config, name, payload)
    if next_attempt is not None:
        dispatcher.spawn(next_attempt)
    return {"outcome": outcome}


@router.post("/payments")
async def payment_webhook(
    body: bytes = Depends(signed_body),
    pool: asyncpg.Pool = Depends(get_pool),
) -> dict:
    payment = _parse(PaymentWebhook, body)
    credited = await handle_payment(pool, payment)
    return {"credited": credited}
