"""
AJE Local RAG Subsystem
========================
Provides local, air-gapped vector search and document indexing for internal
RFCs, architecture documentation, design guidelines, and database schemas.
Uses a fast, pure-Python cosine-similarity vector store with BM25/keyword fallback,
so it requires zero heavy external binary dependencies or database servers.
"""

from .indexer import LocalRAGIndexer
from .retriever import LocalRAGRetriever

__all__ = ["LocalRAGIndexer", "LocalRAGRetriever"]
