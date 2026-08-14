#!/usr/bin/env python3
"""Offline verification of a sealed release evidence object."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys
import tarfile
from typing import Any

from app.release.evidence import ReleaseEvidenceIntegrityError, verify_evidence_object
from app.release.trust import ReleaseEvidenceTrustError, load_trust_set
from app.schema.canonical import canonical_json_bytes
from app.assistant.domain.digests import sha256_bytes


def _safe_bundle(bundle: Path, manifest: Any) -> None:
    expected = {item.sha256_digest for item in manifest.artifact_refs}
    seen: set[str] = set()
    try:
        with tarfile.open(bundle, mode="r") as archive:
            for member in archive.getmembers():
                name = member.name
                if member.isdir() or not re.fullmatch(r"artifacts/[0-9a-f]{64}", name):
                    raise ValueError("artifact bundle contains unsafe member")
                digest = name.split("/", 1)[1]
                if digest in seen:
                    raise ValueError("artifact bundle contains duplicate member")
                seen.add(digest)
                handle = archive.extractfile(member)
                if handle is None:
                    raise ValueError("artifact bundle member is unreadable")
                content = handle.read()
                if sha256_bytes(content) != digest:
                    raise ValueError("artifact bundle digest mismatch")
                ref = next((item for item in manifest.artifact_refs if item.sha256_digest == digest), None)
                if ref is None or len(content) != ref.byte_size:
                    raise ValueError("artifact bundle contains an unexpected artifact")
    except (OSError, tarfile.TarError) as exc:
        raise ValueError("artifact bundle is invalid") from exc
    if seen != expected:
        raise ValueError("artifact bundle is incomplete")


def verify(evidence_path: Path, bundle_path: Path, trust_path: Path) -> tuple[dict[str, Any], int]:
    raw = evidence_path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    if canonical_json_bytes(payload) != raw:
        raise ValueError("evidence object is not canonical JSON")
    trust = load_trust_set(trust_path)
    manifest, attestation = verify_evidence_object(payload, trust)
    _safe_bundle(bundle_path, manifest)
    results = list(manifest.assertion_results)
    passed = sum(1 for item in results if item.passed)
    failed = len(results) - passed
    summary = {
        "evidenceKind": manifest.evidence_kind,
        "releaseRunId": str(manifest.release_run_id),
        "manifestDigest": manifest.manifest_digest,
        "keyId": attestation["keyId"] if isinstance(attestation, dict) and "keyId" in attestation else getattr(attestation, "key_id", ""),
        "passedAssertions": passed,
        "failedAssertions": failed,
        "buildRevision": manifest.build_revision,
        "schemaRevision": manifest.schema_revision,
        "dependencyLockSetDigest": manifest.dependency_lock_set_digest,
        "scenarioSetDigest": manifest.scenario_set_digest,
        "startedAt": manifest.started_at.isoformat().replace("+00:00", "Z"),
        "endedAt": manifest.ended_at.isoformat().replace("+00:00", "Z"),
    }
    return summary, (0 if failed == 0 else 3)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify MindAtlas release evidence offline")
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--artifact-bundle", type=Path, required=True)
    parser.add_argument("--trust-set", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        summary, code = verify(
            evidence_path=args.evidence,
            bundle_path=args.artifact_bundle,
            trust_path=args.trust_set,
        )
    except (ValueError, OSError, ReleaseEvidenceIntegrityError, ReleaseEvidenceTrustError) as exc:
        print(json.dumps({"error": getattr(exc, "safe_code", "release_attestation_invalid")}, sort_keys=True))
        return 2
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
