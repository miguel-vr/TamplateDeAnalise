import json
import logging
import os
import shutil
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from core.knowledge_base import KnowledgeBase
from core.processor import DocumentProcessor


class JsonEventLogger:
    """Gravo os eventos estruturados em JSONL para auditar o pipeline sem sofrer com grep depois."""

    def __init__(self, log_path: Path):
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def emit(self, event_type: str, payload: Dict) -> None:
        record = {"type": event_type, "timestamp": time.time(), "payload": payload}
        with self._lock:
            with open(self.log_path, "a", encoding="utf-8") as handler:
                handler.write(json.dumps(record, ensure_ascii=False) + "\n")


class DirectoryWatcher(threading.Thread):
    """Basic polling watcher that captures new files and triggers the callback."""

    def __init__(self, name: str, directory: Path, interval: int, callback: Callable[[Path], None], logger: JsonEventLogger):
        super().__init__(daemon=True, name=name)
        self.directory = directory
        self.interval = interval
        self.callback = callback
        self.logger = logger
        self._stop_event = threading.Event()
        self._seen: Set[str] = set()

    def run(self) -> None:
        logging.info("Watcher '%s' iniciado monitorando %s", self.name, self.directory)
        while not self._stop_event.is_set():
            self.poll_once()
            time.sleep(self.interval)

    def poll_once(self) -> None:
        logging.debug("Watcher %s escaneando %s", self.name, self.directory)
        found_new = False
        try:
            for file in self.directory.iterdir():
                if not file.is_file():
                    continue
                if file.name.startswith("~$"):
                    continue
                if file.name not in self._seen:
                    found_new = True
                    self._seen.add(file.name)
                    logging.info("Watcher %s detectou novo arquivo: %s", self.name, file.name)
                    self.logger.emit(
                        "detected",
                        {"watcher": self.name, "file": file.name, "path": str(file)},
                    )
                    self._handle_file(file)
        except Exception as exc:
            logging.error("Erro no watcher %s: %s", self.name, exc)
        if not found_new:
            logging.debug("Watcher %s sem novos arquivos em %s", self.name, self.directory)

    def _handle_file(self, file: Path) -> None:
        try:
            logging.info("Iniciando processamento do arquivo %s via callback do watcher %s", file.name, self.name)
            self.callback(file)
            logging.info("Callback concluido para %s no watcher %s", file.name, self.name)
        except Exception as exc:  # pragma: no cover - runtime
            logging.exception("Falha ao processar arquivo %s no watcher %s: %s", file, self.name, exc)

    def stop(self) -> None:
        self._stop_event.set()


