from __future__ import annotations

from uuid import UUID


def parse_uuid_csv(value: str | None) -> list[UUID]:
    if value is None:
        return []
    raw = value.strip()
    if not raw:
        return []
    items: list[UUID] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        items.append(UUID(part))
    return items

