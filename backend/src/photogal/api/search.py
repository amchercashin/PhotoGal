"""Search API: CLIP text-to-image search with category shortcut."""

import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from photogal.api.deps import get_clip, get_db
from photogal import search as search_module
from photogal.pipeline.analyzer import _CATEGORIES
from photogal.translate import translate_query

router = APIRouter(prefix="/search", tags=["search"])


class SearchRequest(BaseModel):
    query: str
    limit: int = 200


class SearchResult(BaseModel):
    photo_id: int
    similarity: float


class SearchResponse(BaseModel):
    query: str
    translated_query: str | None = None
    results: list[SearchResult]
    total_with_embeddings: int
    elapsed_ms: float


@router.post("/", response_model=SearchResponse)
def search_photos(req: SearchRequest):
    db = get_db()
    query = req.query.strip()
    if len(query) > 256:
        raise HTTPException(status_code=400, detail="Query too long (max 256 characters)")
    if not query:
        return SearchResponse(
            query=query, results=[], total_with_embeddings=db.count_embeddings(), elapsed_ms=0,
        )

    # Translate Russian → English (dictionary + argos-translate fallback)
    translated = translate_query(query)
    effective_query = translated or query

    # Category shortcut: exact match on category key → filter by content_category
    if effective_query.lower() in _CATEGORIES:
        t0 = time.perf_counter()
        limit = min(req.limit, 500)
        photos = db.get_photos_paginated(
            limit=limit, offset=0, filter_category=effective_query.lower(),
        )
        elapsed = (time.perf_counter() - t0) * 1000
        return SearchResponse(
            query=query,
            translated_query=translated,
            results=[SearchResult(photo_id=p["id"], similarity=1.0) for p in photos],
            total_with_embeddings=search_module.get_cached_count(),
            elapsed_ms=round(elapsed, 1),
        )

    # Regular CLIP text-to-image search
    t0 = time.perf_counter()
    clip = get_clip()
    text_emb = clip.embed_texts([effective_query])[0]  # (768,) float32
    results = search_module.search(db, text_emb, limit=min(req.limit, 500))
    elapsed = (time.perf_counter() - t0) * 1000

    return SearchResponse(
        query=query,
        translated_query=translated,
        results=[SearchResult(**r) for r in results],
        total_with_embeddings=search_module.get_cached_count(),
        elapsed_ms=round(elapsed, 1),
    )