class IntakeWatcher:
    """Fico de olho na pasta de entrada, movo os arquivos para processamento e aciono o executor paralelo."""

    def __init__(
        self,
        entrada_dir: Path,
        processamento_dir: Path,
        processor: DocumentProcessor,
        interval: int,
        logger: JsonEventLogger,
        max_workers: int = 2,
    ):
        self.entrada_dir = entrada_dir
        self.processamento_dir = processamento_dir
        self.processor = processor
        self.interval = interval
        self.logger = logger
        self.max_workers = max(1, int(max_workers))
        self._executor = ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix="processor-worker",
        )
        self._active_tasks: Dict[str, Dict[str, float]] = {}
        self._thread = DirectoryWatcher(
            name="entrada-watcher",
            directory=self.entrada_dir,
            interval=self.interval,
            callback=self._on_new_file,
            logger=self.logger,
        )

    def start(self) -> None:
        logging.info(
            "IntakeWatcher iniciado com %s worker(s) paralelos para processamento.",
            self.max_workers,
        )
        self._thread.start()

    def stop(self) -> None:
        self._thread.stop()
        self._thread.join(timeout=5)
        logging.info("Aguardando conclusao das tarefas em andamento (%s).", len(self._active_tasks))
        self._executor.shutdown(wait=True)
        self._active_tasks.clear()

    def _on_new_file(self, file_path: Path) -> None:
        target = self.processamento_dir / file_path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            logging.info("Movendo arquivo %s para area de processamento %s", file_path.name, target)
            shutil.move(str(file_path), target)
            self.logger.emit(
                "moved_to_processing",
                {"original": str(file_path), "destination": str(target)},
            )
            logging.info("Arquivo %s movido com sucesso. Iniciando pipeline de analise.", file_path.name)
            self._log_processing_folder_state("apos_movimentacao")
            size_bytes = target.stat().st_size if target.exists() else 0
            self._submit_for_processing(target, size_bytes)
        except Exception as exc:
            logging.exception("Erro ao mover/processar %s: %s", file_path, exc)
            self.logger.emit(
                "processing_error",
                {"file": str(file_path), "error": str(exc)},
            )

    def _submit_for_processing(self, target: Path, size_bytes: int) -> None:
        processing_id = uuid.uuid4().hex[:12]
        enqueued_at = time.time()
        payload = {
            "processing_id": processing_id,
            "file": target.name,
            "path": str(target),
            "queue_depth": len(self._active_tasks) + 1,
            "size_bytes": size_bytes,
        }
        self.logger.emit("processing_enqueued", payload)
        logging.info(
            "[%s] Arquivo %s enfileirado para processamento. Tarefas ativas: %s",
            processing_id,
            target.name,
            len(self._active_tasks) + 1,
        )
        if self.processor.teams_notifier:
            try:
                self.processor.teams_notifier.send_activity_event(
                    title="Documento recebido",
                    message=f"{target.name} foi recebido e enfileirado para processamento.",
                    facts=[
                        ("Processo", processing_id),
                        ("Arquivo", target.name),
                        ("Destino", str(target)),
                        ("Tamanho", f"{size_bytes} bytes"),
                        ("Fila atual", str(len(self._active_tasks) + 1)),
                    ],
                    event_type="intake_received",
                )
            except Exception as exc:  # pragma: no cover - notificacoes
                logging.debug("Falha ao enviar notificacao de recebimento: %s", exc)
        future = self._executor.submit(self.processor.process_file, str(target), processing_id)
        self._active_tasks[processing_id] = {"started_at": enqueued_at, "file": target.name}
        future.add_done_callback(lambda fut, pid=processing_id: self._on_processing_done(pid, fut))

    def _on_processing_done(self, processing_id: str, future: Future) -> None:
        task_info = self._active_tasks.pop(processing_id, {"started_at": time.time(), "file": "desconhecido"})
        duration = time.time() - task_info.get("started_at", time.time())
        file_name = task_info.get("file", "desconhecido")
        if future.cancelled():
            logging.warning("[%s] Processamento cancelado para %s apos %.2fs.", processing_id, file_name, duration)
            self.logger.emit(
                "processing_cancelled",
                {"processing_id": processing_id, "file": file_name, "duration": duration},
            )
        elif future.exception():
            exc = future.exception()
            logging.error(
                "[%s] Processamento falhou para %s apos %.2fs: %s",
                processing_id,
                file_name,
                duration,
                exc,
            )
            self.logger.emit(
                "processing_failed",
                {
                    "processing_id": processing_id,
                    "file": file_name,
                    "duration": duration,
                    "error": str(exc),
                },
            )
        else:
            logging.info(
                "[%s] Pipeline concluido para %s em %.2fs.",
                processing_id,
                file_name,
                duration,
            )
            self.logger.emit(
                "processing_completed",
                {
                    "processing_id": processing_id,
                    "file": file_name,
                    "duration": duration,
                    "artifact": str(future.result()) if future.result() else None,
                },
            )
        self._log_processing_folder_state("apos_conclusao")

    def _log_processing_folder_state(self, motivo: str) -> None:
        try:
            arquivos = sorted(f.name for f in self.processamento_dir.iterdir() if f.is_file())
        except FileNotFoundError:
            arquivos = []
        logging.info(
            "Estado da pasta em_processamento (%s): %s arquivo(s) %s",
            motivo,
            len(arquivos),
            f"- {', '.join(arquivos[:6])}" if arquivos else "",
        )
        self.logger.emit(
            "processing_folder_state",
            {"motivo": motivo, "count": len(arquivos), "files": arquivos[:20]},
        )


