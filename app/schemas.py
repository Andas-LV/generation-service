from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator


class GenerateRequest(BaseModel):
    user_id: int
    model_slug: str
    prompt: str = Field(min_length=1)
    ref_images: list[str] = Field(default_factory=list)


class GenerateResponse(BaseModel):
    task_id: str


class ProviderWebhook(BaseModel):
    external_id: str | None = None
    reference: str | None = None
    status: Literal["completed", "failed"]
    result_url: str | None = None
    error_type: Literal["technical", "moderation", "payload"] | None = None

    @model_validator(mode="after")
    def check_outcome(self) -> Self:
        if not self.external_id and not self.reference:
            raise ValueError("external_id or reference is required")
        if self.status == "completed" and not self.result_url:
            raise ValueError("result_url is required for completed")
        if self.status == "failed" and self.error_type is None:
            self.error_type = "technical"
        return self


class PaymentWebhook(BaseModel):
    payment_id: str = Field(min_length=1)
    user_id: int
    tokens: int = Field(gt=0)
    status: str
