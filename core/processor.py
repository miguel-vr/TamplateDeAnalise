import logging
import os
import shutil
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple, List, TYPE_CHECKING

from core.gpt_core import GPTCore, GPTServiceUnavailable
from core.knowledge_base import KnowledgeBase
from core.validator import Validator
from core.taxonomy import TaxonomyRuleEngine

if TYPE_CHECKING:
    from core.notifier import TeamsNotifier

try:
    import fitz  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    fitz = None  # type: ignore

try:
    from docx import Document  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    Document = None  # type: ignore


SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt"}


class _ProcessingTimeline:
    """Helper to consolidate stage logging and structured events."""

    def __init__(
        self,
        file_name: str,
        processing_id: str,
        emitter: Optional[Callable[[str, Dict[str, Any]], None]],
    ) -> None:
        self.file_name = file_name
        self.processing_id = processing_id
        self._emitter = emitter
        self._stage_start: Dict[str, float] = {}
        self._started_at = time.perf_counter()
        self._records: List[Dict[str, Any]] = []

    def emit(self, event_type: str, payload: Optional[Dict[str, Any]] = None) -> None:
        if not self._emitter:
            return
        record = {"processing_id": self.processing_id, "file": self.file_name}
        if payload:
            record.update(payload)
        try:
            self._emitter(event_type, record)
        except Exception as exc:  # pragma: no cover - defensive
            logging.debug(
                "[%s] Falha ao emitir evento %s: %s",
                self.processing_id,
                event_type,
                exc,
            )

    def stage_start(self, stage: str, extra: Optional[Dict[str, Any]] = None) -> None:
        self._stage_start[stage] = time.perf_counter()
        logging.info(
            "[%s] Etapa '%s' iniciada para %s.",
            self.processing_id,
            stage,
            self.file_name,
        )
        payload = {"stage": stage, "status": "started"}
        if extra:
            payload.update(extra)
        self.emit("processing_stage", payload)
        self._records.append(
            {
                "stage": stage,
                "status": "started",
                "timestamp": time.time(),
                "extra": dict(extra or {}),
            }
        )

    def stage_end(self, stage: str, extra: Optional[Dict[str, Any]] = None) -> None:
        start_time = self._stage_start.pop(stage, None)
        duration = time.perf_counter() - start_time if start_time else None
        detail = ""
        if extra:
            joined = ", ".join(f"{key}={value}" for key, value in extra.items())
            detail = f" ({joined})"
        if duration is not None:
            logging.info(
                "[%s] Etapa '%s' concluida em %.2fs%s.",
                self.processing_id,
                stage,
                duration,
                detail,
            )
        else:
            logging.info(
                "[%s] Etapa '%s' concluida%s.",
                self.processing_id,
                stage,
                detail,
            )
        payload = {"stage": stage, "status": "completed"}
        if duration is not None:
            payload["duration"] = round(duration, 3)
        if extra:
            payload.update(extra)
        self.emit("processing_stage", payload)
        self._records.append(
            {
                "stage": stage,
                "status": "completed",
                "timestamp": time.time(),
                "duration": round(duration, 3) if duration is not None else None,
                "extra": dict(extra or {}),
            }
        )

    def stage_error(self, stage: str, error: Exception) -> None:
        start_time = self._stage_start.pop(stage, None)
        duration = time.perf_counter() - start_time if start_time else None
        if duration is not None:
            logging.error(
                "[%s] Etapa '%s' falhou apos %.2fs: %s",
                self.processing_id,
                stage,
                duration,
                error,
            )
        else:
            logging.error(
                "[%s] Etapa '%s' falhou: %s",
                self.processing_id,
                stage,
                error,
            )
        payload = {"stage": stage, "status": "error", "error": str(error)}
        if duration is not None:
            payload["duration"] = round(duration, 3)
        self.emit("processing_stage", payload)
        self._records.append(
            {
                "stage": stage,
                "status": "error",
                "timestamp": time.time(),
                "duration": round(duration, 3) if duration is not None else None,
                "error": str(error),
            }
        )

    def finish(self, success: bool, extra: Optional[Dict[str, Any]] = None) -> None:
        duration = time.perf_counter() - self._started_at
        status = "success" if success else "error"
        logging.info(
            "[%s] Processamento de %s finalizado (%s) em %.2fs.",
            self.processing_id,
            self.file_name,
            status,
            duration,
        )
        payload = {"status": status, "duration": round(duration, 3)}
        if extra:
            payload.update(extra)
        self.emit("processing_finished", payload)
        self._records.append(
            {
                "stage": "pipeline",
                "status": status,
                "timestamp": time.time(),
                "duration": round(duration, 3),
                "extra": dict(extra or {}),
            }
        )

    def records(self) -> List[Dict[str, Any]]:
        return list(self._records)