class FeedbackWatcher:
    """Leio o feedback humano, atualizo a base de conhecimento e arquivo o que ja foi tratado."""

    def __init__(
        self,
        feedback_dir: Path,
        processed_feedback_dir: Path,
        knowledge_base: KnowledgeBase,
        interval: int,
        logger: JsonEventLogger,
    ):
        self.feedback_dir = feedback_dir
        self.processed_dir = processed_feedback_dir
        self.knowledge_base = knowledge_base
        self.interval = interval
        self.logger = logger
        self._thread = DirectoryWatcher(
            name="feedback-watcher",
            directory=self.feedback_dir,
            interval=self.interval,
            callback=self._handle_feedback,
            logger=self.logger,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._thread.stop()
        self._thread.join(timeout=5)

    def _handle_feedback(self, file_path: Path) -> None:
        suffix = file_path.suffix.lower()
        if suffix not in {".txt", ".json"}:
            logging.info("Arquivo de feedback ignorado (extensao nao suportada): %s", file_path.name)
            return
        logging.info("Processando feedback do arquivo %s", file_path.name)
        data = self._load_feedback_payload(file_path)
        if not data:
            logging.warning("Nao foi possivel interpretar feedback em %s", file_path.name)
            return
        self.logger.emit("feedback_received", {"file": file_path.name, "data": data})
        extras = data.get("extras") if isinstance(data.get("extras"), dict) else {}
        entry = self.knowledge_base.update_entry_feedback(
            file_name=data["documento"],
            status=data["status"],
            observations=data.get("observacoes", ""),
            new_category=data.get("nova_categoria"),
            extras=extras,
        )
        if entry:
            logging.info(
                "Feedback aplicado para %s (status=%s, nova_categoria=%s, extras=%s)",
                data["documento"],
                data.get("status"),
                data.get("nova_categoria"),
                extras,
            )
            self.logger.emit(
                "feedback_applied",
                {
                    "file": file_path.name,
                    "documento": data["documento"],
                    "status": data.get("status"),
                    "nova_categoria": data.get("nova_categoria"),
                    "extras": extras,
                },
            )
        else:
            logging.warning("Feedback nao aplicado para %s - entrada nao encontrada.", data["documento"])
            self.logger.emit(
                "feedback_missing_entry",
                {"file": file_path.name, "documento": data["documento"]},
            )
        archive_category = extras.get("categoria_feedback")
        if entry and not archive_category:
            archive_category = entry.get("category")
        target_folder = self.processed_dir
        if archive_category:
            slug = self._slugify_category(str(archive_category))
            target_folder = self.processed_dir / slug
        target_folder.mkdir(parents=True, exist_ok=True)
        destination = target_folder / file_path.name
        shutil.move(str(file_path), destination)
        logging.info("Feedback %s arquivado em %s", file_path.name, destination)
        if archive_category:
            self.logger.emit(
                "feedback_archived",
                {
                    "file": file_path.name,
                    "categoria": archive_category,
                    "path": str(destination),
                },
            )

    def _load_feedback_payload(self, file_path: Path) -> Optional[Dict]:
        suffix = file_path.suffix.lower()
        if suffix == ".json":
            return self._parse_feedback_json(file_path)
        return self._parse_feedback_file(file_path)

    def _parse_feedback_file(self, file_path: Path) -> Optional[Dict]:
        lines = self._read_feedback_lines(file_path)
        if not lines:
            return None

        raw_text = "\n".join(line.strip() for line in lines).strip()
        if raw_text.startswith("{"):
            try:
                data = json.loads(raw_text)
            except json.JSONDecodeError:
                logging.debug("Feedback JSON invalido em %s", file_path.name)
            else:
                normalized = self._normalize_feedback_dict(data, file_path)
                if normalized:
                    return normalized

        structured = self._parse_key_value_feedback(lines, file_path)
        if structured:
            return structured

        return self._parse_checkbox_feedback(lines, file_path)

    def _parse_feedback_json(self, file_path: Path) -> Optional[Dict]:
        try:
            with open(file_path, "r", encoding="utf-8") as handler:
                data = json.load(handler)
        except UnicodeDecodeError:
            with open(file_path, "r", encoding="latin-1") as handler:
                data = json.load(handler)
        except json.JSONDecodeError as exc:
            logging.error("JSON de feedback invalido em %s: %s", file_path.name, exc)
            return None
        except OSError as exc:
            logging.error("Nao foi possivel ler arquivo de feedback %s: %s", file_path.name, exc)
            return None
        normalized = self._normalize_feedback_dict(data, file_path)
        if not normalized:
            logging.warning("Feedback JSON em %s nao continha campos reconhecidos.", file_path.name)
        else:
            logging.debug(
                "Feedback JSON normalizado para %s: %s",
                file_path.name,
                {
                    "documento": normalized["documento"],
                    "status": normalized["status"],
                    "nova_categoria": normalized.get("nova_categoria"),
                },
            )
        return normalized

    def _read_feedback_lines(self, file_path: Path) -> List[str]:
        try:
            with open(file_path, "r", encoding="utf-8") as handler:
                return [line.rstrip("\n") for line in handler]
        except UnicodeDecodeError:
            with open(file_path, "r", encoding="latin-1") as handler:
                return [line.rstrip("\n") for line in handler]

    def _parse_key_value_feedback(self, lines: List[str], file_path: Path) -> Optional[Dict]:
        mapping: Dict[str, str] = {}
        extras_raw: Dict[str, str] = {}
        current_key: Optional[str] = None
        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                current_key = None
                continue
            if ":" in line:
                key_part, value_part = line.split(":", 1)
            elif "=" in line:
                key_part, value_part = line.split("=", 1)
            else:
                if current_key == "observacoes":
                    existing = mapping.get("observacoes", "")
                    mapping["observacoes"] = (existing + ("\n" if existing else "") + raw_line.strip()).strip()
                continue
            mapped_key = self._map_feedback_key(key_part)
            if not mapped_key:
                current_key = None
                continue
            value = value_part.strip()
            if mapped_key.startswith("extra:"):
                extras_raw[mapped_key] = value
                current_key = None
                continue
            if mapped_key == "observacoes":
                existing = mapping.get("observacoes", "")
                mapping["observacoes"] = (existing + ("\n" if existing else "") + value).strip()
                current_key = "observacoes"
            else:
                mapping[mapped_key] = value
                current_key = None
        if not mapping:
            return None
        return self._normalize_feedback_payload(mapping, file_path, extras_raw)

    def _parse_checkbox_feedback(self, lines: List[str], file_path: Path) -> Optional[Dict]:
        document_name = ""
        status = ""
        new_category: Optional[str] = None
        observations: List[str] = []
        capture_observations = False

        for raw_line in lines:
            line = raw_line.strip()
            lower = line.lower()
            if lower.startswith("documento analisado") or lower.startswith("arquivo analisado"):
                parts = line.split(":", 1)
                if len(parts) == 2:
                    document_name = parts[1].strip()
            elif lower.startswith("documento:") and not document_name:
                document_name = line.split(":", 1)[1].strip()
            elif "[x]" in lower and "correto" in lower:
                status = "correto"
            elif "[x]" in lower and "incorreto" in lower:
                status = "incorreto"
            elif lower.startswith("categoria correta"):
                parts = line.split(":", 1)
                if len(parts) == 2:
                    candidate = parts[1].strip()
                    if candidate:
                        new_category = candidate
            elif lower.startswith("categoria:") and not new_category:
                parts = line.split(":", 1)
                if len(parts) == 2:
                    candidate = parts[1].strip()
                    if candidate:
                        new_category = candidate
            elif lower.startswith("justificativa") or lower.startswith("comentarios"):
                capture_observations = True
                continue
            elif capture_observations:
                if line:
                    observations.append(line)
                else:
                    capture_observations = False

        if not status:
            for raw_line in lines:
                if "status" in raw_line.lower():
                    parts = raw_line.split(":", 1)
                    if len(parts) == 2:
                        status = parts[1].strip()
                        break

        mapping = {
            "documento": document_name,
            "status": status or "correto",
            "nova_categoria": new_category,
            "observacoes": "\n".join(observations).strip(),
        }
        return self._normalize_feedback_payload(mapping, file_path)

    def _normalize_feedback_dict(self, data: Dict, file_path: Path) -> Optional[Dict]:
        mapping: Dict[str, str] = {}
        extras_raw: Dict[str, str] = {}
        for key, value in data.items():
            if not isinstance(key, str):
                continue
            mapped = self._map_feedback_key(key)
            if not mapped:
                continue
            if isinstance(value, list):
                value = "\n".join(str(item) for item in value)
            elif isinstance(value, (int, float)):
                value = str(value)
            elif value is None:
                value = ""
            else:
                value = str(value)
            if mapped.startswith("extra:"):
                extras_raw[mapped] = value.strip()
            elif mapped == "observacoes":
                existing = mapping.get("observacoes", "")
                mapping["observacoes"] = (existing + ("\n" if existing else "") + value.strip()).strip()
            else:
                mapping[mapped] = value.strip()
        if not mapping:
            return None
        return self._normalize_feedback_payload(mapping, file_path, extras_raw)

    def _map_feedback_key(self, raw_key: str) -> Optional[str]:
        normalized = raw_key.strip().lower().replace("-", " ").replace("_", " ")
        if normalized in {"documento", "documento analisado", "arquivo", "arquivo analisado", "doc", "file"}:
            return "documento"
        if normalized in {"status", "avaliacao", "resultado"}:
            return "status"
        if normalized in {"nova categoria", "categoria correta", "categoria"}:
            return "nova_categoria"
        if normalized in {"observacoes", "observacao", "justificativa", "comentarios", "comentarios adicionais", "notas"}:
            return "observacoes"
        if normalized in {"confianca revisada", "confianca_revisada", "confidence", "confidence override"}:
            return "confidence_override"
        if normalized in {"areas secundarias", "areas", "areas_secundarias", "multi categoria"}:
            return "areas_secundarias"
        if normalized in {"palavras chave relevantes", "palavras relevantes", "keywords relevantes", "keywords_positive"}:
            return "keywords_positive"
        if normalized in {"palavras chave irrelevantes", "palavras irrelevantes", "keywords negativas", "keywords_negative"}:
            return "keywords_negative"
        if normalized in {"motivos relevantes", "motivos positivos", "pontos fortes"}:
            return "motivos_relevantes"
        if normalized in {"motivos criticos", "motivos negativos", "pontos fracos", "alertas"}:
            return "motivos_criticos"
        if normalized in {"aprovar para conhecimento", "aprovar conhecimento", "aprovar base", "aprovar documental"}:
            return "approve_for_knowledge"
        if normalized in {"marcar reanalise", "solicitar reanalise", "reanalise"}:
            return "request_reanalysis"
        if normalized in {"categoria feedback", "categoria_feedback", "categoria pasta"}:
            return "categoria_feedback"
        if normalized.startswith("categoria nome"):
            suffix = normalized.replace("categoria nome", "", 1).strip()
            if suffix:
                return f"extra:label:{self._slugify_category(suffix)}"
        if normalized.startswith("confirmar categoria principal"):
            return "extra:confirmar_principal"
        if normalized.startswith("justificativa principal usuario"):
            return "extra:principal_comment"
        if normalized.startswith("categoria alternativa"):
            suffix = normalized.replace("categoria alternativa", "", 1).strip()
            if suffix:
                return f"extra:alt:{self._slugify_category(suffix)}"
        if normalized.startswith("trecho evidencia"):
            suffix = normalized.replace("trecho evidencia", "", 1).strip()
            if suffix:
                return f"extra:evidence:{self._slugify_category(suffix)}"
        if normalized.startswith("acao incluir conhecimento"):
            suffix = normalized.replace("acao incluir conhecimento", "", 1).strip()
            if suffix:
                return f"extra:include:{self._slugify_category(suffix)}"
        return None

    def _normalize_feedback_payload(
        self,
        mapping: Dict[str, str],
        file_path: Path,
        raw_extras: Optional[Dict[str, str]] = None,
    ) -> Dict:
        document_name = mapping.get("documento") or self._infer_document_from_name(file_path.name)
        status = self._normalize_status(mapping.get("status"))
        nova_categoria = mapping.get("nova_categoria")
        if nova_categoria:
            nova_categoria = nova_categoria.strip() or None
        else:
            nova_categoria = None
        observacoes = (mapping.get("observacoes") or "").strip()

        def parse_list(value: Optional[str]) -> List[str]:
            if not value:
                return []
            text = str(value)
            for sep in [";", "|"]:
                text = text.replace(sep, ",")
            text = text.replace("\n", ",")
            return [item.strip() for item in text.split(",") if item.strip()]

        def parse_bool(value: Optional[str]) -> Optional[bool]:
            if value is None:
                return None
            normalized_bool = str(value).strip().lower()
            if normalized_bool in {"sim", "s", "true", "1", "yes"}:
                return True
            if normalized_bool in {"nao", "n+uo", "n", "false", "0", "no"}:
                return False
            return None

        def parse_float(value: Optional[str]) -> Optional[float]:
            if value is None or value == "":
                return None
            try:
                return float(str(value).replace(",", ".").replace("%", "").strip())
            except ValueError:
                return None

        def clean_label(value: Optional[str]) -> str:
            if value is None:
                return ""
            text = str(value)
            if "#" in text:
                text = text.split("#", 1)[0]
            return text.strip()

        extras: Dict[str, Any] = {}
        if "confidence_override" in mapping:
            extras["confidence_override"] = parse_float(mapping.get("confidence_override"))
        if "areas_secundarias" in mapping:
            extras["areas_secundarias"] = parse_list(mapping.get("areas_secundarias"))
        if "keywords_positive" in mapping:
            extras["keywords_positive"] = parse_list(mapping.get("keywords_positive"))
        if "keywords_negative" in mapping:
            extras["keywords_negative"] = parse_list(mapping.get("keywords_negative"))
        if "motivos_relevantes" in mapping:
            extras["motivos_relevantes"] = parse_list(mapping.get("motivos_relevantes"))
        if "motivos_criticos" in mapping:
            extras["motivos_criticos"] = parse_list(mapping.get("motivos_criticos"))
        if "approve_for_knowledge" in mapping:
            extras["approve_for_knowledge"] = parse_bool(mapping.get("approve_for_knowledge"))
        if "request_reanalysis" in mapping:
            extras["request_reanalysis"] = parse_bool(mapping.get("request_reanalysis"))
        if "categoria_feedback" in mapping:
            extras["categoria_feedback"] = mapping.get("categoria_feedback")

        raw_extras = raw_extras or {}
        category_feedback: Dict[str, Dict[str, Any]] = {}
        for key, raw_value in raw_extras.items():
            if key == "extra:confirmar_principal":
                extras["confirmar_principal"] = parse_bool(raw_value)
                continue
            if key == "extra:principal_comment":
                extras["principal_comment"] = str(raw_value).strip()
                continue
            if key.startswith("extra:"):
                parts = key.split(":", 2)
                if len(parts) != 3:
                    continue
                _, kind, slug = parts
                slug = slug.strip()
                if not slug:
                    continue
                entry = category_feedback.setdefault(slug, {})
                if kind == "label":
                    entry["label"] = clean_label(raw_value)
                elif kind == "alt":
                    entry["selected"] = parse_bool(raw_value)
                elif kind == "evidence":
                    entry["evidence"] = str(raw_value).strip()
                elif kind == "include":
                    entry["include"] = parse_bool(raw_value)
        if category_feedback:
            extras["category_feedback"] = category_feedback

        return {
            "documento": document_name,
            "status": status,
            "observacoes": observacoes,
            "nova_categoria": nova_categoria,
            "extras": extras,
        }

    def _normalize_status(self, value: Optional[str]) -> str:
        if value is None:
            return "correto"
        normalized = str(value).strip().lower()
        if normalized in {"", "correto", "ok", "aprovado", "valido", "validado", "certo", "confirmado", "true", "1"}:
            return "correto"
        if normalized in {"incorreto", "errado", "revisar", "ajustar", "reprocessar", "false", "0"}:
            return "incorreto"
        return "correto"

    def _infer_document_from_name(self, file_name: str) -> str:
        base = os.path.splitext(file_name)[0]
        if base.startswith("feedback_"):
            return base.replace("feedback_", "", 1)
        return base

    def _slugify_category(self, value: str) -> str:
        slug = "".join(ch.lower() if ch.isalnum() else "_" for ch in value)
        slug = "_".join(part for part in slug.split("_") if part)
        return slug or "geral"
