import difflib
import unicodedata
import json
import logging
import os
import threading
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

TOKEN_BLACKLIST = {
    "the",
    "and",
    "para",
    "como",
    "com",
    "das",
    "dos",
    "uma",
    "sobre",
    "que",
    "esta",
    "este",
    "isso",
    "isto",
    "sao",
}


def _normalize_token(token: str) -> Optional[str]:
    """Normalize and filter tokens used in the lightweight embeddings."""
    token = token.lower().strip()
    if not token:
        return None
    if len(token) <= 2:
        return None
    if any(ch.isdigit() for ch in token):
        return None
    if token in TOKEN_BLACKLIST:
        return None
    return token


def _tokens_from_text(text: str, limit: int = 60) -> Dict[str, float]:
    """Tokenize text using a simple bag-of-words representation with tf-like weights."""
    words = [_normalize_token(t) for t in text.replace("\n", " ").split(" ")]
    words = [w for w in words if w]
    if not words:
        return {}
    counter = Counter(words)
    most_common = counter.most_common(limit)
    max_freq = most_common[0][1]
    return {token: round(freq / max_freq, 4) for token, freq in most_common}


def cosine_similarity(a: Dict[str, float], b: Dict[str, float]) -> float:
    """Compute cosine similarity between two sparse token-weight dictionaries."""
    if not a or not b:
        return 0.0
    shared_tokens = set(a).intersection(b)
    if not shared_tokens:
        return 0.0
    numerator = sum(a[token] * b[token] for token in shared_tokens)
    sum_sq_a = sum(value * value for value in a.values())
    sum_sq_b = sum(value * value for value in b.values())
    denominator = np.sqrt(sum_sq_a) * np.sqrt(sum_sq_b)
    if denominator == 0:
        return 0.0
    return float(round(numerator / denominator, 4))


