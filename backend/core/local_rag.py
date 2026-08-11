"""Free local RAG backed by ChromaDB and Ollama embeddings.

Both dependencies are optional at import time so the main API can still start
before Chroma is installed or Ollama is running.  Knowledge and semantic memory
share one collection and one embedding model, avoiding cross-model dimensions.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from .knowledge import EMBED_BATCH, SKIP_DIRS, chunk_text

logger = logging.getLogger("infinity.local_rag")


class LocalRAGUnavailable(RuntimeError):
    pass


class OllamaEmbedder:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        model: str = "embeddinggemma",
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = float(timeout)
        if self.base_url not in ("http://127.0.0.1:11434", "http://localhost:11434"):
            raise ValueError("Ollama embeddings must use the local Ollama service.")

    def _request(self, path: str, payload: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST" if data is not None else "GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise LocalRAGUnavailable(f"Ollama unavailable: {exc}") from exc

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        body = self._request(
            "/api/embed",
            {"model": self.model, "input": list(texts), "truncate": True},
        )
        vectors = body.get("embeddings")
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise LocalRAGUnavailable("Ollama returned an invalid embedding batch.")
        return [[float(value) for value in vector] for vector in vectors]

    def status(self) -> dict[str, Any]:
        try:
            body = self._request("/api/tags")
            names = {
                str(item.get("name") or item.get("model") or "").split(":")[0]
                for item in body.get("models", [])
                if isinstance(item, Mapping)
            }
            return {
                "ollama": True,
                "model": self.model,
                "model_installed": self.model.split(":")[0] in names,
            }
        except LocalRAGUnavailable as exc:
            return {
                "ollama": False,
                "model": self.model,
                "model_installed": False,
                "reason": str(exc),
            }


class HFEmbedder:
    """HuggingFace Inference API embeddings — free tier, no local GPU needed.

    Uses the serverless feature-extraction pipeline. Requires an HF_TOKEN or
    HUGGINGFACE_HUB_TOKEN environment variable. Falls back gracefully when
    the API is unavailable or rate-limited.
    """

    DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
    API_BASE = "https://api-inference.huggingface.co/pipeline/feature-extraction"

    def __init__(
        self,
        model: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.model = model or str(
            __import__("os").environ.get("INFINITY_HF_EMBED_MODEL") or self.DEFAULT_MODEL
        )
        self.timeout = float(timeout)

    @staticmethod
    def _token() -> str:
        import os
        return (
            os.environ.get("HF_TOKEN")
            or os.environ.get("HUGGINGFACE_HUB_TOKEN")
            or ""
        ).strip()

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        token = self._token()
        if not token:
            raise LocalRAGUnavailable("No HF_TOKEN or HUGGINGFACE_HUB_TOKEN set.")
        if not texts:
            return []
        payload = json.dumps(
            {"inputs": list(texts), "options": {"wait_for_model": True}}
        ).encode("utf-8")
        url = f"{self.API_BASE}/{self.model}"
        request = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise LocalRAGUnavailable("HF token rejected (invalid or expired).") from exc
            if exc.code == 503:
                raise LocalRAGUnavailable("HF model is loading — try again shortly.") from exc
            raise LocalRAGUnavailable(f"HF Inference API returned HTTP {exc.code}.") from exc
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise LocalRAGUnavailable(f"HF Inference API unavailable: {exc}") from exc
        # HF returns list-of-lists for batch, or a single list for one input.
        if isinstance(body, list) and body and isinstance(body[0], list):
            if isinstance(body[0][0], list):
                # Nested: [[vec_for_input1], [vec_for_input2]] — unwrap.
                vectors = [item[0] if isinstance(item, list) and item else item for item in body]
            else:
                vectors = body
        elif isinstance(body, list) and body and isinstance(body[0], float):
            vectors = [body]  # single-input response
        else:
            raise LocalRAGUnavailable(f"HF returned unexpected shape: {type(body)}")
        if len(vectors) != len(texts):
            raise LocalRAGUnavailable(
                f"HF returned {len(vectors)} vectors for {len(texts)} inputs."
            )
        return [[float(v) for v in vec] for vec in vectors]

    def status(self) -> dict[str, Any]:
        token = self._token()
        return {
            "hf": bool(token),
            "model": self.model,
            "provider": "huggingface-inference",
        }


def make_embedder() -> Any:
    """Return the best available embedder: HF > Ollama.

    Both are optional — if neither is available, raises LocalRAGUnavailable
    at embed time, not at construction time.
    """
    # HF first (free cloud, no GPU needed)
    if HFEmbedder._token():
        try:
            hf = HFEmbedder()
            logger.info("Using HF embeddings: %s", hf.model)
            return hf
        except Exception:
            pass
    # Ollama fallback (local GPU)
    try:
        ollama = OllamaEmbedder(
            model=str(
                __import__("os").environ.get("INFINITY_OLLAMA_EMBED_MODEL")
                or "embeddinggemma"
            )
        )
        logger.info("Using Ollama embeddings: %s", ollama.model)
        return ollama
    except Exception:
        pass
    # Return Ollama as last resort — it'll raise LocalRAGUnavailable at embed time
    return OllamaEmbedder()


class LocalRAG:
    def __init__(
        self,
        persist_path: Path,
        *,
        client: Optional[Any] = None,
        embedder: Optional[Any] = None,
    ) -> None:
        self.persist_path = Path(persist_path)
        self.persist_path.mkdir(parents=True, exist_ok=True)
        self._client = client
        self.embedder = embedder or make_embedder()
        safe_model = re.sub(r"[^a-z0-9]+", "_", str(self.embedder.model).lower()).strip("_")
        digest = hashlib.sha1(str(self.embedder.model).encode("utf-8")).hexdigest()[:8]
        self.collection_name = f"infinity_local_{safe_model[:30]}_{digest}"
        self._collection: Optional[Any] = None

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if importlib.util.find_spec("chromadb") is None:
            raise LocalRAGUnavailable("ChromaDB is not installed.")
        import chromadb  # type: ignore[import-not-found]

        self._client = chromadb.PersistentClient(path=str(self.persist_path))
        return self._client

    def _get_collection(self) -> Any:
        if self._collection is None:
            self._collection = self._get_client().get_or_create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine", "embedding_model": str(self.embedder.model)},
            )
        return self._collection

    @staticmethod
    def _knowledge_id(source: str, relpath: str, ordinal: int, text: str) -> str:
        value = f"{source}\0{relpath}\0{ordinal}\0{text}".encode("utf-8", errors="replace")
        return "k:" + hashlib.sha256(value).hexdigest()

    @staticmethod
    def _iter_text_files(root: Path) -> Iterable[tuple[Path, str]]:
        if root.is_file():
            yield root, root.name
            return
        candidates = list(root.rglob("*.md")) + list(root.rglob("*.txt"))
        for path in candidates:
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            yield path, path.relative_to(root).as_posix()

    def reindex(self, sources: Sequence[Mapping[str, Any]]) -> dict[str, int]:
        collection = self._get_collection()
        records: list[tuple[str, str, dict[str, Any]]] = []
        stats = {"files_indexed": 0, "chunks_indexed": 0, "chunks_removed": 0, "errors": 0}
        for source in sources:
            if not bool(source.get("enabled", True)) or str(source.get("kind")) == "downloads":
                continue
            root = Path(str(source.get("path") or ""))
            if not root.exists():
                continue
            for path, relpath in self._iter_text_files(root):
                try:
                    if path.stat().st_size > 2 * 1024 * 1024:
                        continue
                    pieces = chunk_text(path.read_text(encoding="utf-8", errors="replace"))
                    for ordinal, text in enumerate(pieces):
                        records.append(
                            (
                                self._knowledge_id(str(root), relpath, ordinal, text),
                                text,
                                {
                                    "kind": "knowledge",
                                    "source_path": str(root),
                                    "relpath": relpath,
                                    "ordinal": ordinal,
                                },
                            )
                        )
                    stats["files_indexed"] += 1
                except OSError as exc:
                    logger.warning("Local RAG skipped %s: %s", path, exc)
                    stats["errors"] += 1

        for start in range(0, len(records), EMBED_BATCH):
            batch = records[start : start + EMBED_BATCH]
            documents = [record[1] for record in batch]
            collection.upsert(
                ids=[record[0] for record in batch],
                embeddings=self.embedder.embed(documents),
                documents=documents,
                metadatas=[record[2] for record in batch],
            )
        stats["chunks_indexed"] = len(records)

        current_ids = {record[0] for record in records}
        existing = collection.get(include=[]).get("ids", [])
        stale = [item_id for item_id in existing if str(item_id).startswith("k:") and item_id not in current_ids]
        if stale:
            collection.delete(ids=stale)
        stats["chunks_removed"] = len(stale)
        return stats

    def sync_memories(self, memories: Sequence[Mapping[str, Any]]) -> dict[str, int]:
        collection = self._get_collection()
        records = [
            ("m:" + str(item["id"]), str(item.get("text") or ""), item)
            for item in memories
            if item.get("id") and str(item.get("text") or "").strip()
        ]
        for start in range(0, len(records), EMBED_BATCH):
            batch = records[start : start + EMBED_BATCH]
            documents = [item[1] for item in batch]
            collection.upsert(
                ids=[item[0] for item in batch],
                embeddings=self.embedder.embed(documents),
                documents=documents,
                metadatas=[{"kind": "memory", "memory_id": str(item[2]["id"])} for item in batch],
            )
        current_ids = {item[0] for item in records}
        existing = collection.get(include=[]).get("ids", [])
        stale = [item_id for item_id in existing if str(item_id).startswith("m:") and item_id not in current_ids]
        if stale:
            collection.delete(ids=stale)
        return {"memories_indexed": len(records), "memories_removed": len(stale)}

    def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        query = str(query or "").strip()
        if not query:
            return []
        collection = self._get_collection()
        if int(collection.count()) == 0:
            return []
        result = collection.query(
            query_embeddings=self.embedder.embed([query]),
            n_results=max(1, min(int(top_k), int(collection.count()))),
            include=["documents", "metadatas", "distances"],
        )
        ids = (result.get("ids") or [[]])[0]
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        hits: list[dict[str, Any]] = []
        for index, item_id in enumerate(ids):
            metadata = dict(metadatas[index] or {})
            distance = float(distances[index]) if distances[index] is not None else 1.0
            hits.append(
                {
                    "id": item_id,
                    "text": str(documents[index] or ""),
                    "score": round(max(-1.0, min(1.0, 1.0 - distance)), 4),
                    **metadata,
                }
            )
        return hits

    def status(self) -> dict[str, Any]:
        embed_status = self.embedder.status()
        package_available = self._client is not None or importlib.util.find_spec("chromadb") is not None
        vectors = 0
        reason = None
        if package_available:
            try:
                vectors = int(self._get_collection().count())
            except Exception as exc:  # noqa: BLE001 - status must stay nonfatal
                reason = str(exc)
        available = bool(package_available and embed_status.get("ollama") and embed_status.get("model_installed"))
        return {
            "available": available,
            "chromadb": package_available,
            "vectors": vectors,
            "path": str(self.persist_path),
            **embed_status,
            **({"reason": reason} if reason else {}),
        }


__all__ = ["LocalRAG", "LocalRAGUnavailable", "OllamaEmbedder", "HFEmbedder", "make_embedder"]
