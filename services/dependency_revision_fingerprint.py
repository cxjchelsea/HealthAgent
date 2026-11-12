"""Stable, non-secret identity of one Host-approved revision candidate.

Canonical JSON prevents differences in irrelevant key ordering or whitespace.
No raw model prompt or user text is included in audit records, only SHA-256.
"""
import hashlib
import json


def revision_fingerprint(candidate: dict) -> str:
    if type(candidate) is not dict:
        raise ValueError("dependency_revision_invalid_candidate")
    canonical = json.dumps(
        candidate, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