def _timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _normalize_label(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    stripped = "".join(ch for ch in normalized if ch.isalnum() or ch.isspace())
    return stripped.lower().strip()


@dataclass
class KnowledgeEntry:
    """Dataclass representing an entry in the knowledge base."""

    id: str
    file_name: str
    category: str
    theme: str
    confidence: float
    summary: str
    justification: str
    tokens: Dict[str, float]
    areas_secundarias: List[str] = field(default_factory=list)
    feedback: Dict[str, int] = field(default_factory=lambda: {"positivo": 0, "negativo": 0})
    created_at: str = field(default_factory=_timestamp)
    updated_at: str = field(default_factory=_timestamp)

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "file_name": self.file_name,
            "category": self.category,
            "theme": self.theme,
            "confidence": self.confidence,
            "summary": self.summary,
            "justification": self.justification,
            "tokens": self.tokens,
            "areas_secundarias": self.areas_secundarias,
            "feedback": self.feedback,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class KnowledgeBase:
    """Manages knowledge persistence and lightweight semantic similarity support."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.RLock()
        self._data = {
            "version": 1,
            "entries": [],
            "categories": [],
            "feedback_history": [],
            "category_keywords": {},
        }
        self._load()

    def _load(self) -> None:
        with self._lock:
            if os.path.exists(self.path):
                try:
                    with open(self.path, "r", encoding="utf-8") as handler:
                        self._data = json.load(handler)
                        if "category_keywords" not in self._data:
                            self._data["category_keywords"] = {}
                        logging.debug("Knowledge base loaded with %s entries.", len(self._data.get("entries", [])))
                except json.JSONDecodeError as exc:
                    logging.error("Invalid knowledge base file. Reinitializing. Error: %s", exc)
                    self._write()
            else:
                logging.info("Knowledge base not found. Creating a new one at %s", self.path)
                self._write()

    def _write(self) -> None:
        with self._lock:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as handler:
                json.dump(self._data, handler, indent=2, ensure_ascii=False)

    def refresh(self) -> None:
        """Reload knowledge data from disk."""
        self._load()

    def _ensure_category(self, category: str) -> str:
        candidate = (category or "").strip()
        if not candidate:
            candidate = "outros"
        categories = self._data.setdefault("categories", [])
        normalized_candidate = _normalize_label(candidate)
        for existing in categories:
            if _normalize_label(existing) == normalized_candidate:
                return existing
        best_match = None
        best_ratio = 0.0
        for existing in categories:
            ratio = difflib.SequenceMatcher(
                None, _normalize_label(existing), normalized_candidate
            ).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_match = existing
        if best_match and best_ratio >= 0.85:
            logging.info(
                "Categoria '%s' reaproveitada por similaridade com '%s' (%.2f).",
                candidate,
                best_match,
                best_ratio,
            )
            return best_match
        categories.append(candidate)
        logging.info("Nova categoria registrada na base de conhecimento: %s", candidate)
        return candidate

    def add_entry(
        self,
        file_name: str,
        category: str,
        theme: str,
        confidence: float,
        summary: str,
        justification: str,
        areas_secundarias: Optional[List[str]],
        raw_text: str,
    ) -> KnowledgeEntry:
        """Persist a new knowledge entry and return it."""
        embedding_tokens = _tokens_from_text(raw_text)
        with self._lock:
            canonical_category = self._ensure_category(category)
            entry = KnowledgeEntry(
                id=str(uuid.uuid4()),
                file_name=file_name,
                category=canonical_category,
                theme=theme,
                confidence=confidence,
                summary=summary,
                justification=justification,
                tokens=embedding_tokens,
                areas_secundarias=areas_secundarias or [],
            )
            self._data.setdefault("entries", []).append(entry.to_dict())
            self._maybe_register_keywords(canonical_category, embedding_tokens, confidence)
            keyword_snapshot = dict(
                self._data.get("category_keywords", {}).get(entry.category, {})
            )
            self._write()
        logging.info(
            "Conhecimento registrado: %s (categoria=%s, conf=%.2f%%, tokens=%s)",
            entry.file_name,
            entry.category,
            entry.confidence * 100,
            len(entry.tokens),
        )
        if confidence >= 0.9 and keyword_snapshot:
            top_tokens = sorted(keyword_snapshot.items(), key=lambda item: item[1], reverse=True)[:5]
            logging.info(
                "Palavras-chave reforcadas para %s: %s",
                entry.category,
                ", ".join(token for token, _ in top_tokens),
            )
        logging.debug("Knowledge base entry added for %s in category %s", file_name, entry.category)
        return entry

    def update_entry_feedback(
        self, file_name: str, status: str, observations: str, new_category: Optional[str] = None
    ) -> Optional[Dict]:
        """Update feedback counters for a given file name."""
        status = status.lower().strip()
        valid_status = {"correto", "correta", "ok", "aprovado", "incorreto", "incorreta", "rever"}
        if status not in valid_status:
            logging.warning("Feedback status %s is not recognized. Ignoring.", status)
            return None
        with self._lock:
            entries = self._data.get("entries", [])
            for entry in entries:
                if entry.get("file_name") == file_name:
                    feedback = entry.setdefault("feedback", {"positivo": 0, "negativo": 0})
                if status in {"correto", "correta", "ok", "aprovado"}:
                    feedback["positivo"] = feedback.get("positivo", 0) + 1
                else:
                    feedback["negativo"] = feedback.get("negativo", 0) + 1

                if new_category:
                    normalized_input = new_category.strip()
                    if normalized_input:
                        resolved_category = self._ensure_category(normalized_input)
                        if resolved_category != entry.get("category"):
                            logging.info(
                                "Categoria ajustada por feedback: %s -> %s",
                                entry.get("category"),
                                resolved_category,
                            )
                            entry["category"] = resolved_category

                entry["updated_at"] = _timestamp()
                history_item = {
                    "file_name": file_name,
                    "status": status,
                    "observations": observations,
                    "timestamp": _timestamp(),
                    "new_category": new_category,
                }
                self._data.setdefault("feedback_history", []).append(history_item)
                self._write()
                logging.info("Feedback registered for %s with status %s", file_name, status)
                return entry
        logging.warning("No knowledge entry found for feedback file %s", file_name)
        return None

    def find_similar(self, raw_text: str, top_n: int = 3) -> List[Tuple[Dict, float]]:
        """Find similar entries based on lightweight embeddings."""
        target_tokens = _tokens_from_text(raw_text)
        with self._lock:
            entries = self._data.get("entries", [])
        scored: List[Tuple[Dict, float]] = []
        for entry in entries:
            score = cosine_similarity(target_tokens, entry.get("tokens", {}))
            if score > 0:
                scored.append((entry, score))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:top_n]

    def known_categories(self) -> List[str]:
        with self._lock:
            return list(self._data.get("categories", []))

    def export_snapshot(self) -> Dict:
        with self._lock:
            return json.loads(json.dumps(self._data))

    def category_profiles(self, top_n: int = 12) -> Dict[str, Dict[str, List[str]]]:
        with self._lock:
            keywords = self._data.setdefault("category_keywords", {})
            categories = self._data.get("categories", [])
            entries = self._data.get("entries", [])

        profile = {}
        for category in categories:
            cat_keywords = keywords.get(category, {})
            sorted_kw = sorted(cat_keywords.items(), key=lambda item: item[1], reverse=True)
            top_keywords = [kw for kw, _ in sorted_kw[:top_n]]

            recent_samples = [
                entry.get("file_name")
                for entry in entries
                if entry.get("category") == category
            ][-top_n:]

            profile[category] = {
                "top_keywords": top_keywords,
                "recent_samples": recent_samples,
            }
        return profile

    def category_match_report(self, raw_text: str, top_n: int = 5) -> List[Dict[str, float]]:
        target_tokens = _tokens_from_text(raw_text)
        with self._lock:
            entries = list(self._data.get("entries", []))
        category_scores: Dict[str, List[float]] = {}
        for entry in entries:
            cat = entry.get("category") or "outros"
            tokens = entry.get("tokens", {})
            score = cosine_similarity(target_tokens, tokens)
            if score <= 0:
                continue
            category_scores.setdefault(cat, []).append(score)
        aggregated: List[Tuple[str, float, float]] = []
        for cat, scores in category_scores.items():
            best = max(scores)
            average = sum(scores) / len(scores)
            aggregated.append((cat, best, average))
        aggregated.sort(key=lambda item: item[1], reverse=True)
        report = [
            {"category": cat, "best_match": round(best, 4), "average_match": round(avg, 4)}
            for cat, best, avg in aggregated[:top_n]
        ]
        return report

    def _maybe_register_keywords(self, category: str, tokens: Dict[str, float], confidence: float) -> None:
        if confidence < 0.9:
            return
        top_tokens = sorted(tokens.items(), key=lambda item: item[1], reverse=True)[:20]
        with self._lock:
            keyword_map = self._data.setdefault("category_keywords", {})
            cat_keywords = keyword_map.setdefault(category, {})
            for token, weight in top_tokens:
                cat_keywords[token] = round(cat_keywords.get(token, 0.0) + weight, 4)
            top_keywords = [token for token, _ in top_tokens[:5]]
            logging.info("Categoria %s reforcada com novas palavras-chave: %s", category, ", ".join(top_keywords))
