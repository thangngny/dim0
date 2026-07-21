"""Embedding classes.

Provides:
  - OpenAIEmbedder: uses an OpenAI-compatible /v1/embeddings endpoint
  - OllamaEmbedder: uses the native Ollama /api/embed endpoint (no API key needed)
"""

import asyncio
import logging
import os

import httpx
from openai import AsyncOpenAI

from topix.config import catalog
from topix.nlp.tokens import truncate_to_tokens
from topix.utils.timeit import async_timeit

logger = logging.getLogger(__name__)

MODEL_NAME = "text-embedding-3-small"
DIMENSIONS = 512

# Hard per-input limit for `text-embedding-3-small` is 8191 tokens; cap a touch
# below it so any counting drift still lands inside the limit. Oversized inputs
# (long sheets, mini-app JSX, non-English notes) are truncated rather than
# allowed to 400 the whole embed request.
MAX_EMBED_TOKENS = 8000


class OpenAIEmbedder:
    """Embeds text via an OpenAI-compatible endpoint (OpenAI or OpenRouter).

    The concrete provider, model string, and vector size are resolved from the
    model catalog based on which API keys are present.
    """

    def __init__(
        self,
        client: AsyncOpenAI,
        model: str = MODEL_NAME,
        dimensions: int = DIMENSIONS,
    ):
        """Initialize the embedder with a provider client, model, and vector size."""
        self._client = client
        self.model = model
        self.dimensions = dimensions

    @classmethod
    def from_config(cls):
        """Create an embedder routed to the first available embedding provider."""
        resolved = catalog.available_embedding()
        if resolved is None:
            raise RuntimeError(
                "No embedding model available. Set OPENAI_API_KEY or OPENROUTER_API_KEY."
            )

        client = catalog.openai_compatible_client(resolved)
        if client is None:
            raise RuntimeError(f"Unsupported embedding provider: {resolved.provider}")

        return cls(
            client=client,
            model=resolved.model,
            dimensions=resolved.dim or DIMENSIONS,
        )

    def _fit(self, text: str) -> str:
        """Clamp one text to the embedding model's per-input token limit.

        Logs a warning when truncation actually happens so we have visibility
        into how often oversized content is being clipped (and on what).
        """
        fitted = truncate_to_tokens(text, MAX_EMBED_TOKENS)
        if len(fitted) != len(text):
            logger.warning(
                "Truncated embedding input to %d tokens (was %d chars, now %d chars)",
                MAX_EMBED_TOKENS, len(text), len(fitted),
            )
        return fitted

    def _fit_batch(self, texts: list[str]) -> list[str]:
        """Clamp every text in a batch to the token limit, mapping empties to "$"."""
        return [self._fit(text) if text else "$" for text in texts]

    @async_timeit
    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        # Clamping only runs a (synchronous, GIL-holding) BPE encode for text
        # long enough to possibly overflow — see the fast-path threshold in
        # truncate_to_tokens. tiktoken releases the GIL during encode, so when a
        # batch actually contains such text we offload the whole fit to a thread
        # to keep a large bulk batch off the event loop. All-short batches (e.g.
        # chat turns) skip the hop and fit inline for free. Mis-estimating the
        # threshold only costs a needless hop or inline encode, never correctness.
        needs_encode = any(t and len(t) > MAX_EMBED_TOKENS // 4 for t in texts)
        texts = await asyncio.to_thread(self._fit_batch, texts) if needs_encode else self._fit_batch(texts)
        # Call the embeddings endpoint asynchronously
        response = await self._client.embeddings.create(
            model=self.model,
            input=texts,
            dimensions=self.dimensions
        )
        # The embeddings are in response.data, ordered same as input
        return [e.embedding for e in response.data]

    async def embed(
        self,
        texts: list[str],
        batch_size: int = 1000
    ) -> list[list[float]]:
        """Embed a list of texts using OpenAI embeddings."""
        if not texts:
            return []

        tasks = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            task = asyncio.create_task(self._embed_batch(batch))
            tasks.append(task)
        results = await asyncio.gather(*tasks)

        # Flatten the list of lists into a single list
        embeddings = [embedding for batch in results for embedding in batch]

        return embeddings


class OllamaEmbedder:
    """Embeds text via the native Ollama /api/embed endpoint.

    Requires no API key. Configured via:
      OLLAMA_BASE_URL  (default: http://localhost:11434)
      OLLAMA_EMBED_MODEL  (default: nomic-embed-text)

    The model must be pulled locally: `ollama pull nomic-embed-text`
    nomic-embed-text produces 768-dimensional vectors.
    """

    DEFAULT_BASE_URL = "http://localhost:11434"
    DEFAULT_MODEL = "nomic-embed-text"
    DEFAULT_DIMENSIONS = 768
    TIMEOUT = 60.0
    MAX_RETRIES = 3

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        dimensions: int | None = None,
    ):
        """Initialise with base_url, model, and expected vector dimensions."""
        self.base_url = (base_url or os.getenv("OLLAMA_BASE_URL", self.DEFAULT_BASE_URL)).rstrip("/")
        self.model = model or os.getenv("OLLAMA_EMBED_MODEL", self.DEFAULT_MODEL)
        self.dimensions = dimensions or self.DEFAULT_DIMENSIONS
        self._client = httpx.AsyncClient(timeout=self.TIMEOUT)

    @classmethod
    def from_config(cls) -> "OllamaEmbedder":
        """Create an OllamaEmbedder from the model catalog."""
        resolved = catalog.available_embedding()
        if resolved is None or resolved.provider != "ollama":
            # Fallback to defaults from env
            return cls()
        return cls(
            model=resolved.model,
            dimensions=resolved.dim or cls.DEFAULT_DIMENSIONS,
        )

    async def health_check(self) -> bool:
        """Return True if the Ollama embed endpoint is reachable."""
        try:
            payload = {"model": self.model, "input": ["health check"]}
            resp = await self._client.post(
                f"{self.base_url}/api/embed",
                json=payload,
            )
            if resp.status_code == 200:
                data = resp.json()
                embs = data.get("embeddings", [])
                if embs and len(embs[0]) == self.dimensions:
                    return True
                logger.warning(
                    "OllamaEmbedder health: unexpected dim got=%d want=%d",
                    len(embs[0]) if embs else 0, self.dimensions,
                )
            else:
                logger.warning("OllamaEmbedder health: HTTP %d", resp.status_code)
        except Exception as exc:
            logger.warning("OllamaEmbedder health error: %s", exc)
        return False

    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Call Ollama /api/embed for one batch, returning a list of vectors."""
        # Replace empty strings with a neutral sentinel so Ollama doesn't error
        safe_texts = [t if t.strip() else "." for t in texts]
        payload = {"model": self.model, "input": safe_texts}
        last_exc: Exception | None = None
        for attempt in range(self.MAX_RETRIES):
            try:
                resp = await self._client.post(
                    f"{self.base_url}/api/embed",
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                embeddings: list[list[float]] = data.get("embeddings", [])
                if len(embeddings) != len(texts):
                    raise ValueError(
                        f"Ollama returned {len(embeddings)} embeddings for {len(texts)} inputs"
                    )
                # Validate dimension on first vector
                if embeddings and len(embeddings[0]) != self.dimensions:
                    actual_dim = len(embeddings[0])
                    logger.warning(
                        "OllamaEmbedder: dimension mismatch — got %d, expected %d. "
                        "Update OLLAMA_EMBED_DIMENSIONS or recreate the Qdrant collection.",
                        actual_dim, self.dimensions,
                    )
                    # Update so the rest of the run is consistent
                    self.dimensions = actual_dim
                return embeddings
            except Exception as exc:
                last_exc = exc
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(0.5 * (attempt + 1))
        raise RuntimeError(
            f"OllamaEmbedder failed after {self.MAX_RETRIES} attempts: {last_exc}"
        )

    async def embed(
        self,
        texts: list[str],
        batch_size: int = 100,
    ) -> list[list[float]]:
        """Embed a list of texts using the local Ollama embedding model."""
        if not texts:
            return []

        tasks = [
            asyncio.create_task(self._embed_batch(texts[i:i + batch_size]))
            for i in range(0, len(texts), batch_size)
        ]
        results = await asyncio.gather(*tasks)
        return [emb for batch in results for emb in batch]

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
