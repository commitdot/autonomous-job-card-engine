"""
Local RAG Indexer
=================
Indexes Markdown, text, and JSON documents into a lightweight local chunk store.
Computes lightweight bag-of-words / character n-gram TF-IDF embeddings to enable
instant offline semantic search with zero network egress.
"""

import os
import re
import math
import json
from pathlib import Path
from typing import List, Dict, Any, Tuple


class LocalRAGIndexer:
    def __init__(self, workspace_root: str, storage_dir: str = None):
        self.workspace_root = Path(workspace_root)
        self.storage_dir = Path(storage_dir or (self.workspace_root / ".jobs" / "rag"))
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.index_file = self.storage_dir / "index.json"
        self.chunks: List[Dict[str, Any]] = []
        self.idf: Dict[str, float] = {}

    def _tokenize(self, text: str) -> List[str]:
        words = re.findall(r"\b[A-Za-z0-9_-]{2,}\b", text.lower())
        return words

    def _compute_tf(self, tokens: List[str]) -> Dict[str, float]:
        tf = {}
        for token in tokens:
            tf[token] = tf.get(token, 0) + 1
        total = max(len(tokens), 1)
        return {k: v / total for k, v in tf.items()}

    def index_paths(self, paths: List[str]) -> int:
        """
        Scans given relative paths in workspace and builds local RAG chunk index.
        """
        raw_chunks = []
        for rel_path in paths:
            target = self.workspace_root / rel_path
            if not target.exists():
                continue
            if target.is_file():
                files = [target]
            else:
                files = list(target.rglob("*.md")) + list(target.rglob("*.txt")) + list(target.rglob("*.json"))

            for f in files:
                try:
                    content = f.read_text(encoding="utf-8", errors="ignore")
                    # Split into ~500 char sections or paragraphs
                    paragraphs = [p.strip() for p in content.split("\n\n") if len(p.strip()) > 30]
                    for idx, para in enumerate(paragraphs):
                        raw_chunks.append({
                            "source": str(f.relative_to(self.workspace_root)),
                            "chunk_id": f"{f.stem}_{idx}",
                            "content": para,
                            "tokens": self._tokenize(para),
                        })
                except Exception:
                    continue

        if not raw_chunks:
            # Write empty index
            self.chunks = []
            self.idf = {}
            self._save()
            return 0

        # Compute IDF across all chunks
        doc_count = len(raw_chunks)
        doc_freq: Dict[str, int] = {}
        for chunk in raw_chunks:
            unique_tokens = set(chunk["tokens"])
            for t in unique_tokens:
                doc_freq[t] = doc_freq.get(t, 0) + 1

        self.idf = {t: math.log((doc_count + 1) / (freq + 1)) + 1.0 for t, freq in doc_freq.items()}

        # Compute TF-IDF vectors
        self.chunks = []
        for chunk in raw_chunks:
            tf = self._compute_tf(chunk["tokens"])
            tfidf = {t: tf[t] * self.idf.get(t, 1.0) for t in tf}
            # Normalize vector
            norm = math.sqrt(sum(v * v for v in tfidf.values())) or 1.0
            norm_tfidf = {t: v / norm for t, v in tfidf.items()}
            self.chunks.append({
                "source": chunk["source"],
                "chunk_id": chunk["chunk_id"],
                "content": chunk["content"],
                "vector": norm_tfidf
            })

        self._save()
        return len(self.chunks)

    def _save(self):
        payload = {
            "idf": self.idf,
            "chunks": self.chunks
        }
        with open(self.index_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    def load(self) -> bool:
        if not self.index_file.exists():
            return False
        try:
            with open(self.index_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.idf = data.get("idf", {})
            self.chunks = data.get("chunks", [])
            return True
        except Exception:
            return False
