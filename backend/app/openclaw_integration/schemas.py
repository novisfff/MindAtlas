from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.common.schemas import CamelModel
from app.lightrag.schemas import LightRagQueryResponse

OpenClawSystemCapabilityKey = Literal[
    "search_entries",
    "get_entry",
    "create_relation",
    "query_knowledge_graph",
    "generate_periodic_review",
]
OpenClawSystemDefaultKey = Literal[
    "submit_context_capture",
    "search_entries",
    "get_entry",
    "create_relation",
    "query_knowledge_graph",
    "generate_periodic_review",
]
OpenClawCapabilitySourceType = Literal["tool", "workflow", "agent"]
OpenClawCatalogSourceType = Literal["tool", "workflow", "agent"]
OpenClawToolResponseMode = Literal["json_schema", "text_field"]
OpenClawSchemaMode = Literal["readonly", "editable"]


def _normalize_optional_text_input(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _normalize_optional_temporal_input(value: Any) -> Any:
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    return value


class OpenClawEntryRecordResponse(CamelModel):
    id: UUID
    title: str
    summary: str | None = None
    content: str | None = None
    entry_type_code: str = Field(alias="entryTypeCode")
    entry_type_name: str = Field(alias="entryTypeName")
    tag_names: list[str] = Field(default_factory=list, alias="tagNames")
    time_mode: str = Field(alias="timeMode")
    time_at: datetime | None = Field(default=None, alias="timeAt")
    time_from: datetime | None = Field(default=None, alias="timeFrom")
    time_to: datetime | None = Field(default=None, alias="timeTo")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class OpenClawSearchEntriesRequest(CamelModel):
    query: str | None = Field(
        default=None,
        max_length=2000,
        description=(
            "Optional keyword query. Prefer omitting this field or passing null for an unrestricted search. "
            "An empty string is also accepted as a compatibility input and becomes no keyword. "
            "'.' and '*' are treated as literal keywords, not match-all syntax."
        ),
        examples=["weekly review"],
    )
    entry_type: str | None = Field(
        default=None,
        max_length=128,
        alias="entryType",
        description=(
            "Optional entry type filter. Prefer one of the enabled entry type codes exposed below. "
            "Omit or pass null when not filtering. An empty string is also accepted as a compatibility input and "
            "becomes no filter. Localized entry type names may still be accepted for compatibility, but stable "
            "codes are the canonical contract. Do not pass placeholders like '.' or '*'."
        ),
        examples=["KNOWLEDGE"],
    )
    tag_names: list[str] = Field(
        default_factory=list,
        alias="tagNames",
        description=(
            "Optional array of tag names. Prefer existing tag names. Omit this field or use [] when not filtering "
            "by tags. Unknown tag names do not raise input errors, but usually return zero matches."
        ),
        examples=[["work", "planning"]],
    )
    time_from: datetime | None = Field(
        default=None,
        alias="timeFrom",
        description=(
            "Optional lower time bound. Prefer YYYY-MM-DD. Full ISO 8601 datetimes are also accepted. "
            "Omit or pass null when unused. An empty string is also accepted as a compatibility input and becomes "
            "no lower bound. Filtering ultimately uses only the date portion."
        ),
        examples=["2026-04-01T00:00:00+08:00"],
    )
    time_to: datetime | None = Field(
        default=None,
        alias="timeTo",
        description=(
            "Optional upper time bound. Prefer YYYY-MM-DD. Full ISO 8601 datetimes are also accepted. "
            "Omit or pass null when unused. An empty string is also accepted as a compatibility input and becomes "
            "no upper bound. Filtering ultimately uses only the date portion."
        ),
        examples=["2026-04-30T23:59:59+08:00"],
    )
    limit: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum number of matched entries to return. Must be an integer between 1 and 50.",
        examples=[10],
    )

    @field_validator("query", "entry_type", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: str | None) -> str | None:
        return _normalize_optional_text_input(value)

    @field_validator("time_from", "time_to", mode="before")
    @classmethod
    def _normalize_optional_temporal_fields(cls, value: Any) -> Any:
        return _normalize_optional_temporal_input(value)

    @field_validator("tag_names", mode="before")
    @classmethod
    def _normalize_tag_names(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("tagNames must be a list")
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            text = str(item or "").strip()
            lowered = text.lower()
            if not text or lowered in seen:
                continue
            seen.add(lowered)
            normalized.append(text)
        return normalized


class OpenClawSearchEntriesResponse(CamelModel):
    total: int
    items: list[OpenClawEntryRecordResponse] = Field(default_factory=list)


class OpenClawGetEntryRequest(CamelModel):
    entry_id: UUID = Field(
        alias="entryId",
        description=(
            "Required entry ID. This is the canonical input. Usually use the id from a previous search result or "
            "an exact known record. Compatibility note: the capability may also accept a search-hit object that "
            "contains id, but entryId remains the primary contract."
        ),
        examples=["123e4567-e89b-12d3-a456-426614174000"],
    )


class OpenClawCaptureEntryRequest(CamelModel):
    title: str = Field(min_length=1, max_length=255)
    summary: str | None = Field(default=None, max_length=4000)
    content: str | None = Field(default=None, max_length=40000)
    entry_type: str = Field(min_length=1, max_length=128, alias="entryType")
    tag_names: list[str] = Field(default_factory=list, alias="tagNames")
    time_at: datetime | None = Field(default=None, alias="timeAt")
    time_from: datetime | None = Field(default=None, alias="timeFrom")
    time_to: datetime | None = Field(default=None, alias="timeTo")

    @field_validator("title", "summary", "content", "entry_type", mode="before")
    @classmethod
    def _normalize_required_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @field_validator("tag_names", mode="before")
    @classmethod
    def _normalize_capture_tag_names(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("tagNames must be a list")
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            text = str(item or "").strip()
            lowered = text.lower()
            if not text or lowered in seen:
                continue
            seen.add(lowered)
            normalized.append(text)
        return normalized


class OpenClawCreateRelationRequest(CamelModel):
    source_entry_id: UUID = Field(
        alias="sourceEntryId",
        description="Required source entry ID, usually taken from a previous search result.",
        examples=["123e4567-e89b-12d3-a456-426614174000"],
    )
    target_entry_id: UUID = Field(
        alias="targetEntryId",
        description="Required target entry ID, usually taken from a previous search result.",
        examples=["123e4567-e89b-12d3-a456-426614174001"],
    )
    relation_type: str = Field(
        min_length=1,
        max_length=128,
        alias="relationType",
        description=(
            "Required relation type. Prefer one of the enabled relation type codes exposed below. "
            "Localized relation type names may still be accepted for compatibility, but stable codes are the "
            "canonical contract."
        ),
        examples=["RELATED_TO"],
    )
    description: str | None = Field(
        default=None,
        max_length=512,
        description="Optional short explanation for why the two entries are related.",
    )

    @field_validator("relation_type", "description", mode="before")
    @classmethod
    def _normalize_relation_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None


class OpenClawRelationRecordResponse(CamelModel):
    id: UUID
    source_entry_id: UUID = Field(alias="sourceEntryId")
    source_entry_title: str = Field(alias="sourceEntryTitle")
    target_entry_id: UUID = Field(alias="targetEntryId")
    target_entry_title: str = Field(alias="targetEntryTitle")
    relation_type_code: str = Field(alias="relationTypeCode")
    relation_type_name: str = Field(alias="relationTypeName")
    description: str | None = None


class OpenClawQueryKnowledgeGraphRequest(CamelModel):
    query: str = Field(
        min_length=1,
        max_length=4000,
        description="Required natural-language question for the knowledge graph.",
        examples=["What connects my recent project and hiring notes?"],
    )
    mode: Literal["naive", "local", "global", "hybrid", "mix"] = Field(
        default="hybrid",
        description="Optional retrieval mode. Supported values: naive, local, global, hybrid, mix.",
    )
    top_k: int = Field(
        default=5,
        ge=1,
        le=20,
        alias="topK",
        description="Maximum number of source items to retrieve. Must be an integer between 1 and 20.",
        examples=[5],
    )

    @field_validator("query", mode="before")
    @classmethod
    def _normalize_query(cls, value: str | None) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("query is required")
        return normalized


class OpenClawGeneratePeriodicReviewRequest(CamelModel):
    focus: Literal["overview", "type", "tag", "trend"] | None = Field(
        default=None,
        description=(
            "Optional review focus. Use overview for a general recap, type for entry-type distribution, "
            "tag for tag hotspots, or trend for activity and change patterns."
        ),
        examples=["overview"],
    )
    period: str | None = Field(
        default=None,
        max_length=128,
        description=(
            "Optional natural-language relative period such as last week, this month, the last two weeks, or 2026 Q1. "
            "Prefer omitting it or passing null when explicit dates are provided."
        ),
        examples=["last week"],
    )
    start_date: date | None = Field(
        default=None,
        alias="startDate",
        description=(
            "Optional explicit start date in YYYY-MM-DD. If only one explicit date is provided, the review is treated "
            "as a single-day recap. Explicit dates take priority over `period`."
        ),
        examples=["2026-03-01"],
    )
    end_date: date | None = Field(
        default=None,
        alias="endDate",
        description=(
            "Optional explicit end date in YYYY-MM-DD. If omitted while startDate is provided, the review is treated "
            "as a single-day recap. Explicit dates take priority over `period`."
        ),
        examples=["2026-03-31"],
    )

    @field_validator("focus", "period", mode="before")
    @classmethod
    def _normalize_optional_review_text(cls, value: Any) -> str | None:
        return _normalize_optional_text_input(value)

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def _normalize_optional_review_dates(cls, value: Any) -> Any:
        return _normalize_optional_temporal_input(value)

    @model_validator(mode="after")
    def _validate_date_order(self) -> "OpenClawGeneratePeriodicReviewRequest":
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("startDate must be less than or equal to endDate")
        return self


class OpenClawPeriodicReviewResponse(CamelModel):
    content: str = Field(
        description="A user-facing periodic review in Markdown.",
    )


class OpenClawIntegrationUpdateRequest(CamelModel):
    enabled: bool


class OpenClawCapabilityItemBaseRequest(CamelModel):
    source_type: OpenClawCatalogSourceType = Field(alias="sourceType")
    tool_name: str = Field(alias="toolName", min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=256)
    description: str = Field(default="", max_length=4000)
    enabled: bool = True
    input_summary: str = Field(default="", alias="inputSummary", max_length=4000)
    output_summary: str = Field(default="", alias="outputSummary", max_length=4000)
    input_schema: dict[str, Any] | None = Field(default=None, alias="inputSchema")
    output_schema: dict[str, Any] | None = Field(default=None, alias="outputSchema")
    tool_response_mode: OpenClawToolResponseMode | None = Field(default=None, alias="toolResponseMode")
    source_tool_name: str | None = Field(default=None, alias="sourceToolName", max_length=128)
    tool_id: UUID | None = Field(default=None, alias="toolId")
    workflow_id: UUID | None = Field(default=None, alias="workflowId")
    agent_profile_id: UUID | None = Field(default=None, alias="agentProfileId")

    @field_validator("tool_name", "title", "description", "input_summary", "output_summary", mode="before")
    @classmethod
    def _normalize_text_fields(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @field_validator("source_tool_name", mode="before")
    @classmethod
    def _normalize_optional_source_tool_name(cls, value: Any) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @model_validator(mode="after")
    def _validate_source_binding(self) -> "OpenClawCapabilityItemBaseRequest":
        if self.source_type == "tool":
            if self.workflow_id or self.agent_profile_id:
                raise ValueError("tool source cannot include workflowId or agentProfileId")
            if self.tool_id is None and not self.source_tool_name:
                raise ValueError("tool source requires toolId or sourceToolName")
        elif self.source_type == "workflow":
            if self.workflow_id is None:
                raise ValueError("workflow source requires workflowId")
            if self.tool_id is not None or self.source_tool_name or self.agent_profile_id is not None:
                raise ValueError("workflow source cannot include tool or agent bindings")
        elif self.source_type == "agent":
            if self.agent_profile_id is None:
                raise ValueError("agent source requires agentProfileId")
            if self.tool_id is not None or self.source_tool_name or self.workflow_id is not None:
                raise ValueError("agent source cannot include tool or workflow bindings")
        return self


class OpenClawCapabilityItemCreateRequest(OpenClawCapabilityItemBaseRequest):
    pass


class OpenClawCapabilityItemUpdateRequest(CamelModel):
    source_type: OpenClawCatalogSourceType | None = Field(default=None, alias="sourceType")
    tool_name: str | None = Field(default=None, alias="toolName", min_length=1, max_length=128)
    title: str | None = Field(default=None, min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=4000)
    enabled: bool | None = None
    input_summary: str | None = Field(default=None, alias="inputSummary", max_length=4000)
    output_summary: str | None = Field(default=None, alias="outputSummary", max_length=4000)
    input_schema: dict[str, Any] | None = Field(default=None, alias="inputSchema")
    output_schema: dict[str, Any] | None = Field(default=None, alias="outputSchema")
    tool_response_mode: OpenClawToolResponseMode | None = Field(default=None, alias="toolResponseMode")
    source_tool_name: str | None = Field(default=None, alias="sourceToolName", max_length=128)
    tool_id: UUID | None = Field(default=None, alias="toolId")
    workflow_id: UUID | None = Field(default=None, alias="workflowId")
    agent_profile_id: UUID | None = Field(default=None, alias="agentProfileId")

    @field_validator("tool_name", "title", "description", "input_summary", "output_summary", mode="before")
    @classmethod
    def _normalize_optional_text_fields(cls, value: Any) -> str | None:
        if value is None:
            return None
        return str(value).strip()

    @field_validator("source_tool_name", mode="before")
    @classmethod
    def _normalize_optional_source_tool_name(cls, value: Any) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None


class OpenClawCatalogSourceResponse(CamelModel):
    source_type: OpenClawCatalogSourceType = Field(alias="sourceType")
    source_key: str = Field(alias="sourceKey")
    title: str
    description: str
    source_name: str | None = Field(default=None, alias="sourceName")
    source_description: str | None = Field(default=None, alias="sourceDescription")
    is_system: bool = Field(alias="isSystem")
    enabled: bool
    bindable: bool
    unavailable_reason: str | None = Field(default=None, alias="unavailableReason")
    schema_mode: OpenClawSchemaMode = Field(alias="schemaMode")
    source_tool_name: str | None = Field(default=None, alias="sourceToolName")
    tool_id: UUID | None = Field(default=None, alias="toolId")
    workflow_id: UUID | None = Field(default=None, alias="workflowId")
    agent_profile_id: UUID | None = Field(default=None, alias="agentProfileId")
    published_version_id: UUID | None = Field(default=None, alias="publishedVersionId")
    default_input_schema: dict[str, Any] | None = Field(default=None, alias="defaultInputSchema")
    default_output_schema: dict[str, Any] | None = Field(default=None, alias="defaultOutputSchema")
    default_input_summary: str = Field(default="", alias="defaultInputSummary")
    default_output_summary: str = Field(default="", alias="defaultOutputSummary")
    default_tool_response_mode: OpenClawToolResponseMode | None = Field(
        default=None,
        alias="defaultToolResponseMode",
    )


class OpenClawCatalogSourceListResponse(CamelModel):
    items: list[OpenClawCatalogSourceResponse] = Field(default_factory=list)


class OpenClawCapabilityItemResponse(CamelModel):
    id: UUID
    capability_key: str = Field(alias="capabilityKey")
    tool_name: str = Field(alias="toolName")
    title: str
    description: str
    source_type: OpenClawCapabilitySourceType = Field(alias="sourceType")
    implementation_type: str = Field(alias="implementationType")
    system_default_key: str | None = Field(default=None, alias="systemDefaultKey")
    source_tool_name: str | None = Field(default=None, alias="sourceToolName")
    tool_id: UUID | None = Field(default=None, alias="toolId")
    workflow_id: UUID | None = Field(default=None, alias="workflowId")
    agent_profile_id: UUID | None = Field(default=None, alias="agentProfileId")
    source_name: str | None = Field(default=None, alias="sourceName")
    source_description: str | None = Field(default=None, alias="sourceDescription")
    source_is_system: bool = Field(default=False, alias="sourceIsSystem")
    source_enabled: bool | None = Field(default=None, alias="sourceEnabled")
    published_version_id: UUID | None = Field(default=None, alias="publishedVersionId")
    enabled: bool
    is_system_item: bool = Field(alias="isSystemItem")
    retired: bool = False
    retirement_reason: str | None = Field(default=None, alias="retirementReason")
    available: bool
    availability_reason: str | None = Field(default=None, alias="availabilityReason")
    schema_editable: bool = Field(alias="schemaEditable")
    input_summary: str = Field(alias="inputSummary")
    output_summary: str = Field(alias="outputSummary")
    input_schema: dict[str, Any] = Field(alias="inputSchema")
    output_schema: dict[str, Any] = Field(alias="outputSchema")
    tool_response_mode: OpenClawToolResponseMode = Field(alias="toolResponseMode")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class OpenClawRuntimeCapabilityResponse(CamelModel):
    capability_key: str = Field(alias="capabilityKey")
    tool_name: str = Field(alias="toolName")
    title: str
    description: str
    source_type: OpenClawCapabilitySourceType = Field(alias="sourceType")
    implementation_type: str = Field(alias="implementationType")
    available: bool
    availability_reason: str | None = Field(default=None, alias="availabilityReason")
    input_summary: str = Field(alias="inputSummary")
    output_summary: str = Field(alias="outputSummary")
    input_schema: dict[str, Any] = Field(alias="inputSchema")
    output_schema: dict[str, Any] = Field(alias="outputSchema")
    tool_response_mode: OpenClawToolResponseMode = Field(alias="toolResponseMode")


class OpenClawIntegrationSettingsResponse(CamelModel):
    enabled: bool
    secret_configured: bool = Field(alias="secretConfigured")
    secret_hint: str | None = Field(default=None, alias="secretHint")
    secret_last_rotated_at: datetime | None = Field(default=None, alias="secretLastRotatedAt")
    sync_warning: str | None = Field(default=None, alias="syncWarning")
    catalog_items: list[OpenClawCapabilityItemResponse] = Field(default_factory=list, alias="catalogItems")


class OpenClawRotateSecretResponse(CamelModel):
    secret: str
    settings: OpenClawIntegrationSettingsResponse


class OpenClawCapabilityCatalogResponse(CamelModel):
    integration_name: str = Field(alias="integrationName")
    capabilities: list[OpenClawRuntimeCapabilityResponse] = Field(default_factory=list)


class OpenClawCapabilityExecuteResponse(CamelModel):
    capability_key: str = Field(alias="capabilityKey")
    tool_name: str = Field(alias="toolName")
    result: Any


OPENCLAW_SYSTEM_CAPABILITY_INPUT_MODELS: dict[OpenClawSystemCapabilityKey, type[CamelModel]] = {
    "search_entries": OpenClawSearchEntriesRequest,
    "get_entry": OpenClawGetEntryRequest,
    "create_relation": OpenClawCreateRelationRequest,
    "query_knowledge_graph": OpenClawQueryKnowledgeGraphRequest,
    "generate_periodic_review": OpenClawGeneratePeriodicReviewRequest,
}

OPENCLAW_SYSTEM_CAPABILITY_OUTPUT_MODELS: dict[OpenClawSystemCapabilityKey, type[Any]] = {
    "search_entries": OpenClawSearchEntriesResponse,
    "get_entry": OpenClawEntryRecordResponse,
    "create_relation": OpenClawRelationRecordResponse,
    "query_knowledge_graph": LightRagQueryResponse,
    "generate_periodic_review": OpenClawPeriodicReviewResponse,
}
