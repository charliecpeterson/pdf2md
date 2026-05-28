from __future__ import annotations

import os
from dataclasses import dataclass


DEFAULT_RERANKER = os.environ.get("DOCSMCP_RERANKER", "BAAI/bge-reranker-v2-m3")


@dataclass
class RerankHit:
    idx: int
    score: float


class CrossEncoderReranker:
    _instance: "CrossEncoderReranker | None" = None

    def __init__(self, model_name: str = DEFAULT_RERANKER):
        from sentence_transformers import CrossEncoder

        self.model_name = model_name
        self._ce = CrossEncoder(model_name)

    def rerank(self, query: str, passages: list[str], top_k: int | None = None) -> list[RerankHit]:
        if not passages:
            return []
        pairs = [(query, p) for p in passages]
        scores = self._ce.predict(pairs, show_progress_bar=False)
        ranked = sorted(
            (RerankHit(idx=i, score=float(s)) for i, s in enumerate(scores)),
            key=lambda h: h.score,
            reverse=True,
        )
        return ranked[:top_k] if top_k else ranked


_DEFAULT: CrossEncoderReranker | None = None


def get_default() -> CrossEncoderReranker:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = CrossEncoderReranker()
    return _DEFAULT
