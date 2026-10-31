"""Vector database (ChromaDB, stored on disk) with free local embeddings (FastEmbed).

Two collections:
  * shop_items - every product, so "cooking oil" can find "Sunflower Oil" by meaning.
  * shop_docs  - chunks of files the owner uploads, so the assistant can answer from big PDFs / Excel sheets.

Every function degrades gracefully: if the vector packages or the embedding model are unavailable the
assistant simply falls back to fuzzy text matching. Nothing here can break the shop.
"""
import logging
import threading

from django.conf import settings

log = logging.getLogger("assistant")
_lock = threading.Lock()
_embeddings = None
_stores = {}
_broken = False


def _store(name):
    global _embeddings, _broken
    if _broken:
        return None
    with _lock:
        if name in _stores:
            return _stores[name]
        try:
            from chromadb.config import Settings as ChromaSettings
            from langchain_chroma import Chroma
            from langchain_community.embeddings import FastEmbedEmbeddings

            if _embeddings is None:
                _embeddings = FastEmbedEmbeddings(model_name=settings.EMBEDDING_MODEL)
            _stores[name] = Chroma(
                collection_name=name,
                embedding_function=_embeddings,
                persist_directory=str(settings.CHROMA_DIR),
                client_settings=ChromaSettings(anonymized_telemetry=False),
            )
            return _stores[name]
        except Exception as e:  # missing package, no internet for first model download, etc.
            log.warning("Vector DB disabled: %s", e)
            _broken = True
            return None


def is_ready():
    return _store("shop_items") is not None


# ------------------------------------------------------------------ products
def reindex_items():
    """Rebuild the product index (cheap: a shop has hundreds of products, not millions)."""
    vs = _store("shop_items")
    if vs is None:
        return
    try:
        from store.models import Item

        old = vs.get().get("ids") or []
        if old:
            vs.delete(ids=old)
        items = list(Item.objects.select_related("category").all())
        if items:
            vs.add_texts(
                texts=[f"{i.name}. Also called: {i.aliases}. Category: {i.category.name if i.category else ''}" for i in items],
                metadatas=[{"item_id": i.id} for i in items],
                ids=[i.id for i in items],
            )
    except Exception as e:
        log.warning("reindex_items failed: %s", e)


def search_items_semantic(query, k=3):
    vs = _store("shop_items")
    if vs is None:
        return []
    try:
        if not (vs.get(limit=1).get("ids")):
            reindex_items()
        return [d.metadata["item_id"] for d, _score in vs.similarity_search_with_score(query, k=k)]
    except Exception as e:
        log.warning("search_items_semantic failed: %s", e)
        return []


# ------------------------------------------------------------------ uploaded documents
def add_document(session_id, name, text):
    """Split a file's text into chunks and store them for this chat session only."""
    vs = _store("shop_docs")
    if vs is None or not text.strip():
        return 0
    try:
        from langchain_text_splitters import RecursiveCharacterTextSplitter

        chunks = RecursiveCharacterTextSplitter(chunk_size=900, chunk_overlap=100).split_text(text[:200_000])
        vs.add_texts(
            texts=chunks,
            metadatas=[{"session": session_id, "source": name} for _ in chunks],
        )
        return len(chunks)
    except Exception as e:
        log.warning("add_document failed: %s", e)
        return 0


def search_docs(session_id, query, k=4):
    vs = _store("shop_docs")
    if vs is None:
        return []
    try:
        hits = vs.similarity_search(query, k=k, filter={"session": session_id})
        return [{"source": d.metadata.get("source", ""), "text": d.page_content[:900]} for d in hits]
    except Exception as e:
        log.warning("search_docs failed: %s", e)
        return []


def delete_session_docs(session_id):
    vs = _store("shop_docs")
    if vs is None:
        return
    try:
        ids = vs.get(where={"session": session_id}).get("ids") or []
        if ids:
            vs.delete(ids=ids)
    except Exception as e:
        log.warning("delete_session_docs failed: %s", e)
