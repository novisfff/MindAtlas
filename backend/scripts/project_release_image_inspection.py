#!/usr/bin/env python3
"""Project Docker image inspection into a release-safe, label-only document."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any


_SAFE_LABELS = frozenset(
    {
        "org.opencontainers.image.revision",
        "io.mindatlas.platform",
        "io.mindatlas.api-worker-lock-sha256",
        "io.mindatlas.parse-worker-lock-sha256",
        "io.mindatlas.dependency-lock-set-sha256",
        "io.mindatlas.frontend-build-content-sha256",
    }
)


class InspectionProjectionError(ValueError):
    safe_code = "release_image_inspection_invalid"


def project(raw: Any, *, build_revision: str) -> list[dict[str, Any]]:
    if not isinstance(build_revision, str) or re.fullmatch(r"[0-9a-f]{40}", build_revision) is None:
        raise InspectionProjectionError("release_source_revision_invalid")
    expected_tags = {
        f"mindatlas-release-backend:{build_revision}",
        f"mindatlas-release-scripted-provider:{build_revision}",
        f"mindatlas-release-web:{build_revision}",
    }
    if not isinstance(raw, list) or len(raw) != len(expected_tags):
        raise InspectionProjectionError("release_image_inspection_invalid")
    projected: list[dict[str, Any]] = []
    seen_tags: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise InspectionProjectionError("release_image_inspection_invalid")
        image_id = item.get("Id")
        if not isinstance(image_id, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None:
            raise InspectionProjectionError("release_image_identity_id_missing")
        tags = item.get("RepoTags")
        if (
            not isinstance(tags, list)
            or len(tags) != 1
            or not isinstance(tags[0], str)
            or tags[0] not in expected_tags
        ):
            raise InspectionProjectionError("release_image_inspection_invalid")
        tag = tags[0]
        if tag in seen_tags:
            raise InspectionProjectionError("release_image_inspection_invalid")
        seen_tags.add(tag)
        config = item.get("Config")
        labels = config.get("Labels", {}) if isinstance(config, dict) else None
        if not isinstance(labels, dict):
            raise InspectionProjectionError("release_image_inspection_invalid")
        safe_labels = {
            key: value
            for key, value in labels.items()
            if key in _SAFE_LABELS and isinstance(value, str)
        }
        projected.append(
            {
                "Id": image_id,
                "imageDigest": image_id,
                "RepoTags": tags,
                "Config": {"Labels": safe_labels},
            }
        )
    if seen_tags != expected_tags:
        raise InspectionProjectionError("release_image_inspection_invalid")
    return projected


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise InspectionProjectionError("release_image_inspection_invalid") from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--build-revision", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        projected = project(_read_json(args.input), build_revision=args.build_revision)
        if args.output.exists() or args.output.is_symlink():
            raise InspectionProjectionError("release_image_inspection_output_collision")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(projected, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        args.output.chmod(0o600)
    except InspectionProjectionError as exc:
        print(exc.safe_code)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
