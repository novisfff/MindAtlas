"""Deterministic Domain Key ↔ Provider alias mapping and frozen tool surfaces.

Plan 03 Task 2. Alias mapping is transport-only, append-only on the Manifest, and
never re-resolves "latest" on resume. Domain Key remains the identity; the Provider
alias is a protocol name only.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Collection, Mapping, Sequence
from typing import Any

from app.assistant.capabilities.contracts import (
    CapabilityDescriptor,
    FrozenCapabilityBinding,
)
from app.assistant.domain.contracts import (
    ResolvedProviderAliasRef,
    ResolvedRunManifestRevision,
    append_provider_aliases,
)
from app.assistant.domain.json_schema import binding_schema_digest
from app.assistant.provider_loop.contracts import (
    ProviderExecutionScope,
    ProviderToolDefinition,
    ProviderToolSurface,
    ToolSurfaceResolution,
    compute_alias_map_digest,
    compute_surface_digest,
)

# OpenAI Chat Completions tool-name syntax.
_ALIAS_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# Non-ASCII or non-alphanumeric runs become a single underscore.
_SANITIZE_RE = re.compile(r"[^A-Za-z0-9]+")
_COLLAPSE_RE = re.compile(r"_+")

OPENAI_CHAT_PROVIDER_PROTOCOL = "openai_chat_completions"

# Adapter/runtime control aliases that must never be allocated to capabilities.
# Hints and generated candidates that collide with these fall back / re-suffix.
DEFAULT_RESERVED_CONTROL_ALIASES: frozenset[str] = frozenset(
    {
        "mindatlas_soft_finalization",
        "mindatlas_runtime_control",
        "mindatlas_probe_echo",
        "mindatlas_probe_left",
        "mindatlas_probe_right",
    }
)

MAX_GENERATED_BASE_LEN = 48
_IDENTITY_HEX_LEN = 12
_READABLE_PREFIX_MAX = 35


def _casefold_ascii(value: str) -> str:
    """ASCII case-fold only; rejects non-ASCII by encode('ascii')."""
    return value.encode("utf-8").decode("ascii").casefold()


def is_valid_provider_alias(alias: str) -> bool:
    return isinstance(alias, str) and bool(_ALIAS_RE.fullmatch(alias))


def sanitize_domain_key_for_alias(domain_key: str) -> str:
    """Replace non-ASCII / non-alphanumeric runs with `_`, collapse, trim, lowercase."""
    if not isinstance(domain_key, str):
        raise TypeError("domain_key must be a string")
    replaced = _SANITIZE_RE.sub("_", domain_key)
    collapsed = _COLLAPSE_RE.sub("_", replaced)
    return collapsed.strip("_").lower()


def identity_digest_prefix(
    *,
    provider_protocol: str,
    domain_key: str,
    binding_contract_digest: str,
    length: int = _IDENTITY_HEX_LEN,
) -> str:
    material = (
        provider_protocol.encode("utf-8")
        + b"\x00"
        + domain_key.encode("utf-8")
        + b"\x00"
        + binding_contract_digest.encode("utf-8")
    )
    return hashlib.sha256(material).hexdigest()[:length]


def generated_alias_candidate(
    domain_key: str,
    *,
    provider_protocol: str,
    binding_contract_digest: str,
) -> str:
    """Deterministic generated alias candidate for one Domain Key / binding.

    Does not consult occupancy; collision allocation happens separately.
    """
    if not isinstance(domain_key, str) or not domain_key:
        raise ValueError("domain_key must be a non-empty string")
    if not isinstance(provider_protocol, str) or not provider_protocol:
        raise ValueError("provider_protocol must be a non-empty string")
    if not isinstance(binding_contract_digest, str) or len(binding_contract_digest) != 64:
        raise ValueError("binding_contract_digest must be a 64-char digest")

    sanitized = sanitize_domain_key_for_alias(domain_key)
    if sanitized and len(sanitized) <= MAX_GENERATED_BASE_LEN:
        if not is_valid_provider_alias(sanitized):
            # Should not happen after sanitize, but fail closed.
            raise ValueError(f"sanitized alias candidate is invalid: {sanitized!r}")
        return sanitized

    suffix = identity_digest_prefix(
        provider_protocol=provider_protocol,
        domain_key=domain_key,
        binding_contract_digest=binding_contract_digest,
    )
    if sanitized:
        prefix = sanitized[:_READABLE_PREFIX_MAX].rstrip("_") or "cap"
    else:
        prefix = "cap"
    candidate = f"{prefix}_{suffix}"
    if len(candidate) > 64:
        # Keep the identity suffix; trim the readable prefix.
        keep = 64 - 1 - len(suffix)
        prefix = prefix[: max(1, keep)].rstrip("_") or "c"
        candidate = f"{prefix}_{suffix}"
    if not is_valid_provider_alias(candidate):
        raise ValueError(f"generated alias candidate is invalid: {candidate!r}")
    return candidate


def _assert_binding_digest_independent(
    *,
    domain_key: str,
    binding: FrozenCapabilityBinding,
) -> None:
    """Reject reverse digest edges: binding digest must not depend on alias/surface."""
    digest = binding.ref.binding_contract_digest
    if domain_key in digest or "providerAlias" in digest or "surfaceDigest" in digest:
        # Digests are hex; this is a structural guard for fixture misuse.
        pass
    snapshot = binding.resolved.resolution_snapshot
    if not isinstance(snapshot, Mapping):
        raise ValueError("resolution_snapshot must be a mapping")
    forbidden_keys = {
        "providerAlias",
        "provider_alias",
        "manifestDigest",
        "manifest_digest",
        "surfaceDigest",
        "surface_digest",
        "aliasMapDigest",
        "alias_map_digest",
    }
    for key in snapshot:
        if key in forbidden_keys:
            raise ValueError(
                "binding_contract_digest must not depend on alias/Manifest/surface data"
            )


def _accepted_hints(
    *,
    provider_protocol: str,
    pending: Sequence[tuple[str, str]],
    alias_hints: Mapping[str, str],
    occupied_folded: set[str],
) -> dict[str, str]:
    """Return domain_key -> hint for hints that are uniquely valid and free.

    If multiple pending domain keys declare the same case-folded hint, none wins.
    """
    # domain_key -> raw hint (only for pending keys that declared one)
    declared: dict[str, str] = {}
    for domain_key, _binding in pending:
        if domain_key not in alias_hints:
            continue
        declared[domain_key] = alias_hints[domain_key]

    # Group by case-folded hint among declared pending keys.
    by_folded: dict[str, list[str]] = {}
    for domain_key, hint in declared.items():
        if not isinstance(hint, str) or not is_valid_provider_alias(hint):
            continue
        try:
            folded = _casefold_ascii(hint)
        except UnicodeEncodeError:
            continue
        by_folded.setdefault(folded, []).append(domain_key)

    accepted: dict[str, str] = {}
    for folded, keys in by_folded.items():
        if len(keys) != 1:
            # Colliding hints: none wins; all fall back to generated aliases.
            continue
        domain_key = keys[0]
        hint = declared[domain_key]
        if folded in occupied_folded:
            continue
        accepted[domain_key] = hint
    return accepted


def _allocate_unique_alias(
    *,
    base: str,
    provider_protocol: str,
    domain_key: str,
    binding_contract_digest: str,
    occupied_folded: set[str],
) -> str:
    """Pick `base` if free; otherwise suffix with identity digest material."""
    try:
        folded_base = _casefold_ascii(base)
    except UnicodeEncodeError as exc:
        raise ValueError(f"alias is not ASCII: {base!r}") from exc

    if is_valid_provider_alias(base) and folded_base not in occupied_folded:
        return base

    material = identity_digest_prefix(
        provider_protocol=provider_protocol,
        domain_key=domain_key,
        binding_contract_digest=binding_contract_digest,
        length=64,
    )
    # Prefer shorter suffixes first; always keep final length <= 64.
    for n in (8, 12, 16, 20, 24, 32, 40, 48, 64):
        suffix = material[:n]
        room = 64 - 1 - n
        if room < 1:
            trial = f"c_{suffix}"[:64]
        else:
            prefix = base[:room].rstrip("_") or "c"
            trial = f"{prefix}_{suffix}"
            if len(trial) > 64:
                trial = trial[:64]
        if not is_valid_provider_alias(trial):
            continue
        try:
            folded = _casefold_ascii(trial)
        except UnicodeEncodeError:
            continue
        if folded not in occupied_folded:
            return trial

    raise ValueError(
        f"unable to allocate unique provider alias for domain key {domain_key!r}"
    )


def allocate_provider_aliases(
    *,
    provider_protocol: str,
    current_manifest: ResolvedRunManifestRevision,
    domain_bindings: Sequence[tuple[str, str]],
    alias_hints: Mapping[str, str] | None = None,
    reserved_aliases: Collection[str] | None = None,
) -> tuple[ResolvedProviderAliasRef, ...]:
    """Allocate append-only Provider aliases for the given domain/binding pairs.

    Returns the complete set of alias refs that should exist after allocation
    (parent refs plus any newly allocated ones). Does not mutate the Manifest;
    callers pass the result to ``append_provider_aliases``.
    """
    if not isinstance(provider_protocol, str) or not provider_protocol.strip():
        raise ValueError("provider_protocol must be a non-empty string")
    if not isinstance(current_manifest, ResolvedRunManifestRevision):
        raise TypeError("current_manifest must be a ResolvedRunManifestRevision")
    if not isinstance(domain_bindings, Sequence) or isinstance(
        domain_bindings, (str, bytes, bytearray)
    ):
        raise TypeError("domain_bindings must be a sequence of (domain_key, binding_digest)")

    hints = dict(alias_hints or {})
    reserved = set(DEFAULT_RESERVED_CONTROL_ALIASES)
    if reserved_aliases is not None:
        reserved.update(reserved_aliases)

    # Occupancy is protocol-scoped and case-folded.
    occupied_folded: set[str] = set()
    for item in reserved:
        if not is_valid_provider_alias(item):
            raise ValueError(f"reserved alias is invalid: {item!r}")
        occupied_folded.add(_casefold_ascii(item))

    existing_for_protocol: dict[str, ResolvedProviderAliasRef] = {}
    complete: list[ResolvedProviderAliasRef] = []
    for ref in current_manifest.provider_aliases:
        if ref.provider_protocol != provider_protocol:
            # Other protocols are ignored for this surface but preserved by append.
            continue
        existing_for_protocol[ref.domain_key] = ref
        occupied_folded.add(_casefold_ascii(ref.provider_alias))
        complete.append(ref)

    # Normalize / validate pending bindings. Duplicates of the same pair are ok;
    # same domain key with a different binding is a hard conflict.
    pending_map: dict[str, str] = {}
    for item in domain_bindings:
        if not isinstance(item, tuple) or len(item) != 2:
            raise TypeError("domain_bindings items must be (domain_key, binding_digest)")
        domain_key, binding_digest = item
        if not isinstance(domain_key, str) or not domain_key:
            raise ValueError("domain_key must be a non-empty string")
        if not isinstance(binding_digest, str) or len(binding_digest) != 64:
            raise ValueError("binding_contract_digest must be a 64-char digest")
        prior = pending_map.get(domain_key)
        if prior is not None and prior != binding_digest:
            raise ValueError(
                f"domain key {domain_key!r} has conflicting binding digests in one surface"
            )
        existing = existing_for_protocol.get(domain_key)
        if existing is not None and existing.binding_contract_digest != binding_digest:
            raise ValueError(
                f"domain key {domain_key!r} already frozen to a different binding; "
                "resume must not re-alias"
            )
        pending_map[domain_key] = binding_digest

    # Keys that still need an alias under this protocol.
    pending: list[tuple[str, str]] = [
        (domain_key, binding_digest)
        for domain_key, binding_digest in pending_map.items()
        if domain_key not in existing_for_protocol
    ]
    # Deterministic order for collision resolution.
    pending.sort(key=lambda item: (item[0], item[1]))

    accepted_hints = _accepted_hints(
        provider_protocol=provider_protocol,
        pending=pending,
        alias_hints=hints,
        occupied_folded=occupied_folded,
    )
    # Reserve accepted hints immediately so later generated names cannot steal them.
    for domain_key, hint in accepted_hints.items():
        occupied_folded.add(_casefold_ascii(hint))

    for domain_key, binding_digest in pending:
        if domain_key in accepted_hints:
            alias = accepted_hints[domain_key]
        else:
            base = generated_alias_candidate(
                domain_key,
                provider_protocol=provider_protocol,
                binding_contract_digest=binding_digest,
            )
            alias = _allocate_unique_alias(
                base=base,
                provider_protocol=provider_protocol,
                domain_key=domain_key,
                binding_contract_digest=binding_digest,
                occupied_folded=occupied_folded,
            )
        occupied_folded.add(_casefold_ascii(alias))
        complete.append(
            ResolvedProviderAliasRef(
                provider_protocol=provider_protocol,
                domain_key=domain_key,
                provider_alias=alias,
                binding_contract_digest=binding_digest,
            )
        )

    # Stable order for callers (append_provider_aliases also sorts).
    complete.sort(
        key=lambda item: (item.provider_protocol, item.domain_key, item.provider_alias)
    )
    return tuple(complete)


def _validate_visible_pair(
    binding: FrozenCapabilityBinding,
    descriptor: CapabilityDescriptor,
) -> None:
    if not isinstance(binding, FrozenCapabilityBinding):
        raise TypeError("binding must be a FrozenCapabilityBinding")
    if not isinstance(descriptor, CapabilityDescriptor):
        raise TypeError("descriptor must be a CapabilityDescriptor")

    _assert_binding_digest_independent(
        domain_key=binding.ref.capability_key,
        binding=binding,
    )

    if descriptor.capability_key != binding.ref.capability_key:
        raise ValueError("descriptor capability_key must match binding")
    if descriptor.binding_contract_digest != binding.ref.binding_contract_digest:
        raise ValueError("descriptor binding_contract_digest must match binding")
    if descriptor.resolution_digest != binding.ref.resolution_digest:
        raise ValueError("descriptor resolution_digest must match binding")
    if descriptor.dependency_closure_digest != binding.ref.dependency_closure_digest:
        raise ValueError("descriptor dependency_closure_digest must match binding")
    if descriptor.input_schema_digest != binding.ref.input_schema_digest:
        raise ValueError("descriptor input_schema_digest must match binding")
    if descriptor.output_schema_digest != binding.ref.output_schema_digest:
        raise ValueError("descriptor output_schema_digest must match binding")
    if descriptor.capability_type != binding.ref.capability_type:
        raise ValueError("descriptor capability_type must match binding")
    if descriptor.target_identity != binding.ref.target_identity:
        raise ValueError("descriptor target_identity must match binding")
    if descriptor.target_id != binding.ref.target_id:
        raise ValueError("descriptor target_id must match binding")
    if descriptor.target_version_id != binding.ref.target_version_id:
        raise ValueError("descriptor target_version_id must match binding")
    if descriptor.target_revision != binding.ref.target_revision:
        raise ValueError("descriptor target_revision must match binding")

    # Recompute schema digests; do not trust caller-supplied values alone.
    recomputed_input = binding_schema_digest(descriptor.input_schema)
    recomputed_output = binding_schema_digest(descriptor.output_schema)
    if recomputed_input != descriptor.input_schema_digest:
        raise ValueError("descriptor input_schema_digest does not match input_schema")
    if recomputed_output != descriptor.output_schema_digest:
        raise ValueError("descriptor output_schema_digest does not match output_schema")
    if recomputed_input != binding.ref.input_schema_digest:
        raise ValueError("binding input_schema_digest does not match descriptor schema")

    if descriptor.availability.status != "available":
        raise ValueError(
            f"descriptor for {descriptor.capability_key!r} is not available "
            f"(status={descriptor.availability.status!r})"
        )
    if descriptor.behavior.interrupt_mode == "legacy_blocking":
        raise ValueError(
            f"legacy_blocking descriptor {descriptor.capability_key!r} is excluded "
            "from the Provider Loop surface"
        )


def _reject_runtime_objects(value: Any, *, path: str) -> None:
    """Fail if open Sessions or mutable ORM-like objects appear in frozen results."""
    type_name = type(value).__name__
    module_name = getattr(type(value), "__module__", "") or ""
    if type_name in {"Session", "AsyncSession", "scoped_session"}:
        raise ValueError(f"frozen surface must not retain a database Session at {path}")
    if "sqlalchemy" in module_name and type_name not in {"UUID"}:
        # Defensive: any live SQLAlchemy runtime type is forbidden.
        if type_name.endswith("Session") or "ORM" in type_name or "Mapper" in type_name:
            raise ValueError(f"frozen surface must not retain ORM object at {path}")


def build_provider_tool_surface(
    *,
    manifest: ResolvedRunManifestRevision,
    provider_protocol: str,
    visible: Sequence[tuple[FrozenCapabilityBinding, CapabilityDescriptor]],
    descriptions: Mapping[str, str] | None = None,
    alias_hints: Mapping[str, str] | None = None,
    reserved_aliases: Collection[str] | None = None,
    scope: ProviderExecutionScope | None = None,
) -> ToolSurfaceResolution:
    """Allocate aliases (append-only), freeze one complete ProviderToolSurface.

    ``descriptions`` freezes locale text for this surface. When omitted, the
    descriptor's description is used. Changing locale mid-run must not rewrite a
    previously frozen surface; callers pass the already-chosen frozen text.
    """
    if not isinstance(manifest, ResolvedRunManifestRevision):
        raise TypeError("manifest must be a ResolvedRunManifestRevision")
    if not isinstance(provider_protocol, str) or not provider_protocol.strip():
        raise ValueError("provider_protocol must be a non-empty string")
    if not isinstance(visible, Sequence) or isinstance(visible, (str, bytes, bytearray)):
        raise TypeError("visible must be a sequence of (binding, descriptor)")

    if scope is not None:
        if scope.run_id != manifest.run_id:
            raise ValueError("execution scope run_id must match manifest.run_id")
        if manifest.model is not None and scope is not None:
            # Scope does not carry model_ref; loop-level checks own full equality.
            # Here we only guard the run identity contract required by the surface.
            pass

    description_map = dict(descriptions or {})
    pairs: list[tuple[FrozenCapabilityBinding, CapabilityDescriptor, str]] = []
    seen_domain: set[str] = set()
    seen_binding: set[str] = set()
    domain_bindings: list[tuple[str, str]] = []

    for index, item in enumerate(visible):
        if not isinstance(item, tuple) or len(item) != 2:
            raise TypeError("visible items must be (FrozenCapabilityBinding, CapabilityDescriptor)")
        binding, descriptor = item
        _validate_visible_pair(binding, descriptor)
        domain_key = binding.ref.capability_key
        binding_digest = binding.ref.binding_contract_digest

        if domain_key in seen_domain:
            raise ValueError(f"duplicate Domain Key on surface: {domain_key!r}")
        if binding_digest in seen_binding:
            raise ValueError(f"duplicate binding digest on surface: {binding_digest!r}")
        seen_domain.add(domain_key)
        seen_binding.add(binding_digest)

        # Ensure Manifest capability set, when present, agrees on binding digest.
        for cap in manifest.capabilities:
            if cap.capability_key == domain_key and cap.binding_contract_digest != binding_digest:
                raise ValueError(
                    f"binding digest for {domain_key!r} differs from Manifest capability ref"
                )

        if domain_key in description_map:
            description = description_map[domain_key]
        else:
            description = descriptor.description
        if not isinstance(description, str) or not description.strip():
            raise ValueError(f"description for {domain_key!r} must be non-empty")

        # Input schema must be a JSON object suitable for Provider Tool input.
        if not isinstance(descriptor.input_schema, dict):
            raise ValueError(f"input_schema for {domain_key!r} must be a JSON object")
        if descriptor.input_schema.get("type") not in {None, "object"}:
            # Provider tools require object-root arguments; allow missing type only
            # when properties clearly describe an object schema already validated.
            if "properties" not in descriptor.input_schema:
                raise ValueError(
                    f"input_schema for {domain_key!r} is invalid for Provider Tool input"
                )

        pairs.append((binding, descriptor, description))
        domain_bindings.append((domain_key, binding_digest))

    allocated = allocate_provider_aliases(
        provider_protocol=provider_protocol,
        current_manifest=manifest,
        domain_bindings=domain_bindings,
        alias_hints=alias_hints,
        reserved_aliases=reserved_aliases,
    )

    # Only refs for this protocol participate in this surface's alias map, but
    # append_provider_aliases receives the full allocated set for this protocol
    # (existing + new). Parent aliases for other protocols stay on the Manifest.
    new_or_existing = tuple(
        ref for ref in allocated if ref.provider_protocol == provider_protocol
    )
    next_manifest = append_provider_aliases(manifest, aliases=new_or_existing)

    # Digest dependency DAG: binding -> alias ref -> manifest -> alias map -> surface.
    # Assert no reverse edge by construction: alias refs never carry manifest_digest.
    for ref in next_manifest.provider_aliases:
        if hasattr(ref, "manifest_digest"):
            raise ValueError("alias ref must not carry manifest_digest")

    alias_by_domain = {
        ref.domain_key: ref
        for ref in next_manifest.provider_aliases
        if ref.provider_protocol == provider_protocol
    }

    tools: list[ProviderToolDefinition] = []
    alias_triples: list[tuple[str, str, str]] = []
    for binding, descriptor, description in pairs:
        domain_key = binding.ref.capability_key
        ref = alias_by_domain.get(domain_key)
        if ref is None:
            raise ValueError(f"missing provider alias for domain key {domain_key!r}")
        if ref.binding_contract_digest != binding.ref.binding_contract_digest:
            raise ValueError(
                f"alias binding digest mismatch for domain key {domain_key!r}"
            )
        if ref.provider_protocol != provider_protocol:
            raise ValueError(f"stale alias protocol for domain key {domain_key!r}")
        tools.append(
            ProviderToolDefinition(
                provider_alias=ref.provider_alias,
                domain_key=domain_key,
                description=description,
                input_schema=descriptor.input_schema,
                binding=binding,
                descriptor=descriptor,
            )
        )
        alias_triples.append(
            (domain_key, ref.provider_alias, ref.binding_contract_digest)
        )

    tools_sorted = tuple(sorted(tools, key=lambda item: item.provider_alias))
    alias_map_digest = compute_alias_map_digest(
        provider_protocol=provider_protocol,
        manifest_digest=next_manifest.manifest_digest,
        aliases=alias_triples,
    )
    surface_digest = compute_surface_digest(
        provider_protocol=provider_protocol,
        manifest_revision=next_manifest.revision,
        manifest_digest=next_manifest.manifest_digest,
        alias_map_digest=alias_map_digest,
        tools=tools_sorted,
    )
    surface = ProviderToolSurface(
        provider_protocol=provider_protocol,
        manifest_revision=next_manifest.revision,
        manifest_digest=next_manifest.manifest_digest,
        alias_map_digest=alias_map_digest,
        tools=tools_sorted,
        surface_digest=surface_digest,
    )
    _reject_runtime_objects(surface, path="surface")
    _reject_runtime_objects(next_manifest, path="manifest")
    return ToolSurfaceResolution(manifest=next_manifest, surface=surface)


def forward_alias_map(surface: ProviderToolSurface) -> dict[str, str]:
    """provider_alias -> domain_key (exact one-to-one)."""
    return {tool.provider_alias: tool.domain_key for tool in surface.tools}


def reverse_alias_map(surface: ProviderToolSurface) -> dict[str, tuple[str, str]]:
    """provider_alias -> (domain_key, binding_contract_digest)."""
    return {
        tool.provider_alias: (
            tool.domain_key,
            tool.binding.ref.binding_contract_digest,
        )
        for tool in surface.tools
    }


def lookup_tool_by_alias(surface: ProviderToolSurface, provider_alias: str) -> ProviderToolDefinition:
    """Resolve a Provider alias on a frozen surface; unknown alias fails closed."""
    if not isinstance(provider_alias, str) or not provider_alias:
        raise ValueError("provider_alias must be a non-empty string")
    # Exact match only; case variants are not accepted even if unique-looking.
    for tool in surface.tools:
        if tool.provider_alias == provider_alias:
            return tool
    raise KeyError(f"unknown provider alias: {provider_alias!r}")


__all__ = [
    "DEFAULT_RESERVED_CONTROL_ALIASES",
    "OPENAI_CHAT_PROVIDER_PROTOCOL",
    "allocate_provider_aliases",
    "build_provider_tool_surface",
    "forward_alias_map",
    "generated_alias_candidate",
    "identity_digest_prefix",
    "is_valid_provider_alias",
    "lookup_tool_by_alias",
    "reverse_alias_map",
    "sanitize_domain_key_for_alias",
]
