#!/usr/bin/env python3
"""Validate the immutable helper-image lock used by the release profile.

The checked-in lock is deliberately small and canonical.  Application images
are built by the protected runner and are supplied as immutable references;
only the three reviewed infrastructure helpers belong here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from typing import Any


LOCK_DOMAIN = "mindatlas:release-images-lock:v1"
PLATFORM = "linux/amd64"
EXPECTED = {
    "postgres": (
        "postgres:15",
        "postgres@sha256:0dda651c259bfe50e2bcc28ca23d1fcca772fa90b0210803aa7b97379ccf4e85",
    ),
    "minio": (
        "minio/minio:RELEASE.2023-08-16T20-17-30Z",
        "minio/minio@sha256:a6f318a0b80d344553cee9acd979df480309f22b79390840e3b5c9f753c875d1",
    ),
    "minio-client": (
        "minio/mc:RELEASE.2025-08-13T08-35-41Z",
        "minio/mc@sha256:a7fe349ef4bd8521fb8497f55c6042871b2ae640607cf99d9bede5e9bdf11727",
    ),
}
_DIGEST_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}$")


class ReleaseImageLockError(ValueError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def lock_digest(payload: dict[str, Any]) -> str:
    body = {key: value for key, value in payload.items() if key != "lockDigest"}
    return hashlib.sha256(_canonical({"domain": LOCK_DOMAIN, **body})).hexdigest()


def load_lock(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise ReleaseImageLockError("release_image_lock_invalid_json") from None
    if not isinstance(raw, dict):
        raise ReleaseImageLockError("release_image_lock_invalid_shape")
    if set(raw) != {"schemaVersion", "platform", "images", "lockDigest"}:
        raise ReleaseImageLockError("release_image_lock_fields_invalid")
    if raw["schemaVersion"] != 1 or raw["platform"] != PLATFORM:
        raise ReleaseImageLockError("release_image_lock_version_invalid")
    images = raw["images"]
    if not isinstance(images, list) or len(images) != len(EXPECTED):
        raise ReleaseImageLockError("release_image_lock_roles_invalid")
    observed: dict[str, tuple[str, str]] = {}
    for image in images:
        if not isinstance(image, dict) or set(image) != {"role", "source", "reference"}:
            raise ReleaseImageLockError("release_image_lock_image_invalid")
        role = image.get("role")
        source = image.get("source")
        reference = image.get("reference")
        if not isinstance(role, str) or role in observed or role not in EXPECTED:
            raise ReleaseImageLockError("release_image_lock_role_invalid")
        if not isinstance(source, str) or not isinstance(reference, str):
            raise ReleaseImageLockError("release_image_lock_reference_invalid")
        if not _DIGEST_RE.fullmatch(reference) or ":latest" in source.lower():
            raise ReleaseImageLockError("release_image_lock_reference_not_immutable")
        if (source, reference) != EXPECTED[role]:
            raise ReleaseImageLockError("release_image_lock_reference_not_reviewed")
        observed[role] = (source, reference)
    if set(observed) != set(EXPECTED):
        raise ReleaseImageLockError("release_image_lock_roles_incomplete")
    digest = raw.get("lockDigest")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ReleaseImageLockError("release_image_lock_digest_invalid")
    if digest != lock_digest(raw):
        raise ReleaseImageLockError("release_image_lock_digest_mismatch")
    return raw


def _write_lock(path: Path) -> None:
    images = [
        {"role": role, "source": source, "reference": reference}
        for role, (source, reference) in EXPECTED.items()
    ]
    payload: dict[str, Any] = {
        "schemaVersion": 1,
        "platform": PLATFORM,
        "images": images,
    }
    payload["lockDigest"] = lock_digest(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical(payload) + b"\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate the release helper-image lock")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    parser.add_argument(
        "--lock",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "deploy" / "release-images.lock",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.write:
            _write_lock(args.lock)
        payload = load_lock(args.lock)
    except ReleaseImageLockError as exc:
        print(str(exc))
        return 2
    print(json.dumps({"platform": payload["platform"], "lockDigest": payload["lockDigest"], "roles": sorted(item["role"] for item in payload["images"])}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
