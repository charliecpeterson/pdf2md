"""Token counters used to enforce passage budgets after retrieval context is added.

The built-in counter keeps conversion offline. A Hugging Face tokenizer can be
selected explicitly when passages must match a particular embedding model.
"""

from __future__ import annotations

import re
from typing import Protocol


DEFAULT_TOKENIZER = "lexical"
LEXICAL_TOKENIZER_ID = "pdf2md-unicode-lexical-v1"
_TOKEN = re.compile(r"\w+(?:['’]\w+)*|[^\w\s]", re.UNICODE)


class PassageTokenizer(Protocol):
    id: str
    model_max_tokens: int | None

    def count(self, text: str) -> int: ...


class LexicalTokenizer:
    id = LEXICAL_TOKENIZER_ID
    model_max_tokens = None

    def count(self, text: str) -> int:
        return len(_TOKEN.findall(text))


class HuggingFaceTokenizer:
    def __init__(self, model: str) -> None:
        try:
            from transformers import AutoTokenizer
        except ImportError as exc:
            raise RuntimeError(
                "Hugging Face passage tokenization needs transformers; "
                "install the project dependencies or use passage_tokenizer = 'lexical'"
            ) from exc

        try:
            self._tokenizer = AutoTokenizer.from_pretrained(model)
        except (OSError, ValueError) as exc:
            raise RuntimeError(
                f"could not load passage tokenizer {model!r}; use a local model path, "
                "make the model available in the Hugging Face cache, or use 'lexical'"
            ) from exc
        self.id = f"huggingface:{model}"
        model_max_length = int(self._tokenizer.model_max_length)
        self.model_max_tokens = (
            model_max_length if model_max_length < 1_000_000 else None
        )

    def count(self, text: str) -> int:
        return len(self._tokenizer.encode(
            text,
            add_special_tokens=True,
            verbose=False,
        ))


def load_passage_tokenizer(spec: str = DEFAULT_TOKENIZER) -> PassageTokenizer:
    if spec == DEFAULT_TOKENIZER:
        return LexicalTokenizer()
    if spec.startswith("hf:") and spec[3:]:
        return HuggingFaceTokenizer(spec[3:])
    raise ValueError("passage_tokenizer must be 'lexical' or 'hf:<model-or-local-path>'")
