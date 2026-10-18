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

    def _extract_chunks_from_file(self, file_path: Path, source_rel: str) -> List[Dict[str, Any]]:
        """
        Extracts semantic text chunks from markdown, text, or structured JSON (like lessons_learned.json).
        """
        chunks: List[Dict[str, Any]] = []
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            # Special structured handling for JSON files (especially lessons_learned.json)
            if file_path.suffix.lower() == ".json":
                try:
                    data = json.loads(content)
                    if isinstance(data, list) and all(isinstance(item, dict) for item in data):
                        for idx, item in enumerate(data):
                            if "summary" in item or "lesson_id" in item or "category" in item:
                                category = item.get("category", "lesson").upper()
                                summary = item.get("summary", "")
                                details = item.get("details", "")
                                tags_str = ", ".join(item.get("tags", []))
                                source_card = item.get("source_card_id", "")
                                lesson_text = f"[{category}] {summary}\nDetails: {details}\nTags: {tags_str}\nSource: {source_card}".strip()
                                chunks.append({
                                    "source": source_rel,
                                    "chunk_id": f"lesson_{item.get('lesson_id', idx)}",
                                    "content": lesson_text,
                                    "tokens": self._tokenize(lesson_text),
                                })
                        if chunks:
                            return chunks
                except Exception:
                    pass

            # Standard paragraph splitting for Markdown and text
            paragraphs = [p.strip() for p in content.split("\n\n") if len(p.strip()) > 20]
            for idx, para in enumerate(paragraphs):
                chunks.append({
                    "source": source_rel,
                    "chunk_id": f"{file_path.stem}_{idx}",
                    "content": para,
                    "tokens": self._tokenize(para),
                })
        except Exception:
            pass
        return chunks

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
                source_rel = str(f.relative_to(self.workspace_root)).replace("\\", "/")
                file_chunks = self._extract_chunks_from_file(f, source_rel)
                raw_chunks.extend(file_chunks)

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

    def reindex_file(self, rel_path: str) -> int:
        """
        Incrementally re-indexes a single file without rescanning entire workspace.
        """
        self.load()
        norm_target = str(Path(rel_path)).replace("\\", "/")
        # Filter out old chunks from this source
        retained = [c for c in self.chunks if c.get("source", "").replace("\\", "/") != norm_target]

        target_file = self.workspace_root / rel_path
        new_chunks = []
        if target_file.exists() and target_file.is_file():
            new_chunks = self._extract_chunks_from_file(target_file, norm_target)

        all_raw = [{"tokens": c["tokens"] if "tokens" in c else self._tokenize(c["content"]), "chunk": c} for c in retained]
        for c in new_chunks:
            all_raw.append({"tokens": c["tokens"], "chunk": c})

        if not all_raw:
            self.chunks = []
            self.idf = {}
            self._save()
            return 0

        doc_count = len(all_raw)
        doc_freq: Dict[str, int] = {}
        for item in all_raw:
            for t in set(item["tokens"]):
                doc_freq[t] = doc_freq.get(t, 0) + 1

        self.idf = {t: math.log((doc_count + 1) / (freq + 1)) + 1.0 for t, freq in doc_freq.items()}

        self.chunks = []
        for item in all_raw:
            c = item["chunk"]
            tokens = item["tokens"]
            tf = self._compute_tf(tokens)
            tfidf = {t: tf[t] * self.idf.get(t, 1.0) for t in tf}
            norm = math.sqrt(sum(v * v for v in tfidf.values())) or 1.0
            norm_tfidf = {t: v / norm for t, v in tfidf.items()}
            self.chunks.append({
                "source": c.get("source", norm_target),
                "chunk_id": c.get("chunk_id", "chunk"),
                "content": c.get("content", ""),
                "vector": norm_tfidf,
            })

        self._save()
        return len(new_chunks)

    def index_lessons(self, lessons_path: str = ".jobs/recursion/lessons_learned.json") -> int:
        """
        Indexes institutional lessons from .jobs/recursion/lessons_learned.json
        into the local RAG store.
        """
        return self.reindex_file(lessons_path)

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
