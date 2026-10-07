"""
Local RAG Retriever
===================
Queries the local index using cosine similarity and keyword matching.
Injects top-k relevant architecture/schema chunks directly into prompts.
"""

import math
import re
from pathlib import Path
from typing import List, Dict, Any
from .indexer import LocalRAGIndexer


class LocalRAGRetriever:
    def __init__(self, workspace_root: str, storage_dir: str = None):
        self.indexer = LocalRAGIndexer(workspace_root=workspace_root, storage_dir=storage_dir)
        self.indexer.load()

    def query(self, query_text: str, top_k: int = 3) -> List[Dict[str, Any]]:
        """
        Retrieves top_k most relevant chunks for a given natural language query.
        """
        if not self.indexer.chunks:
            # Try reloading in case index was refreshed
            if not self.indexer.load() or not self.indexer.chunks:
                return []

        tokens = re.findall(r"\b[A-Za-z0-9_-]{2,}\b", query_text.lower())
        if not tokens:
            return []

        # Compute query vector
        tf = {}
        for token in tokens:
            tf[token] = tf.get(token, 0) + 1
        total = len(tokens)
        query_tfidf = {t: (cnt / total) * self.indexer.idf.get(t, 1.0) for t, cnt in tf.items()}
        q_norm = math.sqrt(sum(v * v for v in query_tfidf.values())) or 1.0
        norm_query = {t: v / q_norm for t, v in query_tfidf.items()}

        scores = []
        for chunk in self.indexer.chunks:
            c_vec = chunk.get("vector", {})
            # Dot product (both vectors are normalized)
            score = sum(norm_query[t] * c_vec[t] for t in norm_query if t in c_vec)
            if score > 0.05:  # Relevance threshold
                scores.append({
                    "score": round(score, 3),
                    "source": chunk["source"],
                    "chunk_id": chunk["chunk_id"],
                    "content": chunk["content"]
                })

        scores.sort(key=lambda x: x["score"], reverse=True)
        return scores[:top_k]

    def format_rag_context(self, query_text: str, top_k: int = 3) -> str:
        """
        Formats top results into a clean markdown block ready for prompt injection.
        """
        results = self.query(query_text, top_k=top_k)
        if not results:
            return ""

        lines = ["## Internal Knowledge & Architecture Context (Retrieved via RAG):"]
        for idx, res in enumerate(results, 1):
            lines.append(f"### [{idx}] Source: {res['source']} (Relevance Score: {res['score']})")
            lines.append(res['content'])
            lines.append("")
        return "\n".join(lines).strip()
