"""Incremental local knowledge index for the published company knowledge base."""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from pathlib import Path

from .wiki import KnowledgeResult, _knowledge_terms

EXCLUDED_DIRECTORIES = {"90_原始资料索引", "98_待整理与待核实", "99_已废止但保留"}


class KnowledgeIndexer:
    def __init__(self, index_path: str):
        self.index_path = index_path

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.index_path, timeout=30)
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS knowledge_documents (
                    source_path TEXT PRIMARY KEY,
                    content_hash TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    modified_at REAL NOT NULL,
                    indexed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS knowledge_chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_path TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    UNIQUE(source_path, chunk_index)
                )"""
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_source ON knowledge_chunks(source_path)"
            )

    def sync(self, knowledge_path: str) -> dict[str, int]:
        self.initialize()
        root = Path(knowledge_path)
        if not root.is_dir():
            return {"added": 0, "updated": 0, "removed": 0, "unchanged": 0}

        current_files = {
            str(path.relative_to(root)).replace(os.sep, "/"): path
            for path in root.rglob("*.md")
            if not any(part in EXCLUDED_DIRECTORIES for part in path.relative_to(root).parts)
        }
        added = updated = unchanged = 0
        with self.connect() as connection:
            indexed = {
                row[0]: row[1]
                for row in connection.execute(
                    "SELECT source_path, content_hash FROM knowledge_documents"
                )
            }
            for source_path, file_path in current_files.items():
                content = file_path.read_text(encoding="utf-8-sig").strip()
                content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
                if indexed.get(source_path) == content_hash:
                    unchanged += 1
                    continue
                if source_path in indexed:
                    updated += 1
                else:
                    added += 1
                connection.execute(
                    "DELETE FROM knowledge_chunks WHERE source_path = ?", (source_path,)
                )
                for chunk_index, (title, chunk) in enumerate(_split_document(content)):
                    connection.execute(
                        "INSERT INTO knowledge_chunks (source_path, chunk_index, title, content) VALUES (?, ?, ?, ?)",
                        (source_path, chunk_index, title, chunk),
                    )
                connection.execute(
                    """INSERT INTO knowledge_documents (source_path, content_hash, size, modified_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(source_path) DO UPDATE SET
                        content_hash = excluded.content_hash,
                        size = excluded.size,
                        modified_at = excluded.modified_at,
                        indexed_at = CURRENT_TIMESTAMP""",
                    (source_path, content_hash, file_path.stat().st_size, file_path.stat().st_mtime),
                )

            removed_paths = set(indexed) - set(current_files)
            for source_path in removed_paths:
                connection.execute("DELETE FROM knowledge_chunks WHERE source_path = ?", (source_path,))
                connection.execute("DELETE FROM knowledge_documents WHERE source_path = ?", (source_path,))
        return {
            "added": added,
            "updated": updated,
            "removed": len(removed_paths),
            "unchanged": unchanged,
        }

    def retrieve(self, query: str, knowledge_path: str, limit: int = 3) -> list[KnowledgeResult]:
        self.sync(knowledge_path)
        query_terms = _knowledge_terms(query)
        if not query_terms:
            return []
        results: list[KnowledgeResult] = []
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT source_path, title, content FROM knowledge_chunks"
            ).fetchall()
        for source_path, title, content in rows:
            searchable = f"{title}\n{content}"
            content_terms = _knowledge_terms(searchable)
            score = sum(1 for term in query_terms if term in content_terms or term in searchable.lower())
            if score:
                results.append(
                    {
                        "source": source_path,
                        "content": content,
                        "score": score,
                    }
                )
        return sorted(results, key=lambda item: item["score"], reverse=True)[:limit]


def _split_document(content: str, max_chars: int = 1800) -> list[tuple[str, str]]:
    sections = re.split(r"\n(?=#{1,6}\s)", content)
    chunks: list[tuple[str, str]] = []
    for section in sections:
        section = section.strip()
        if not section:
            continue
        heading = next(
            (line.lstrip("# ").strip() for line in section.splitlines() if line.startswith("#")),
            "未命名章节",
        )
        for start in range(0, len(section), max_chars):
            chunks.append((heading, section[start : start + max_chars]))
    return chunks
