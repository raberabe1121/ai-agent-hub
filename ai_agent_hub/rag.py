"""RAG storage backed by SQLite + sqlite-vec."""

from __future__ import annotations

import json
import sqlite3
from threading import Lock
from typing import Any


class RAGStore:
    """Simple singleton RAG store."""

    _instance: "RAGStore | None" = None
    _instance_db_path: str | None = None
    _instance_lock = Lock()
    _embedding_model: Any = None
    _embedding_lock = Lock()

    def __new__(cls, db_path: str) -> "RAGStore":
        with cls._instance_lock:
            if cls._instance is None or cls._instance_db_path != db_path:
                instance = super().__new__(cls)
                cls._instance = instance
                cls._instance_db_path = db_path
            return cls._instance

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        if not hasattr(self, "_tables_lock"):
            self._tables_lock = Lock()
            self._tables_initialized = False

    def _get_conn(self) -> sqlite3.Connection:
        """Return a fresh SQLite connection per call (thread-safe for FastAPI workers)."""
        import sqlite_vec

        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        return conn

    def _ensure_tables(self) -> None:
        if self._tables_initialized:
            return
        with self._tables_lock:
            if self._tables_initialized:
                return
            self._init_tables()
            self._tables_initialized = True

    def _init_tables(self) -> None:
        conn = self._get_conn()
        try:
            conn.execute("DROP TABLE IF EXISTS vec_documents")
            conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS vec_documents USING vec0(embedding float[384])")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS rag_documents (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  content TEXT NOT NULL,
                  metadata TEXT,
                  source TEXT,
                  created_at TEXT DEFAULT (datetime('now'))
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    @classmethod
    def _get_model(cls) -> Any:
        if cls._embedding_model is None:
            with cls._embedding_lock:
                if cls._embedding_model is None:
                    from fastembed import TextEmbedding

                    cls._embedding_model = TextEmbedding(
                        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
                    )
        return cls._embedding_model

    def _get_embedding(self, text: str) -> list[float]:
        model = self._get_model()
        vec = list(model.embed([text]))[0]
        return [float(v) for v in vec.tolist()]

    def _normalize(self, vec: list[float]) -> list[float]:
        try:
            import numpy as np

            arr = np.array(vec, dtype=np.float32)
            norm = np.linalg.norm(arr)
            if norm > 0:
                arr = arr / norm
            return arr.tolist()
        except Exception:
            length = sum(v * v for v in vec) ** 0.5
            if length <= 0:
                return vec
            return [float(v / length) for v in vec]

    def add_document(self, content: str, source: str | None = None, metadata: dict[str, Any] | None = None, embedding_text: str | None = None) -> int:
        self._ensure_tables()
        text_for_embedding = embedding_text if isinstance(embedding_text, str) and embedding_text else content
        embedding = self._get_embedding(text_for_embedding)
        embedding = self._normalize(embedding)
        metadata_json = json.dumps(metadata, ensure_ascii=False) if metadata is not None else None
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                "INSERT INTO rag_documents (content, metadata, source) VALUES (?, ?, ?)",
                (content, metadata_json, source),
            )
            doc_id = int(cursor.lastrowid)
            conn.execute("INSERT INTO vec_documents(rowid, embedding) VALUES (?, ?)", (doc_id, json.dumps(embedding)))
            conn.commit()
            return doc_id
        finally:
            conn.close()

    def search(self, query: str, limit: int = 5, max_distance: float | None = None) -> list[dict[str, Any]]:
        self._ensure_tables()
        embedding = self._get_embedding(query)
        embedding = self._normalize(embedding)
        k = max(1, int(limit))
        conn = self._get_conn()
        try:
            rows = conn.execute(
                f"""
                SELECT d.id, d.content, d.source, d.metadata, v.distance
                FROM vec_documents v
                JOIN rag_documents d ON d.id = v.rowid
                WHERE v.embedding MATCH ?
                AND k = {k}
                ORDER BY v.distance
                """,
                (json.dumps(embedding),),
            ).fetchall()
        finally:
            conn.close()

        results: list[dict[str, Any]] = []
        for row in rows:
            raw_metadata = row["metadata"]
            metadata = None
            if isinstance(raw_metadata, str) and raw_metadata:
                try:
                    metadata = json.loads(raw_metadata)
                except json.JSONDecodeError:
                    metadata = raw_metadata
            results.append(
                {
                    "id": row["id"],
                    "content": row["content"],
                    "source": row["source"],
                    "metadata": metadata,
                    "distance": row["distance"],
                }
            )
        if max_distance is not None:
            results = [r for r in results if r["distance"] <= max_distance]
        return results

    def delete_document(self, doc_id: int) -> bool:
        self._ensure_tables()
        conn = self._get_conn()
        try:
            cur = conn.execute("DELETE FROM rag_documents WHERE id = ?", (doc_id,))
            conn.execute("DELETE FROM vec_documents WHERE rowid = ?", (doc_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()
