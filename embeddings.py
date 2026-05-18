"""Lightweight local embeddings via fastembed (ONNX runtime, no PyTorch).

Model: BAAI/bge-small-en-v1.5
  - 384 dimensions
  - ~45 MB download (cached to /root/.cache/fastembed inside Docker)
  - ~10 ms inference on CPU

Note: Groq does not offer an embedding API. fastembed runs entirely locally
and requires no additional API key.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastembed import TextEmbedding

DIMS = 384
_MODEL_NAME = "BAAI/bge-small-en-v1.5"
_model: "TextEmbedding | None" = None


def _get_model() -> "TextEmbedding":
    global _model
    if _model is None:
        from fastembed import TextEmbedding
        _model = TextEmbedding(model_name=_MODEL_NAME)
    return _model


def embed(text: str) -> list[float]:
    """Return a 384-dimensional embedding vector for a single text."""
    model = _get_model()
    return next(model.embed([text])).tolist()


def embed_batch(texts: list[str]) -> list[list[float]]:
    """Return embeddings for a list of texts (faster than calling embed() N times)."""
    model = _get_model()
    return [v.tolist() for v in model.embed(texts)]
