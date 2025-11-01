import difflib
import hashlib
import unicodedata
import json
import logging
import os
import shutil
import threading
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

try:
    import fitz  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    fitz = None  # type: ignore

try:
    from docx import Document  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    Document = None  # type: ignore


SUPPORTED_KNOWLEDGE_EXTENSIONS = {".pdf", ".docx", ".txt"}

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


def _slugify_category_name(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "_" for ch in value or "")
    return "_".join(part for part in cleaned.split("_") if part)


@dataclass
class KnowledgeEntry:
    """Registro completo na base de conhecimento com os metadados que eu preciso reaproveitar depois."""

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


class KnowledgeBaseRefresher(threading.Thread):
    """Background thread that periodically refreshes knowledge folders."""

    def __init__(self, knowledge_base: "KnowledgeBase", interval_seconds: int) -> None:
        super().__init__(daemon=True)
        self._knowledge_base = knowledge_base
        self._interval = max(1, int(interval_seconds))
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:  # pragma: no cover - background worker
        while not self._stop_event.is_set():
            try:
                self._knowledge_base.refresh_category_documents()
            except Exception as exc:
                logging.error("Scheduled knowledge refresh failed: %s", exc)
            if self._stop_event.wait(self._interval):
                break


class KnowledgeBase:
    """Persisto e organizo a base de conhecimento, cuidando das categorias e da similaridade sem depender de um banco externo."""

    def __init__(self, path: str, category_root: Optional[str] = None):
        self.path = Path(path)
        self.category_root = Path(category_root).resolve() if category_root else None
        self._lock = threading.RLock()
        self._category_scan_lock = threading.RLock()
        self._data = {
            "version": 1,
            "entries": [],
            "categories": [],
            "feedback_history": [],
            "category_keywords": {},
            "category_documents": {},
            "category_directories": {},
            "category_feedback": {},
        }
        if self.category_root:
            self.category_root.mkdir(parents=True, exist_ok=True)
        self._load()

    def _load(self) -> None:
        with self._lock:
            if self.path.exists():
                try:
                    with open(self.path, "r", encoding="utf-8") as handler:
                        loaded = json.load(handler)
                except json.JSONDecodeError as exc:
                    logging.error("Invalid knowledge base file. Reinitializing. Error: %s", exc)
                    self._write()
                    return
                # Merge defaults to keep backward compatibility with older schema versions.
                for key, value in self._data.items():
                    if key not in loaded:
                        loaded[key] = value if not isinstance(value, dict) else dict(value)
                self._data = loaded
                logging.debug("Knowledge base loaded with %s entries.", len(self._data.get("entries", [])))
            else:
                logging.info("Knowledge base not found. Creating a new one at %s", self.path)
                self._write()

    def _write(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as handler:
                json.dump(self._data, handler, indent=2, ensure_ascii=False)

    def _slugify(self, value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value or "")
        cleaned = "".join(ch.lower() if ch.isalnum() else "_" for ch in normalized)
        cleaned = "_".join(part for part in cleaned.split("_") if part)
        return cleaned or "categoria"

    def _category_metadata_path(self, directory: Path) -> Path:
        return directory / "category.json"

    def _read_category_metadata(self, directory: Path) -> Dict:
        meta_path = self._category_metadata_path(directory)
        if not meta_path.exists():
            return {}
        try:
            with open(meta_path, "r", encoding="utf-8") as handler:
                data = json.load(handler)
                if isinstance(data, dict):
                    return data
        except json.JSONDecodeError:
            logging.warning("Invalid metadata file at %s. It will be recreated.", meta_path)
        return {}

    def _empty_category_doc_state(self) -> Dict:
        return {
            "processed_files": {},
            "aggregated_tokens": {},
            "top_terms": [],
            "document_count": 0,
            "last_scan": None,
        }

    def _merge_category_directory(self, category: str, target: Path, duplicate: Path) -> None:
        if target.resolve() == duplicate.resolve():
            return
        normalized_category = _normalize_label(category)
        target_meta = self._read_category_metadata(target)
        duplicate_meta = self._read_category_metadata(duplicate)
        meta_path = self._category_metadata_path(target)
        # Consolidate metadata
        combined_meta = dict(target_meta or {})
        combined_meta["name"] = combined_meta.get("name") or duplicate_meta.get("name") or category
        created_candidates = [
            combined_meta.get("created_at"),
            duplicate_meta.get("created_at") if duplicate_meta else None,
        ]
        created_candidates = [value for value in created_candidates if value]
        if created_candidates:
            combined_meta["created_at"] = min(created_candidates)
        else:
            combined_meta.setdefault("created_at", _timestamp())
        combined_meta["updated_at"] = _timestamp()

        for item in duplicate.iterdir():
            destination = target / item.name
            if destination.exists():
                if item.name == "category.json":
                    try:
                        if destination.read_text(encoding="utf-8") == item.read_text(encoding="utf-8"):
                            item.unlink()
                        else:
                            item.unlink()
                    except Exception as exc:
                        logging.debug(
                            "Falha ao comparar category.json em duplicata %s: %s",
                            duplicate,
                            exc,
                        )
                    continue
                logging.warning(
                    "Ignorando arquivo duplicado %s em %s (destino %s ja existe).",
                    item,
                    duplicate,
                    destination,
                )
                continue
            try:
                shutil.move(str(item), destination)
                logging.info(
                    "Arquivo %s movido de duplicata %s para %s",
                    item.name,
                    duplicate.name,
                    target.name,
                )
            except Exception as exc:
                logging.warning(
                    "Falha ao mover %s de duplicata %s para %s: %s",
                    item,
                    duplicate,
                    target,
                    exc,
                )
        with open(meta_path, "w", encoding="utf-8") as handler:
            json.dump(combined_meta, handler, indent=2, ensure_ascii=False)
        try:
            shutil.rmtree(duplicate)
            logging.info(
                "Diretorio duplicado %s removido apos consolidacao em %s",
                duplicate.name,
                target.name,
            )
        except Exception as exc:
            logging.debug("Duplicata %s nao pode ser removida: %s", duplicate, exc)
        with self._lock:
            mapping = self._data.setdefault("category_directories", {})
            for key, value in list(mapping.items()):
                if value == duplicate.name:
                    mapping[key] = target.name
            # Ensure canonical category also points to target
            if category in mapping and mapping[category] != target.name:
                mapping[category] = target.name
            # Update categories list capitalization if needed
            categories = self._data.setdefault("categories", [])
            if category not in categories:
                categories.append(category)

    def _consolidate_category_directories(self) -> None:
        if not self.category_root:
            return
        with self._lock:
            categories = list(self._data.get("categories", []))
            mapping = self._data.setdefault("category_directories", {})
        normalized_to_category = {_normalize_label(cat): cat for cat in categories}
        children = [child for child in self.category_root.iterdir() if child.is_dir()]
        primary_for_normalized: Dict[str, Path] = {}
        for child in children:
            meta = self._read_category_metadata(child)
            recorded_name = meta.get("name") if meta else None
            fallback_name = child.name.replace("_", " ")
            normalized = _normalize_label(recorded_name or fallback_name)
            category = normalized_to_category.get(normalized) or (recorded_name or fallback_name)
            if normalized not in primary_for_normalized:
                primary_for_normalized[normalized] = child
                # ensure mapping uses the canonical slug
                with self._lock:
                    mapping.setdefault(category, child.name)
            else:
                target = primary_for_normalized[normalized]
                self._merge_category_directory(category, target, child)

    def _default_feedback_stats(self) -> Dict[str, Any]:
        return {
            "positive": 0,
            "negative": 0,
            "neutral": 0,
            "confidence_sum": 0.0,
            "confidence_count": 0,
            "reprocess_requests": 0,
            "knowledge_approvals": 0,
            "knowledge_rejections": 0,
            "keywords_promoted": {},
            "keywords_flagged": {},
            "last_update": _timestamp(),
        }

    def _ensure_category_feedback_stats(self, category: str) -> Dict[str, Any]:
        feedback_map = self._data.setdefault("category_feedback", {})
        stats = feedback_map.get(category)
        if stats is None:
            stats = self._default_feedback_stats()
            feedback_map[category] = stats
        return stats

    def _parse_list_field(self, value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, list):
            items = [str(item).strip() for item in value]
        else:
            text = str(value)
            separators = [",", ";", "|"]
            for sep in separators[1:]:
                text = text.replace(sep, separators[0])
            text = text.replace("\n", separators[0])
            items = [item.strip() for item in text.split(separators[0])]
        return [item for item in items if item]

    def _parse_numeric(self, value: Any) -> Optional[float]:
        if value is None or value == "":
            return None
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip().replace("%", "").replace(",", ".")
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None

    def _to_bool(self, value: Any) -> Optional[bool]:
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"", "none", "n/a"}:
            return None
        if text in {"true", "1", "yes", "sim", "y", "s"}:
            return True
        if text in {"false", "0", "no", "nao", "n+uo", "n"}:
            return False
        return None

    def _adjust_category_keywords(
        self,
        category: str,
        additions: Optional[List[str]] = None,
        removals: Optional[List[str]] = None,
    ) -> None:
        additions = additions or []
        removals = removals or []
        if not additions and not removals:
            return
        normalized_additions = []
        for token in additions:
            normalized = _normalize_token(token)
            if normalized:
                normalized_additions.append(normalized)
        normalized_removals = []
        for token in removals:
            normalized = _normalize_token(token)
            if normalized:
                normalized_removals.append(normalized)
        if not normalized_additions and not normalized_removals:
            return
        with self._lock:
            keyword_map = self._data.setdefault("category_keywords", {})
            cat_keywords = keyword_map.setdefault(category, {})
            for token in normalized_additions:
                new_weight = round(cat_keywords.get(token, 0.0) + 1.0, 4)
                cat_keywords[token] = new_weight
                logging.info(
                    "Keyword %s manually reinforced for category %s (weight=%.2f).",
                    token,
                    category,
                    new_weight,
                )
            for token in normalized_removals:
                if token in cat_keywords:
                    new_weight = round(max(0.0, cat_keywords[token] - 1.0), 4)
                    if new_weight == 0.0:
                        cat_keywords.pop(token, None)
                        logging.info(
                            "Palavra-chave %s removida da categoria %s por feedback.",
                            token,
                            category,
                        )
                    else:
                        cat_keywords[token] = new_weight
                        logging.info(
                            "Palavra-chave %s ajustada para categoria %s (novo peso=%.2f).",
                            token,
                            category,
                            new_weight,
                        )

    def _feedback_modifier(self, entry: Dict[str, Any]) -> float:
        feedback = entry.get("feedback", {}) or {}
        positive = feedback.get("positivo", 0)
        negative = feedback.get("negativo", 0)
        total = positive + negative
        modifier = 1.0
        if total:
            modifier = max(0.25, (positive + 1) / (total + 2))
        if entry.get("needs_reprocess"):
            modifier *= 0.6
        if entry.get("knowledge_approved") is False:
            modifier *= 0.5
        return round(modifier, 4)

    def _provision_category_directory(self, category: str) -> Optional[Path]:
        if not self.category_root:
            return None
        duplicates: List[Path] = []
        with self._lock:
            mapping = self._data.setdefault("category_directories", {})
            folder_name = mapping.get(category)
            normalized_category = _normalize_label(category)
            if folder_name:
                target = self.category_root / folder_name
            else:
                target = None
                for child in self.category_root.iterdir():
                    if not child.is_dir():
                        continue
                    meta = self._read_category_metadata(child)
                    recorded_name = meta.get("name") if meta else None
                    fallback_name = child.name.replace("_", " ")
                    normalized_recorded = _normalize_label(recorded_name) if recorded_name else ""
                    normalized_fallback = _normalize_label(fallback_name)
                    if normalized_recorded == normalized_category or (not recorded_name and normalized_fallback == normalized_category):
                        target = child
                        break
                if target is None:
                    base_slug = self._slugify(category)
                    target = self.category_root / base_slug
                    suffix = 2
                    while target.exists():
                        meta = self._read_category_metadata(target)
                        recorded_name = meta.get("name") if meta else None
                        fallback_name = target.name.replace("_", " ")
                        if not meta or _normalize_label(recorded_name or fallback_name) == normalized_category:
                            break
                        target = self.category_root / f"{base_slug}_{suffix}"
                        suffix += 1
                mapping[category] = target.name
                self._data.setdefault("category_documents", {}).setdefault(category, self._empty_category_doc_state())
            target.mkdir(parents=True, exist_ok=True)
            meta_path = self._category_metadata_path(target)
            metadata = self._read_category_metadata(target)
            if _normalize_label(metadata.get("name")) != _normalize_label(category):
                metadata["name"] = category
                metadata["updated_at"] = _timestamp()
            if "created_at" not in metadata:
                metadata["created_at"] = _timestamp()
            with open(meta_path, "w", encoding="utf-8") as handler:
                json.dump(metadata, handler, indent=2, ensure_ascii=False)

            normalized_target = _normalize_label(metadata.get("name", category))
            for child in self.category_root.iterdir():
                if not child.is_dir() or child == target:
                    continue
                meta = self._read_category_metadata(child)
                recorded_name = meta.get("name") if meta else None
                fallback_name = child.name.replace("_", " ")
                if _normalize_label(recorded_name or fallback_name) == normalized_target:
                    duplicates.append(child)

        for duplicate in duplicates:
            self._merge_category_directory(category, target, duplicate)

        return target

    def ensure_category_directory(self, category: str) -> Optional[Path]:
        """Public helper to guarantee that the knowledge folder for a category exists."""
        return self._provision_category_directory(category)

    def _digest_file(self, path: Path) -> str:
        hasher = hashlib.sha256()
        with open(path, "rb") as handler:
            for chunk in iter(lambda: handler.read(8192), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    def _load_document_text(self, path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix == ".txt":
            try:
                return path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                return path.read_text(encoding="latin-1")
            except Exception as exc:
                logging.error("Falha ao ler TXT %s: %s", path, exc)
                return ""
        if suffix == ".pdf":
            if fitz is None:
                logging.error(
                    "PyMuPDF (fitz) is not installed. Skipping knowledge PDF: %s",
                    path,
                )
                return ""
            try:  # pragma: no cover - depende de biblioteca opcional
                with fitz.open(path) as doc:
                    return "\n".join(page.get_text("text") for page in doc)
            except Exception as exc:
                logging.error("Erro ao extrair texto de PDF %s: %s", path, exc)
                return ""
        if suffix == ".docx":
            if Document is None:
                logging.error(
                    "python-docx is not installed. Skipping knowledge DOCX: %s",
                    path,
                )
                return ""
            try:  # pragma: no cover - depende de biblioteca opcional
                document = Document(path)
                return "\n".join(p.text for p in document.paragraphs if p.text.strip())
            except Exception as exc:
                logging.error("Erro ao extrair texto de DOCX %s: %s", path, exc)
                return ""
        logging.warning(
            "Extension %s is not supported for knowledge documents (%s).",
            suffix,
            path,
        )
        return ""

    def _scan_category_directory(self, category: str, directory: Path) -> bool:
        if not directory.exists():
            logging.warning(
                "Knowledge folder %s not found for category %s.",
                directory,
                category,
            )
            return False
        doc_state = self._data.setdefault("category_documents", {}).setdefault(
            category, self._empty_category_doc_state()
        )
        processed_files: Dict[str, Dict] = doc_state.setdefault("processed_files", {})
        updated = False

        for file_path in directory.iterdir():
            if file_path.is_dir():
                continue
            if file_path.suffix.lower() not in SUPPORTED_KNOWLEDGE_EXTENSIONS:
                continue
            rel_key = file_path.name
            digest = self._digest_file(file_path)
            existing = processed_files.get(rel_key)
            if existing and existing.get("hash") == digest:
                continue
            text = self._load_document_text(file_path)
            if not text.strip():
                logging.warning(
                    "Knowledge file %s is empty or has no relevant text.",
                    file_path,
                )
                continue
            tokens = _tokens_from_text(text, limit=120)
            if not tokens:
                logging.warning(
                    "Could not derive tokens for knowledge file %s. File ignored.",
                    file_path,
                )
                continue
            processed_files[rel_key] = {
                "hash": digest,
                "tokens": tokens,
                "size": file_path.stat().st_size,
                "word_count": len(text.split()),
                "source_name": file_path.name,
                "relative_path": rel_key,
                "updated_at": _timestamp(),
            }
            logging.info(
                "Conhecimento documental processado para categoria %s: %s",
                category,
                file_path.name,
            )
            updated = True

        existing_keys = set(processed_files)
        current_keys = {item.name for item in directory.iterdir() if item.is_file()}
        removed_keys = existing_keys - current_keys
        for key in removed_keys:
            processed_files.pop(key, None)
            logging.info(
                "Registro de conhecimento removido para categoria %s (arquivo ausente): %s",
                category,
                key,
            )
            updated = True

        if updated:
            aggregation: Counter = Counter()
            for info in processed_files.values():
                for token, weight in info.get("tokens", {}).items():
                    aggregation[token] += weight
            if aggregation:
                most_common = aggregation.most_common(120)
                max_weight = most_common[0][1]
                doc_state["aggregated_tokens"] = {
                    token: round(weight / max_weight, 4) for token, weight in most_common
                }
                doc_state["top_terms"] = [token for token, _ in most_common[:20]]
            else:
                doc_state["aggregated_tokens"] = {}
                doc_state["top_terms"] = []
            doc_state["document_count"] = len(processed_files)
            doc_state["last_scan"] = _timestamp()
            logging.info(
                "Categoria %s: conhecimento documental consolidado (%s arquivos, termos principais=%s).",
                category,
                doc_state["document_count"],
                ", ".join(doc_state.get("top_terms", [])[:5]) or "nenhum",
            )
        return updated

    def refresh_category_documents(self) -> None:
        if not self.category_root:
            return
        with self._category_scan_lock:
            if not self.category_root.exists():
                self.category_root.mkdir(parents=True, exist_ok=True)
            self._consolidate_category_directories()
            directories = [item for item in self.category_root.iterdir() if item.is_dir()]
            if not directories:
                logging.debug(
                    "Nenhuma pasta de conhecimento categorico localizada em %s.",
                    self.category_root,
                )
                return
            updated = False
            mapping = self._data.setdefault("category_directories", {})
            valid_folder_names = set()
            for directory in directories:
                meta = self._read_category_metadata(directory)
                category_label = meta.get("name") or directory.name.replace("_", " ")
                canonical = self._ensure_category(category_label)
                valid_folder_names.add(directory.name)
                if mapping.get(canonical) != directory.name:
                    mapping[canonical] = directory.name
                    updated = True
                if self._scan_category_directory(canonical, directory):
                    updated = True
            # Clean up mapping entries pointing to removed folders
            obsolete = [
                category
                for category, folder in mapping.items()
                if folder not in valid_folder_names
            ]
            for category in obsolete:
                logging.warning(
                    "Knowledge folder for category %s not found. Removing association.",
                    category,
                )
                mapping.pop(category, None)
                self._data.get("category_documents", {}).pop(category, None)
                updated = True
            if updated:
                logging.info("Knowledge base documental atualizado a partir das pastas por categoria.")
                self._write()


    def refresh(self) -> None:
        """Reload knowledge data from disk."""
        self._load()

    def start_periodic_refresh(self, interval_seconds: int) -> Optional[KnowledgeBaseRefresher]:
        """Start a background refresher that scans knowledge folders periodically."""
        if interval_seconds <= 0:
            return None
        refresher = KnowledgeBaseRefresher(self, interval_seconds)
        refresher.start()
        return refresher

    def _ensure_category(self, category: str) -> str:
        candidate = (category or "").strip() or "outros"
        normalized_candidate = _normalize_label(candidate)
        with self._lock:
            categories = self._data.setdefault("categories", [])
            for existing in categories:
                if _normalize_label(existing) == normalized_candidate:
                    self._provision_category_directory(existing)
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
                self._provision_category_directory(best_match)
                return best_match
            categories.append(candidate)
            logging.info("Nova categoria registrada na base de conhecimento: %s", candidate)
            self._provision_category_directory(candidate)
            self._write()
            return candidate

    def _resolve_category_from_slug(self, slug: str) -> Optional[str]:
        normalized = (slug or "").strip().lower()
        if not normalized:
            return None
        with self._lock:
            categories = list(self._data.get("categories", []))
        for category in categories:
            if _slugify_category_name(category) == normalized:
                return category
        return None

    def _register_feedback_evidence(
        self,
        category: str,
        snippet: str,
        source_file: str,
    ) -> Optional[Path]:
        cleaned = (snippet or "").strip()
        if len(cleaned) < 10:
            logging.debug(
                "Trecho de feedback muito curto para registrar na categoria %s.",
                category,
            )
            return None
        normalized_snippet = " ".join(cleaned.split())
        if len(normalized_snippet) > 2000:
            normalized_snippet = normalized_snippet[:2000]
        canonical_category = self._ensure_category(category)
        directory = self.ensure_category_directory(canonical_category)
        if directory is None:
            logging.warning(
                "Nao foi possivel garantir pasta documental para categoria %s.",
                canonical_category,
            )
            return None
        digest = hashlib.sha256(normalized_snippet.encode("utf-8")).hexdigest()[:12]
        file_name = f"feedback_{digest}.txt"
        path = directory / file_name
        if not path.exists():
            header = f"# Fonte: {source_file}\n# Registrado em: {_timestamp()}\n\n"
            try:
                with open(path, "w", encoding="utf-8") as handler:
                    handler.write(header + normalized_snippet + "\n")
            except OSError as exc:
                logging.error(
                    "Falha ao gravar evidencias de feedback em %s: %s",
                    path,
                    exc,
                )
                return None
            logging.info(
                "Evidencia documental adicionada via feedback: categoria=%s arquivo=%s",
                canonical_category,
                path.name,
            )
        else:
            logging.debug(
                "Evidencia de feedback ja existente para categoria %s (arquivo=%s).",
                canonical_category,
                path.name,
            )
        with self._category_scan_lock:
            self._scan_category_directory(canonical_category, directory)
            self._write()
        return path

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
        self,
        file_name: str,
        status: str,
        observations: str,
        new_category: Optional[str] = None,
        extras: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict]:
        """Update feedback counters for a given file name and register learning signals."""
        extras = extras or {}
        normalized_status = (status or "").strip().lower()
        positive_status = {"correto", "correta", "ok", "aprovado", "positivo"}
        negative_status = {"incorreto", "incorreta", "rever", "ajustar", "negativo", "reanalise"}
        neutral_status = {"neutro", "avaliar", "pendente"}
        if not normalized_status:
            normalized_status = "correto"
        if (
            normalized_status not in positive_status
            and normalized_status not in negative_status
            and normalized_status not in neutral_status
        ):
            logging.warning("Feedback status %s is not recognized. Assuming 'correto'.", normalized_status)
            normalized_status = "correto"

        with self._lock:
            entries = self._data.get("entries", [])
            target_entry = None
            for entry in entries:
                if entry.get("file_name") == file_name:
                    target_entry = entry
                    break
            if target_entry is None:
                logging.warning("No knowledge entry found for feedback file %s", file_name)
                return None

            feedback = target_entry.setdefault("feedback", {"positivo": 0, "negativo": 0, "neutro": 0})
            if normalized_status in positive_status:
                feedback["positivo"] = feedback.get("positivo", 0) + 1
            elif normalized_status in negative_status:
                feedback["negativo"] = feedback.get("negativo", 0) + 1
            else:
                feedback["neutro"] = feedback.get("neutro", 0) + 1

            category_before = target_entry.get("category") or "outros"
            category_after = category_before
            if new_category:
                normalized_input = new_category.strip()
                if normalized_input:
                    resolved_category = self._ensure_category(normalized_input)
                    if resolved_category != category_before:
                        logging.info(
                            "Categoria ajustada por feedback: %s -> %s",
                            category_before,
                            resolved_category,
                        )
                        target_entry["category"] = resolved_category
                        category_after = resolved_category

            stats = self._ensure_category_feedback_stats(category_after)
            if normalized_status in positive_status:
                stats["positive"] += 1
            elif normalized_status in negative_status:
                stats["negative"] += 1
            else:
                stats["neutral"] += 1
            stats["last_update"] = _timestamp()

            confidence_override = self._parse_numeric(extras.get("confidence_override"))
            if confidence_override is not None:
                confidence_ratio = confidence_override / 100.0 if confidence_override > 1.0 else confidence_override
                confidence_ratio = max(0.0, min(1.0, confidence_ratio))
                target_entry["confidence"] = round(confidence_ratio, 4)
                target_entry["confidence_percent"] = round(confidence_ratio * 100.0, 2)
                stats["confidence_sum"] += confidence_ratio * 100.0
                stats["confidence_count"] += 1
                logging.info(
                    "Confidence manually adjusted for %s -> %.2f%%",
                    file_name,
                    confidence_ratio * 100.0,
                )

            areas_feedback = self._parse_list_field(extras.get("areas_secundarias"))
            if areas_feedback:
                existing_areas = target_entry.setdefault("areas_secundarias", [])
                for area in areas_feedback:
                    normalized_area = area.strip()
                    if not normalized_area:
                        continue
                    if normalized_area not in existing_areas:
                        existing_areas.append(normalized_area)
                        logging.info(
                            "Area secundaria %s adicionada ao documento %s via feedback.",
                            normalized_area,
                            file_name,
                        )

            positive_keywords = self._parse_list_field(extras.get("keywords_positive"))
            negative_keywords = self._parse_list_field(extras.get("keywords_negative"))
            if positive_keywords or negative_keywords:
                self._adjust_category_keywords(category_after, positive_keywords, negative_keywords)
                keyword_promotions = stats.setdefault("keywords_promoted", {})
                for token in positive_keywords:
                    normalized = _normalize_token(token)
                    if normalized:
                        keyword_promotions[normalized] = keyword_promotions.get(normalized, 0) + 1
                keyword_flags = stats.setdefault("keywords_flagged", {})
                for token in negative_keywords:
                    normalized = _normalize_token(token)
                    if normalized:
                        keyword_flags[normalized] = keyword_flags.get(normalized, 0) + 1

            approve_for_knowledge = self._to_bool(extras.get("approve_for_knowledge"))
            if approve_for_knowledge is True:
                stats["knowledge_approvals"] += 1
                target_entry["knowledge_approved"] = True
            elif approve_for_knowledge is False:
                stats["knowledge_rejections"] += 1
                target_entry["knowledge_approved"] = False

            request_reanalysis = self._to_bool(extras.get("request_reanalysis"))
            if request_reanalysis:
                stats["reprocess_requests"] += 1
                target_entry["needs_reprocess"] = True
                logging.info(
                    "Document %s scheduled for future reanalysis (category %s).",
                    file_name,
                    category_after,
                )

            motivadores = self._parse_list_field(extras.get("motivos_relevantes"))
            if motivadores:
                target_entry.setdefault("feedback_motivos", [])
                target_entry["feedback_motivos"].extend(motivadores)

            bloqueios = self._parse_list_field(extras.get("motivos_criticos"))
            if bloqueios:
                target_entry.setdefault("feedback_alertas", [])
                target_entry["feedback_alertas"].extend(bloqueios)

            category_feedback_extras = extras.get("category_feedback")
            if isinstance(category_feedback_extras, dict):
                applied_feedback: List[Dict[str, Any]] = []
                for slug, payload in category_feedback_extras.items():
                    if not isinstance(payload, dict):
                        continue
                    label = str(payload.get("label") or "").strip()
                    resolved = self._resolve_category_from_slug(slug)
                    if label:
                        label_slug = _slugify_category_name(label)
                        resolved_label = self._resolve_category_from_slug(label_slug)
                        if resolved_label:
                            resolved = resolved_label
                        else:
                            resolved = self._ensure_category(label)
                    if not resolved:
                        fallback = label or slug.replace("_", " ")
                        resolved = self._ensure_category(fallback)
                    selected_flag = self._to_bool(payload.get("selected"))
                    include_flag = self._to_bool(payload.get("include"))
                    evidence_text = str(payload.get("evidence") or "").strip()
                    snippet_path: Optional[Path] = None
                    if selected_flag is True:
                        existing_areas = target_entry.setdefault("areas_secundarias", [])
                        if resolved not in existing_areas:
                            existing_areas.append(resolved)
                            logging.info(
                                "Area secundaria %s adicionada ao documento %s via feedback detalhado.",
                                resolved,
                                file_name,
                            )
                    if include_flag and evidence_text:
                        snippet_path = self._register_feedback_evidence(
                            category=resolved,
                            snippet=evidence_text,
                            source_file=file_name,
                        )
                        if snippet_path:
                            extra_stats = self._ensure_category_feedback_stats(resolved)
                            extra_stats["knowledge_approvals"] += 1
                            logging.info(
                                "Trecho de feedback armazenado para categoria %s a partir de %s.",
                                resolved,
                                snippet_path.name,
                            )
                    applied_feedback.append(
                        {
                            "slug": slug,
                            "label": label or None,
                            "resolved": resolved,
                            "selected": selected_flag,
                            "include": include_flag,
                            "snippet": str(snippet_path) if snippet_path else None,
                        }
                    )
                if applied_feedback:
                    extras["category_feedback_applied"] = applied_feedback

            target_entry["updated_at"] = _timestamp()

            history_item = {
                "file_name": file_name,
                "status": normalized_status,
                "observations": observations,
                "timestamp": _timestamp(),
                "new_category": new_category,
                "category_before": category_before,
                "category_after": category_after,
                "extras": extras,
            }
            self._data.setdefault("feedback_history", []).append(history_item)
            self._write()
            logging.info(
                "Feedback recorded for %s (status=%s, category=%s). Observacoes: %s",
                file_name,
                normalized_status,
                category_after,
                observations or "sem observacoes",
            )
            return target_entry

    def find_similar(self, raw_text: str, top_n: int = 3) -> List[Tuple[Dict, float]]:
        """Find similar entries based on lightweight embeddings."""
        target_tokens = _tokens_from_text(raw_text)
        with self._lock:
            entries = self._data.get("entries", [])
        scored: List[Tuple[Dict, float]] = []
        for entry in entries:
            score = cosine_similarity(target_tokens, entry.get("tokens", {}))
            if score <= 0:
                continue
            score *= self._feedback_modifier(entry)
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

    def category_document_profiles(self, top_n: int = 12) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            documents = json.loads(
                json.dumps(self._data.get("category_documents", {}))
            )
            directories = dict(self._data.get("category_directories", {}))
            category_root = str(self.category_root) if self.category_root else None
        profiles: Dict[str, Dict[str, Any]] = {}
        for category, payload in documents.items():
            processed = payload.get("processed_files", {}) or {}
            recent_sorted = sorted(
                processed.values(),
                key=lambda item: item.get("updated_at", ""),
                reverse=True,
            )
            recent_docs = [
                item.get("source_name") or item.get("relative_path")
                for item in recent_sorted[:5]
            ]
            folder_name = directories.get(category)
            folder_path = None
            if category_root and folder_name:
                folder_path = str(Path(category_root) / folder_name)
            profiles[category] = {
                "top_terms": payload.get("top_terms", [])[:top_n],
                "document_count": payload.get("document_count", len(processed)),
                "recent_documents": recent_docs,
                "last_scan": payload.get("last_scan"),
                "folder": folder_path,
            }
        return profiles

    def category_feedback_profile(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            feedback_map = json.loads(
                json.dumps(self._data.get("category_feedback", {}))
            )
        profile: Dict[str, Dict[str, Any]] = {}
        for category, stats in feedback_map.items():
            positive = stats.get("positive", 0)
            negative = stats.get("negative", 0)
            neutral = stats.get("neutral", 0)
            total = positive + negative + neutral
            approval_ratio = round(positive / total, 4) if total else 0.0
            confidence_count = stats.get("confidence_count", 0) or 0
            confidence_avg = 0.0
            if confidence_count:
                confidence_avg = round(stats.get("confidence_sum", 0.0) / confidence_count, 2)
            promoted = stats.get("keywords_promoted") or {}
            flagged = stats.get("keywords_flagged") or {}
            profile[category] = {
                "positive": positive,
                "negative": negative,
                "neutral": neutral,
                "approval_ratio": approval_ratio,
                "confidence_avg": confidence_avg,
                "confidence_samples": confidence_count,
                "reprocess_requests": stats.get("reprocess_requests", 0),
                "knowledge_approvals": stats.get("knowledge_approvals", 0),
                "knowledge_rejections": stats.get("knowledge_rejections", 0),
                "last_update": stats.get("last_update"),
                "keywords_promoted": sorted(promoted.items(), key=lambda item: item[1], reverse=True)[:12],
                "keywords_flagged": sorted(flagged.items(), key=lambda item: item[1], reverse=True)[:12],
            }
        return profile

    def document_knowledge_match(self, raw_text: str, top_n: int = 5) -> List[Dict[str, float]]:
        tokens = _tokens_from_text(raw_text)
        if not tokens:
            return []
        with self._lock:
            documents = json.loads(
                json.dumps(self._data.get("category_documents", {}))
            )
        scored: List[Dict[str, float]] = []
        for category, payload in documents.items():
            aggregated = payload.get("aggregated_tokens") or {}
            if not aggregated:
                continue
            score = cosine_similarity(tokens, aggregated)
            if score <= 0:
                continue
            scored.append(
                {
                    "category": category,
                    "score": round(score, 4),
                    "document_count": payload.get("document_count", 0),
                    "top_terms": payload.get("top_terms", [])[:8],
                }
            )
        scored.sort(key=lambda item: item["score"], reverse=True)
        return scored[:top_n]

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
            score *= self._feedback_modifier(entry)
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
