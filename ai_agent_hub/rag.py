"""RAG storage backed by SQLite + sqlite-vec."""

from __future__ import annotations

import asyncio
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
                instance._initialized = False
            return cls._instance

    def __init__(self, db_path: str) -> None:
        if getattr(self, "_initialized", False):
            return
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.enable_load_extension(True)
        import sqlite_vec

        sqlite_vec.load(self.conn)
        self.conn.enable_load_extension(False)
        self._init_tables()
        self._initialized = True

    def _init_tables(self) -> None:
        self.conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS vec_documents USING vec0(embedding float[384])")
        self.conn.execute(
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
        self.conn.commit()

    @classmethod
    def _get_model(cls) -> Any:
        if cls._embedding_model is None:
            with cls._embedding_lock:
                if cls._embedding_model is None:
                    from fastembed import TextEmbedding

                    cls._embedding_model = TextEmbedding("BAAI/bge-small-en-v1.5")
        return cls._embedding_model

    async def _get_embedding(self, text: str) -> list[float]:
        def _compute() -> list[float]:
            model = self._get_model()
            vec = list(model.embed([text]))[0]
            return [float(v) for v in vec.tolist()]

        return await asyncio.to_thread(_compute)

    def add_document(self, content: str, source: str | None = None, metadata: dict[str, Any] | None = None) -> int:
        embedding = asyncio.run(self._get_embedding(content))
        metadata_json = json.dumps(metadata, ensure_ascii=False) if metadata is not None else None
        cursor = self.conn.execute(
            "INSERT INTO rag_documents (content, metadata, source) VALUES (?, ?, ?)",
            (content, metadata_json, source),
        )
        doc_id = int(cursor.lastrowid)
        self.conn.execute("INSERT INTO vec_documents(rowid, embedding) VALUES (?, ?)", (doc_id, json.dumps(embedding)))
        self.conn.commit()
        return doc_id

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        embedding = asyncio.run(self._get_embedding(query))
        rows = self.conn.execute(
            """
            SELECT d.id, d.content, d.source, d.metadata, v.distance
            FROM vec_documents v
            JOIN rag_documents d ON d.id = v.rowid
            WHERE v.embedding MATCH ?
            ORDER BY v.distance
            LIMIT ?
            """,
            (json.dumps(embedding), limit),
        ).fetchall()

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
        return results

    def delete_document(self, doc_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM rag_documents WHERE id = ?", (doc_id,))
        self.conn.execute("DELETE FROM vec_documents WHERE rowid = ?", (doc_id,))
        self.conn.commit()
        return cur.rowcount > 0
