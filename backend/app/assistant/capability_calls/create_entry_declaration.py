"""Non-writing Provider declaration for the gateway-owned create_entry flow."""

from __future__ import annotations

from typing import Annotated

from langchain_core.tools import InjectedToolArg, tool
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.assistant.capabilities.contracts import CapabilityError
from app.assistant.capabilities.errors import CapabilityDomainError


class CapabilityGatewayRequired(CapabilityDomainError):
    """Raised when a Provider-facing declaration bypasses the gateway."""

    safe_code = "capability_gateway_required"

    def __init__(self) -> None:
        super().__init__(
            CapabilityError(
                error_type="unauthorized",
                safe_code=self.safe_code,
                safe_message="create_entry must be invoked through the capability gateway",
                retry_disposition="never",
            )
        )


_INVOCATION_SECRET = object()


class CapabilityGatewayInvocation:
    """Opaque marker created only by the resolved system-tool adapter."""

    __slots__ = ("_secret",)

    def __init__(self, secret: object) -> None:
        self._secret = secret

    @property
    def verified(self) -> bool:
        return self._secret is _INVOCATION_SECRET


def _gateway_invocation_for_capability_adapter() -> CapabilityGatewayInvocation:
    """Construct the non-schema invocation marker at the trusted adapter edge."""
    return CapabilityGatewayInvocation(_INVOCATION_SECRET)


class CreateEntryCapabilityInput(BaseModel):
    """Provider-visible arguments; internal invocation state is deliberately absent."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    summary: str | None = None
    content: str | None = None
    type_code: str | None = None
    tags: list[str] | None = None
    time_mode: str | None = None
    time_at: str | None = None
    time_from: str | None = None
    time_to: str | None = None

    @field_validator("title", "summary", "content", "type_code", "time_mode", "time_at", "time_from", "time_to", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: object) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @field_validator("tags", mode="before")
    @classmethod
    def _normalize_tags(cls, value: object) -> list[str] | None:
        if value is None:
            return None
        if not isinstance(value, list):
            raise ValueError("tags must be a list")
        normalized = [str(item).strip() for item in value]
        return [item for item in normalized if item]


class CreateEntryProposal(CreateEntryCapabilityInput):
    """Normalized, non-writing request handed back to the gateway ledger."""

    @classmethod
    def from_normalized(cls, **values: object) -> "CreateEntryProposal":
        return cls.model_validate(values)


@tool("create_entry", args_schema=CreateEntryCapabilityInput)
def create_entry_declaration(
    title: str | None = None,
    summary: str | None = None,
    content: str | None = None,
    type_code: str | None = None,
    tags: list[str] | None = None,
    time_mode: str | None = None,
    time_at: str | None = None,
    time_from: str | None = None,
    time_to: str | None = None,
    *,
    _gateway_invocation: Annotated[
        CapabilityGatewayInvocation | None, InjectedToolArg
    ] = None,
) -> CreateEntryProposal:
    """Return a normalized create proposal only for a verified gateway invocation."""
    if _gateway_invocation is None or not _gateway_invocation.verified:
        raise CapabilityGatewayRequired()
    return CreateEntryProposal.from_normalized(
        title=title,
        summary=summary,
        content=content,
        type_code=type_code,
        tags=tags,
        time_mode=time_mode,
        time_at=time_at,
        time_from=time_from,
        time_to=time_to,
    )


__all__ = [
    "CapabilityGatewayInvocation",
    "CapabilityGatewayRequired",
    "CreateEntryCapabilityInput",
    "CreateEntryProposal",
    "create_entry_declaration",
]
