"""Local Markdown Wiki retrieval.

The Wiki files are external knowledge. This module only defines how the agent
reads and ranks them, so the storage can later be replaced without changing
the agent orchestration code.
"""

import os
import re
from typing import TypedDict


class KnowledgeResult(TypedDict):
    source: str
    content: str
    score: int


def _knowledge_terms(text: str) -> set[str]:
    terms: set[str] = set(re.findall(r"[A-Za-z0-9_]+", text.lower()))
    for chinese_text in re.findall(r"[一-龥]+", text):
        terms.update(
            chinese_text[index : index + 2]
            for index in range(len(chinese_text) - 1)
        )
    return terms


def retrieve(query: str, knowledge_path: str, limit: int = 3) -> list[KnowledgeResult]:
    """Retrieve relevant Markdown Wiki files with explainable lexical scoring."""
    if not os.path.isdir(knowledge_path):
        return []
    query_terms = _knowledge_terms(query)
    if not query_terms:
        return []

    results: list[KnowledgeResult] = []
    for root, _, filenames in os.walk(knowledge_path):
        for filename in filenames:
            if not filename.lower().endswith(".md"):
                continue
            file_path = os.path.join(root, filename)
            with open(file_path, encoding="utf-8") as wiki_file:
                content = wiki_file.read().strip()
            content_lower = content.lower()
            content_terms = _knowledge_terms(content)
            score = sum(
                1
                for term in query_terms
                if term in content_terms or term in content_lower
            )
            if score:
                relative_path = os.path.relpath(file_path, knowledge_path).replace(
                    os.sep, "/"
                )
                results.append(
                    {"source": relative_path, "content": content, "score": score}
                )

    return sorted(results, key=lambda item: item["score"], reverse=True)[:limit]


def format_context(query: str, knowledge_path: str, limit: int = 3) -> str:
    """Format retrieved Wiki content with source names for model grounding."""
    results = retrieve(query, knowledge_path, limit=limit)
    if not results:
        return "知识库参考：当前没有检索到相关 Wiki 内容。"
    lines = ["知识库参考："]
    for result in results:
        lines.append(f"- 来源: {result['source']}\n  内容: {result['content']}")
    return "\n".join(lines)
