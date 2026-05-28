from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

import numpy as np


@dataclass(frozen=True)
class EmbedModel:
    name: str
    repo_id: str
    dim: int
    query_prefix: str = ""
    passage_prefix: str = ""


MODELS: dict[str, EmbedModel] = {
    "mxbai-large": EmbedModel(
        name="mxbai-large",
        repo_id="mixedbread-ai/mxbai-embed-large-v1",
        dim=1024,
        query_prefix=(
            "Represent this sentence for searching relevant passages: "
        ),
        passage_prefix="",
    ),
    "bge-small": EmbedModel(
        name="bge-small",
        repo_id="BAAI/bge-small-en-v1.5",
        dim=384,
        query_prefix="Represent this sentence for searching relevant passages: ",
        passage_prefix="",
    ),
    "bge-large": EmbedModel(
        name="bge-large",
        repo_id="BAAI/bge-large-en-v1.5",
        dim=1024,
        query_prefix="Represent this sentence for searching relevant passages: ",
        passage_prefix="",
    ),
}

DEFAULT_MODEL = os.environ.get("DOCSMCP_EMBED_MODEL", "mxbai-large")


class Embedder(Protocol):
    model: EmbedModel

    def embed_passages(self, texts: list[str]) -> np.ndarray: ...
    def embed_query(self, text: str) -> np.ndarray: ...


class SentenceTransformerEmbedder:
    _instance: "SentenceTransformerEmbedder | None" = None

    def __init__(self, model_name: str = DEFAULT_MODEL):
        if model_name not in MODELS:
            raise ValueError(f"Unknown embed model {model_name!r}. Known: {list(MODELS)}")
        self.model = MODELS[model_name]
        from sentence_transformers import SentenceTransformer

        self._st = SentenceTransformer(self.model.repo_id)

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.model.dim), dtype=np.float32)
        prefixed = [self.model.passage_prefix + t for t in texts] if self.model.passage_prefix else texts
        v = self._st.encode(
            prefixed,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
            batch_size=32,
        )
        return v.astype(np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        prefixed = self.model.query_prefix + text if self.model.query_prefix else text
        v = self._st.encode(
            prefixed,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return v.astype(np.float32)


_DEFAULT: Embedder | None = None


def get_default() -> Embedder:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = SentenceTransformerEmbedder(DEFAULT_MODEL)
    return _DEFAULT


def vector_to_bytes(v: np.ndarray) -> bytes:
    return np.ascontiguousarray(v, dtype=np.float32).tobytes()
