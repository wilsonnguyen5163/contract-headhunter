"""Stable fingerprint of (company, title, city) used to spot the same job across sources."""

import hashlib

from jobmatch.normalize.phrases import fold


def job_fingerprint(company_norm: str | None, title_norm: str | None, city: str | None) -> str:
    parts = (" ".join(fold(p or "").split()) for p in (company_norm, title_norm, city))
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
