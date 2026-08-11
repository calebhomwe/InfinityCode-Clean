from __future__ import annotations

from backend.core.local_rag import LocalRAG


class _FakeEmbedder:
    model = "test-embed"

    def embed(self, texts):
        return [[float(len(text)), 1.0] for text in texts]

    def status(self):
        return {"ollama": True, "model": self.model, "model_installed": True}


class _FakeCollection:
    def __init__(self):
        self.rows = {}

    def count(self):
        return len(self.rows)

    def get(self, include=None):
        return {"ids": list(self.rows)}

    def upsert(self, *, ids, embeddings, documents, metadatas):
        for i, item_id in enumerate(ids):
            self.rows[item_id] = (documents[i], metadatas[i], embeddings[i])

    def delete(self, *, ids):
        for item_id in ids:
            self.rows.pop(item_id, None)

    def query(self, *, query_embeddings, n_results, include):
        rows = list(self.rows.items())[:n_results]
        return {
            "ids": [[item_id for item_id, _ in rows]],
            "documents": [[value[0] for _, value in rows]],
            "metadatas": [[value[1] for _, value in rows]],
            "distances": [[0.1 for _ in rows]],
        }


class _FakeClient:
    def __init__(self):
        self.collection = _FakeCollection()

    def get_or_create_collection(self, **kwargs):
        return self.collection


def test_local_rag_indexes_enabled_sources_and_searches_them(tmp_path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "notes.md").write_text("A durable local knowledge note.", encoding="utf-8")
    client = _FakeClient()
    rag = LocalRAG(tmp_path / "chroma", client=client, embedder=_FakeEmbedder())

    stats = rag.reindex([{"path": str(vault), "enabled": 1, "kind": "vault"}])
    hits = rag.search("durable", top_k=3)

    assert stats["files_indexed"] == 1
    assert stats["chunks_indexed"] == 1
    assert hits[0]["relpath"] == "notes.md"
    assert hits[0]["score"] == 0.9


def test_local_rag_removes_stale_chunks_on_reindex(tmp_path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "notes.md"
    note.write_text("Temporary note", encoding="utf-8")
    client = _FakeClient()
    rag = LocalRAG(tmp_path / "chroma", client=client, embedder=_FakeEmbedder())
    sources = [{"path": str(vault), "enabled": 1, "kind": "vault"}]
    rag.reindex(sources)
    assert client.collection.count() == 1

    note.unlink()
    stats = rag.reindex(sources)

    assert stats["chunks_removed"] == 1
    assert client.collection.count() == 0


def test_local_rag_status_is_nonfatal_when_dependency_is_lazy(tmp_path) -> None:
    rag = LocalRAG(tmp_path / "chroma", client=_FakeClient(), embedder=_FakeEmbedder())

    status = rag.status()

    assert status["available"] is True
    assert status["vectors"] == 0
    assert status["model"] == "test-embed"