class DocumentProcessor:
    """Coordinates document ingestion, GPT analysis, and artifact generation."""

    def __init__(
        self,
        gpt_core: GPTCore,
        validator: Validator,
        knowledge_base: KnowledgeBase,
        base_folder: str,
        event_emitter: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        taxonomy_engine: Optional[TaxonomyRuleEngine] = None,
        teams_notifier: Optional["TeamsNotifier"] = None,
    ):
        self.gpt_core = gpt_core
        self.validator = validator
        self.knowledge_base = knowledge_base
        self.base_folder = Path(base_folder)
        self.processed_folder = self.base_folder / "folders" / "processados"
        self._event_emitter = event_emitter
        self.taxonomy_engine = taxonomy_engine
        self.teams_notifier = teams_notifier

    def _emit_event(self, event_type: str, payload: Optional[Dict[str, Any]] = None) -> None:
        if not self._event_emitter:
            return
        try:
            self._event_emitter(event_type, payload or {})
        except Exception as exc:  # pragma: no cover - defensive
            logging.debug("Falha ao emitir evento %s: %s", event_type, exc)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process_file(self, file_path: str, processing_id: Optional[str] = None) -> Optional[Path]:
        path = Path(file_path)
        proc_id = processing_id or uuid.uuid4().hex[:12]
        suffix = path.suffix.lower()
        size_bytes = path.stat().st_size if path.exists() else 0
        timeline = _ProcessingTimeline(path.name, proc_id, self._event_emitter)
        timeline.emit(
            "processing_started",
            {"path": str(path), "extension": suffix, "size_bytes": size_bytes},
        )
        logging.info(
            "[%s] Iniciando processamento de %s (extensao=%s, tamanho=%s bytes).",
            proc_id,
            path.name,
            suffix or "desconhecida",
            size_bytes,
        )
        if suffix not in SUPPORTED_EXTENSIONS:
            logging.warning("[%s] Extensao nao suportada para %s. Ignorando.", proc_id, path)
            timeline.finish(False, {"reason": "unsupported_extension"})
            return None

        try:
            timeline.stage_start("extracao_texto", {"extensao": suffix})
            try:
                text = self._extract_text(path)
            except Exception as exc:
                timeline.stage_error("extracao_texto", exc)
                raise
            timeline.stage_end("extracao_texto", {"caracteres": len(text)})

            logging.info(
                "[%s] Extracao concluida para %s com %s caracteres.",
                proc_id,
                path.name,
                len(text),
            )
            if not text or len(text.strip()) < 20:
                logging.warning("[%s] Conteudo insuficiente em %s para analise.", proc_id, path.name)
                timeline.finish(False, {"reason": "conteudo_insuficiente", "caracteres": len(text)})
                return None

            metadata = {"file_name": path.name, "absolute_path": str(path)}

            timeline.stage_start("analise_gpt")
            try:
                primary_result = self.gpt_core.analyze_document(text, metadata)
            except GPTServiceUnavailable as exc:
                timeline.stage_error("analise_gpt", exc)
                logging.error("[%s] Falha ao acessar o GPT para %s: %s", proc_id, path.name, exc)
                self._handle_gpt_failure(path)
                timeline.finish(False, {"reason": "gpt_indisponivel", "error": str(exc)})
                return None
            except Exception as exc:
                timeline.stage_error("analise_gpt", exc)
                raise
            categoria_inicial = primary_result.get("categoria") or primary_result.get("categoria_principal")
            confianca_inicial = primary_result.get("confidence_percent", primary_result.get("confianca", 0))
            timeline.stage_end(
                "analise_gpt",
                {
                    "categoria_inicial": categoria_inicial,
                    "confianca_inicial": confianca_inicial,
                },
            )
            try:
                confianca_log = float(confianca_inicial)
            except (TypeError, ValueError):
                confianca_log = 0.0
            logging.info(
                "[%s] Resultado primario para %s: categoria=%s confianca=%.2f%%",
                proc_id,
                path.name,
                categoria_inicial,
                confianca_log,
            )

            timeline.stage_start("validacao")
            try:
                validated_result = self.validator.ensure_confidence(primary_result, text, metadata)
            except Exception as exc:
                timeline.stage_error("validacao", exc)
                raise
            confidence_validada = round(validated_result.get("confidence", 0.0) * 100, 2)
            timeline.stage_end(
                "validacao",
                {
                    "categoria_validada": validated_result.get("categoria"),
                    "confianca_validada": confidence_validada,
                    "tentativas_validacao": validated_result.get("validation_attempts"),
                },
            )
            logging.info(
                "[%s] Resultado validado para %s: categoria=%s confianca=%.2f%% (tentativas=%s)",
                proc_id,
                path.name,
                validated_result.get("categoria"),
                confidence_validada,
                validated_result.get("validation_attempts"),
            )
            matches = validated_result.get("knowledge_matches") or []
            if matches:
                top_log = "; ".join(
                    f"{item['category']} (match {item['best_match']:.2f})"
                    for item in matches[:3]
                )
                logging.info("[%s] Conhecimento base: melhores correspondencias para %s -> %s", proc_id, path.name, top_log)
            else:
                logging.info("[%s] Conhecimento base: nenhum match relevante para %s", proc_id, path.name)

            if self.taxonomy_engine:
                timeline.stage_start("refinamento_taxonomia")
                try:
                    refinement = self.taxonomy_engine.refine(
                        text=text,
                        validation_result=validated_result,
                        known_categories=self.knowledge_base.known_categories(),
                        knowledge_matches=matches,
                    )
                except Exception as exc:
                    timeline.stage_error("refinamento_taxonomia", exc)
                    logging.exception("[%s] Falha na camada heuristica: %s", proc_id, exc)
                else:
                    validated_result = refinement["result"]
                    taxonomy_report = refinement["report"]
                    scores = taxonomy_report.get("scores", {})
                    top_category = taxonomy_report.get("top_category")
                    heur_score = 0.0
                    if top_category and top_category in scores:
                        heur_score = scores[top_category].get("score", 0.0)  # type: ignore[index]
                    timeline.stage_end(
                        "refinamento_taxonomia",
                        {
                            "acao": taxonomy_report.get("action"),
                            "categoria": validated_result.get("categoria"),
                            "score_heuristico": round(float(heur_score), 3),
                        },
                    )
                    logging.info(
                        "[%s] Heuristica taxonomica para %s: action=%s top=%s score=%.2f composite=%s",
                        proc_id,
                        path.name,
                        taxonomy_report.get("action"),
                        taxonomy_report.get("top_category"),
                        taxonomy_report.get("top_score", 0.0),
                        taxonomy_report.get("composite_scores"),
                    )
                    self._emit_event(
                        "taxonomy_refinement",
                        {
                            "processing_id": proc_id,
                            "file": path.name,
                            "action": taxonomy_report.get("action"),
                            "top_category": taxonomy_report.get("top_category"),
                            "scores": taxonomy_report.get("scores"),
                            "composite": taxonomy_report.get("composite_scores"),
                        },
                    )
            timeline.stage_start("resolucao_categoria", {"categoria": validated_result.get("categoria")})
            try:
                category_folder, created_folder = self._resolve_category_folder(validated_result)
            except Exception as exc:
                timeline.stage_error("resolucao_categoria", exc)
                raise
            timeline.stage_end(
                "resolucao_categoria",
                {"pasta": str(category_folder), "criada": created_folder},
            )
            if created_folder:
                logging.info("[%s] Pasta de categoria criada: %s", proc_id, category_folder)
                self._emit_event(
                    "category_folder_created",
                    {
                        "processing_id": proc_id,
                        "categoria": validated_result.get("categoria"),
                        "folder": str(category_folder),
                    },
                )
            logging.info(
                "[%s] Gerando pacote final para %s na pasta %s",
                proc_id,
                path.name,
                category_folder,
            )

            timeline.stage_start("geracao_pacote", {"pasta": str(category_folder)})
            try:
                zip_path = self._generate_bundle(path, text, validated_result, category_folder)
            except Exception as exc:
                timeline.stage_error("geracao_pacote", exc)
                raise
            timeline.stage_end("geracao_pacote", {"zip": str(zip_path)})
            logging.info("[%s] Pacote gerado para %s em %s", proc_id, path.name, zip_path)

            timeline.stage_start("atualizacao_conhecimento", {"categoria": validated_result.get("categoria")})
            try:
                entry = self.knowledge_base.add_entry(
                    file_name=path.name,
                    category=validated_result.get("categoria", "outros"),
                    theme=validated_result.get("tema", "Tema nao identificado"),
                    confidence=validated_result.get("confidence", 0.0),
                    summary=self._build_summary(text),
                    justification=validated_result.get("justificativa", ""),
                    areas_secundarias=validated_result.get("areas_secundarias"),
                    raw_text=text,
                )
            except Exception as exc:
                timeline.stage_error("atualizacao_conhecimento", exc)
                raise
            timeline.stage_end(
                "atualizacao_conhecimento",
                {"categoria": entry.category, "confianca": round(entry.confidence * 100, 2)},
            )
            logging.info(
                "[%s] Base de conhecimento atualizada: %s -> categoria=%s (conf=%.2f%%)",
                proc_id,
                entry.file_name,
                entry.category,
                entry.confidence * 100,
            )
            self._emit_event(
                "knowledge_entry_added",
                {
                    "processing_id": proc_id,
                    "file": entry.file_name,
                    "categoria": entry.category,
                    "confidence": entry.confidence,
                    "entry_id": entry.id,
                },
            )

            timeline.finish(True, {"categoria": entry.category, "artifact": str(zip_path)})
            timeline_records = timeline.records()
            self._emit_event(
                "processing_timeline_summary",
                {
                    "processing_id": proc_id,
                    "file": path.name,
                    "records": timeline_records,
                },
            )
            logging.info(
                "[%s] Resumo de etapas para %s: %s",
                proc_id,
                path.name,
                ", ".join(
                    f"{record['stage']}={record.get('duration')}s"
                    for record in timeline_records
                    if record["status"] == "completed" and record.get("duration") is not None
                ),
            )
            notification_payload = {
                "file_name": path.name,
                "zip_path": str(zip_path),
                "category": entry.category,
                "theme": validated_result.get("tema"),
                "confidence_percent": validated_result.get("confidence", 0.0) * 100,
                "taxonomy": validated_result.get("taxonomy_report"),
                "knowledge_matches": matches,
                "timeline": timeline_records,
                "summary": self._build_summary(text, limit=320),
            }
            if self.teams_notifier:
                try:
                    self.teams_notifier.send_analysis_summary(notification_payload)
                except Exception as exc:
                    logging.exception(
                        "[%s] Falha ao enviar notificacao Teams para %s: %s",
                        proc_id,
                        path.name,
                        exc,
                    )

            try:
                path.unlink()
            except OSError as exc:
                logging.error("[%s] Falha ao remover arquivo temporario %s: %s", proc_id, path, exc)
            return zip_path
        except Exception as exc:
            self._handle_unexpected_failure(path, proc_id, timeline, exc)
            raise
    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _extract_text(self, path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            return self._read_pdf(path)
        if suffix == ".docx":
            return self._read_docx(path)
        if suffix == ".txt":
            return self._read_txt(path)
        return ""

    def _read_pdf(self, path: Path) -> str:
        if fitz is None:
            logging.error("PyMuPDF (fitz) não está instalado. Não é possível processar PDFs.")
            return ""
        try:
            logging.info("Abrindo PDF %s para extracao de texto", path.name)
            with fitz.open(path) as doc:
                text = "\n".join(page.get_text("text") for page in doc)
                logging.info("PDF %s extraido com %s paginas", path.name, doc.page_count)
                return text
        except Exception as exc:  # pragma: no cover - runtime dependent
            logging.error("Erro ao ler PDF %s: %s", path.name, exc)
            return ""

    def _read_docx(self, path: Path) -> str:
        if Document is None:
            logging.error("python-docx não está instalado. Não é possível processar DOCX.")
            return ""
        try:
            logging.info("Abrindo DOCX %s para extracao de texto", path.name)
            document = Document(path)
            paragraphs = [p.text for p in document.paragraphs if p.text.strip()]
            logging.info("DOCX %s contem %s paragrafos relevantes", path.name, len(paragraphs))
            return "\n".join(paragraphs)
        except Exception as exc:  # pragma: no cover - runtime dependent
            logging.error("Erro ao ler DOCX %s: %s", path.name, exc)
            return ""

    def _read_txt(self, path: Path) -> str:
        try:
            logging.info("Lendo arquivo TXT %s (UTF-8)", path.name)
            with open(path, "r", encoding="utf-8") as handler:
                return handler.read()
        except UnicodeDecodeError:
            logging.info("Reprocessando TXT %s com codificacao latin-1", path.name)
            with open(path, "r", encoding="latin-1") as handler:
                return handler.read()
        except Exception as exc:
            logging.error("Erro ao ler TXT %s: %s", path.name, exc)
            return ""

    def _resolve_category_folder(self, result: Dict) -> Tuple[Path, bool]:
        category = result.get("categoria") or result.get("categoria_principal") or "outros"
        slug = self._slugify(category)
        target_folder = self.processed_folder / slug
        existed = target_folder.exists()
        target_folder.mkdir(parents=True, exist_ok=True)
        return target_folder, not existed

    def _slugify(self, text: str) -> str:
        cleaned = "".join(ch if ch.isalnum() else "_" for ch in text.lower())
        cleaned = cleaned.strip("_")
        return cleaned or "outros"

    def _generate_bundle(self, source_path: Path, text: str, result: Dict, category_folder: Path) -> Path:
        with tempfile.TemporaryDirectory() as tmpdir:
            analysis_path = Path(tmpdir) / "analise.txt"
            feedback_path = Path(tmpdir) / "feedback.txt"
            logging.info("Escrevendo arquivos auxiliares (analise.txt, feedback.txt) para %s", source_path.name)
            self._write_analysis_file(analysis_path, source_path, text, result)
            self._write_feedback_file(feedback_path, source_path, result)

            zip_name = f"{source_path.stem}.zip"
            destination_zip = category_folder / zip_name
            temp_zip = Path(tmpdir) / zip_name

            logging.info("Compactando arquivos em %s", temp_zip)
            with zipfile.ZipFile(temp_zip, "w", zipfile.ZIP_DEFLATED) as bundle:
                bundle.write(source_path, arcname=source_path.name)
                bundle.write(analysis_path, arcname="analise.txt")
                bundle.write(feedback_path, arcname="feedback.txt")

            shutil.move(str(temp_zip), destination_zip)
            logging.info("Pacote %s movido para %s", zip_name, destination_zip)
            return destination_zip

    def _handle_gpt_failure(self, processing_path: Path) -> None:
        entrada_path = self.base_folder / "folders" / "entrada" / processing_path.name
        logging.error(
            "GPT indisponivel. Devolvendo %s para a pasta de entrada (%s) e abortando processamento.",
            processing_path.name,
            entrada_path,
        )
        try:
            shutil.move(str(processing_path), entrada_path)
        except Exception as exc:
            logging.error(
                "Falha ao devolver arquivo %s para pasta de entrada: %s",
                processing_path,
                exc,
            )

    def _handle_unexpected_failure(
        self,
        processing_path: Path,
        proc_id: str,
        timeline: _ProcessingTimeline,
        error: Exception,
    ) -> None:
        logging.exception("[%s] Falha inesperada durante processamento de %s: %s", proc_id, processing_path, error)
        timeline.finish(False, {"reason": "unexpected_error", "error": str(error)})
        self._emit_event(
            "processing_internal_error",
            {
                "processing_id": proc_id,
                "file": processing_path.name,
                "error": str(error),
            },
        )
        if processing_path.exists():
            failure_dir = self.base_folder / "folders" / "em_processamento" / "_falhas"
            failure_dir.mkdir(parents=True, exist_ok=True)
            destination = failure_dir / processing_path.name
            try:
                shutil.move(str(processing_path), destination)
                logging.info(
                    "[%s] Arquivo %s movido para pasta de falhas: %s",
                    proc_id,
                    processing_path.name,
                    destination,
                )
            except Exception as move_err:
                logging.error(
                    "[%s] Falha ao mover %s para pasta de falhas: %s",
                    proc_id,
                    processing_path,
                    move_err,
                )

    def _write_analysis_file(self, target: Path, source_path: Path, text: str, result: Dict) -> None:
        confidence_percent = round(result.get("confidence", 0.0) * 100, 2)
        lines = [
            f"Documento: {source_path.name}",
            f"Categoria principal: {result.get('categoria', 'Não identificada')}",
            f"Tema: {result.get('tema', 'Tema não identificado')}",
            f"Áreas secundárias: {', '.join(result.get('areas_secundarias', []) or ['Nenhuma'])}",
            f"Confiança: {confidence_percent:.2f}%",
            "",
            "Justificativa:",
            result.get("justificativa", "Não informado."),
            "",
            "Motivos chave:",
        ]
        motivos = result.get("motivos_chave") or ["Não informados."]
        lines.extend(f"- {motivo}" for motivo in motivos)

        cross = result.get("cross_validation", {})
        lines.append("")
        lines.append("Cross-validation:")
        lines.append(f"  Acordo: {cross.get('agreement', 'Não informado')}")
        lines.append(f"  Ajuste de confiança: {cross.get('confidence_adjustment', 0)}")
        risks = cross.get("risks") or []
        if risks:
            lines.append("  Riscos:")
            lines.extend(f"    - {risk}" for risk in risks)
        notes = cross.get("notes")
        if notes:
            lines.append(f"  Notas: {notes}")

        i3 = result.get("i3_explanation", {})
        lines.append("")
        lines.append("Camada I3 (Insight, Impacto, Inferência):")
        lines.append(f"  Insight: {i3.get('insight', 'Não informado')}")
        lines.append(f"  Impacto: {i3.get('impacto', 'Não informado')}")
        lines.append(f"  Inferência: {i3.get('inferencia', 'Não informado')}")
        lines.append(f"  Motivação da confiabilidade: {result.get('confidence_reason', 'Não informado')}")

        knowledge_matches = result.get("knowledge_matches") or []
        if knowledge_matches:
            lines.append("")
            lines.append("Validação por conhecimento local:")
            for match in knowledge_matches:
                lines.append(
                    f"  - Categoria {match.get('category')} | match={match.get('best_match', 0):.2f} | média={match.get('average_match', 0):.2f}"
                )

        validation_layers = result.get("validation_layers", {})
        profile = validation_layers.get("category_profile") or {}
        keywords = profile.get("top_keywords") or []
        if keywords:
            lines.append("")
            lines.append("Palavras-chave históricas da categoria selecionada:")
            lines.append(f"  {', '.join(keywords[:10])}")

        similar_docs = validation_layers.get("similar_documents") or []
        if similar_docs:
            lines.append("")
            lines.append("Documentos similares utilizados na decisão:")
            for item in similar_docs:
                lines.append(
                    f"  - {item.get('file_name')} | categoria: {item.get('category')} | similaridade: {item.get('score', 0):.2f}"
                )

        taxonomy_report = result.get("taxonomy_report") or {}
        if taxonomy_report:
            lines.append("")
            lines.append("Camada heuristica (Taxonomy Rule Engine):")
            lines.append(
                f"  Top categoria: {taxonomy_report.get('top_category', 'N/A')} (score={taxonomy_report.get('top_score', 0):.2f})"
            )
            lines.append(f"  Acao tomada: {taxonomy_report.get('action', 'kept')}")
            composite = taxonomy_report.get("composite_scores") or {}
            if composite:
                lines.append("  Scores compostos:")
                for key, value in composite.items():
                    lines.append(f"    - {key}: {value:.2f}")

        suggested = result.get("nova_categoria_sugerida")
        if suggested:
            lines.append("")
            lines.append(f"Nova categoria sugerida: {suggested}")

        lines.append("")
        lines.append("Resumo do texto analisado:")
        lines.append(self._build_summary(text))

        with open(target, "w", encoding="utf-8") as handler:
            handler.write("\n".join(lines))

    def _write_feedback_file(self, target: Path, source_path: Path, result: Dict) -> None:
        template = [
            f"documento: {source_path.name}",
            f"status: correto",
            f"categoria_atribuida: {result.get('categoria', 'Nao identificada')}",
            f"tema: {result.get('tema', 'Tema nao identificado')}",
            f"confianca_estimativa: {round(result.get('confidence', 0.0) * 100, 2):.2f}%",
            "nova_categoria:",
            "observacoes:",
            "",
            "# Opcional: use os marcadores abaixo se preferir apenas marcar com X",
            "[ ] Correto",
            "[ ] Incorreto",
            "Categoria correta (se marcou Incorreto):",
            "Justificativa adicional:",
            "",
            "Instrucoes: salve o arquivo e mova para 'folders/feedback/'.",
        ]
        with open(target, 'w', encoding='utf-8') as handler:
            handler.write("\n".join(template))

    def _build_summary(self, text: str, limit: int = 600) -> str:
        sanitized = " ".join(text.split())
        if len(sanitized) <= limit:
            return sanitized
        return sanitized[:limit] + "..."
