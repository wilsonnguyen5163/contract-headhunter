"""Word-overlap similarity (TF-IDF cosine) between postings and profiles.

No skill list and no aliases: every word counts, and words that are rare across all postings
and profiles count more. Accent- and case-insensitive, so English and French work alike.
Chosen over the taxonomy skill overlap on 2026-11-02 (doc/PROTOTYPES.md): it ranked fits
better on every measure and needs no upkeep. The skill list stays for explanations.

Word rarity is counted over the jobs and contractors being matched, so it shifts when jobs
are imported; `fingerprint` changes with it and goes into the scoring version.
"""

import hashlib
import math
import re
from collections import Counter
from collections.abc import Iterable, Sequence

import numpy as np

from jobmatch.normalize.phrases import fold
from jobmatch.schemas import ContractorProfile, Job

_TOKEN = re.compile(r"[a-z0-9+#][a-z0-9+#.]+")


def job_lexical_text(job: Job) -> str:
    """The posting as written (title and raw text): no sections, no cleanup."""
    return f"{job.title_raw}\n{job.description_raw or job.description_clean or ''}"


def contractor_lexical_text(profile: ContractorProfile) -> str:
    parts = [
        profile.standardized_headline or "",
        *profile.target_titles,
        profile.executive_summary or "",
        "; ".join(profile.core_strengths),
        "; ".join(profile.search_keywords),
        "; ".join(profile.proof_points),
        "; ".join(profile.main_industry),
    ]
    return "\n".join(p for p in parts if p)


class LexicalIndex:
    def __init__(self, documents: Iterable[str], stopwords: Iterable[str] = ()) -> None:
        self._stop = {fold(w) for w in stopwords}
        tokenized = [self.tokens(d) for d in documents]
        self._df = Counter(t for d in tokenized for t in set(d))
        self._n = len(tokenized)
        digest = hashlib.sha256()
        for term, count in sorted(self._df.items()):
            digest.update(f"{term}\x00{count}\x00".encode())
        digest.update(f"{self._n}\x00{sorted(self._stop)}".encode())
        self.fingerprint = digest.hexdigest()[:16]

    def tokens(self, text: str) -> list[str]:
        words = (t.rstrip(".") for t in _TOKEN.findall(fold(text)))
        return [w for w in words if len(w) > 1 and w not in self._stop]

    def vector(self, text: str) -> dict[str, float]:
        """Unit-length TF-IDF weights: (1 + log tf) * log((1 + n) / (1 + df))."""
        tf = Counter(self.tokens(text))
        weights = {
            t: (1 + math.log(c)) * math.log((1 + self._n) / (1 + self._df.get(t, 0)))
            for t, c in tf.items()
        }
        norm = math.sqrt(sum(w * w for w in weights.values())) or 1.0
        return {t: w / norm for t, w in weights.items()}

    def similarity(self, job_texts: Sequence[str], contractor_texts: Sequence[str]) -> np.ndarray:
        """Cosine of every job (rows) with every contractor (columns), 0..1."""
        jobs = [self.vector(t) for t in job_texts]
        contractors = [self.vector(t) for t in contractor_texts]
        out = np.zeros((len(jobs), len(contractors)), dtype=np.float32)
        for i, job in enumerate(jobs):
            for j, con in enumerate(contractors):
                small, big = (job, con) if len(job) <= len(con) else (con, job)
                out[i, j] = sum(w * big.get(t, 0.0) for t, w in small.items())
        return out
