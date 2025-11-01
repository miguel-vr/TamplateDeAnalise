import difflib
import os
import unicodedata
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from core.knowledge_base import KnowledgeBase


def _normalize_category_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    stripped = "".join(ch for ch in normalized if ch.isalnum() or ch.isspace())
    return stripped.lower().strip()


class GPTServiceUnavailable(Exception):
    """Levanto esse erro quando o GPT nao responde ou corta a chamada por falta de autorizacao."""

    def __init__(self, message: str, original: Optional[Exception] = None):
        super().__init__(message)
        self.original = original

try:
    from openai import OpenAI, AzureOpenAI
except ImportError:  # pragma: no cover - library not installed yet
    OpenAI = None  # type: ignore
    AzureOpenAI = None  # type: ignore


class GPTCore:
    """Centraliza minhas chamadas ao GPT: montagem dos prompts, retries e ajustes com base no conhecimento local."""

    def __init__(self, config: Dict, knowledge_base: KnowledgeBase):
        self.config = config
        self.knowledge_base = knowledge_base
        self._client = None
        self.offline_mode = False
        self.azure_endpoint = (
            config.get("azure_endpoint")
            or os.getenv("AZURE_OPENAI_ENDPOINT")
            or os.getenv("URL_BASE")
            or ""
        ).rstrip("/")
        self.azure_api_key = (
            config.get("azure_api_key")
            or os.getenv("AZURE_OPENAI_KEY")
            or os.getenv("API_KEY")
            or ""
        )
        self.azure_deployment = (
            config.get("azure_deployment")
            or os.getenv("AZURE_OPENAI_DEPLOYMENT")
            or os.getenv("DEPLOYMENT_NAME")
            or ""
        )
        self.azure_api_version = (
            config.get("azure_api_version")
            or os.getenv("AZURE_OPENAI_API_VERSION")
            or os.getenv("OPENAI_API_VERSION")
            or "2024-02-01"
        )
        self.azure_enabled = bool(
            config.get("use_azure")
            or os.getenv("USE_AZURE_OPENAI")
            or self.azure_endpoint
        )
        try:
            self.secondary_structured_threshold = float(config.get("secondary_structured_threshold", 0.4))
        except (TypeError, ValueError):
            self.secondary_structured_threshold = 0.4
        try:
            self.secondary_document_threshold = float(config.get("secondary_document_threshold", 0.45))
        except (TypeError, ValueError):
            self.secondary_document_threshold = 0.45
        try:
            self.secondary_strong_threshold = float(config.get("secondary_strong_threshold", 0.8))
        except (TypeError, ValueError):
            self.secondary_strong_threshold = 0.8

        if self.azure_enabled:
            if AzureOpenAI is None:
                logging.error("AzureOpenAI nao disponivel. Instale o pacote 'openai' (>=1.16).")
                self.offline_mode = True
            elif not (self.azure_endpoint and self.azure_api_key and self.azure_deployment):
                logging.error("Parametros Azure incompletos. Informe endpoint, api_key e deployment.")
                self.offline_mode = True
            else:
                logging.info("GPTCore configurado para Azure OpenAI (endpoint=%s, deployment=%s).", self.azure_endpoint, self.azure_deployment)
        if not self.azure_enabled:
            if not config.get("api_key") or OpenAI is None:
                self.offline_mode = True
                logging.warning("GPTCore em modo offline. Forneca OPENAI_API_KEY (ou configure Azure) e instale o pacote 'openai'.")

    def _client_instance(self):
        if self.offline_mode:
            return None
        if self._client is None:
            if self.azure_enabled:
                self._client = AzureOpenAI(
                    azure_endpoint=self.azure_endpoint,
                    api_key=self.azure_api_key,
                    api_version=self.azure_api_version,
                )
            else:
                self._client = OpenAI(api_key=self.config["api_key"])
        return self._client

    def ensure_available(self) -> None:
        """Validate GPT availability before starting watchers."""
        if self.offline_mode:
            raise GPTServiceUnavailable("API key ausente ou biblioteca openai nao instalada.")
        client = self._client_instance()
        if client is None:
            raise GPTServiceUnavailable("Falha ao inicializar cliente OpenAI.")
        try:
            if self.azure_enabled:
                try:
                    client.models.list()
                except Exception as exc:
                    message = str(exc).lower()
                    status = getattr(getattr(exc, 'response', None), 'status_code', None)
                    if status not in {None, 200} and status != 404 and '404' not in message:
                        raise
                    logging.debug('Ignorando falha ao listar modelos em Azure (status=%s, msg=%s).', status, message)
            else:
                model = self.config.get("model")
                client.models.retrieve(model)
        except Exception as exc:
            raise GPTServiceUnavailable("Nao foi possivel validar credenciais ou modelo. Verifique chave, endpoint e deployment configurados.") from exc

    def _extract_content_text(self, response) -> str:
        try:
            message = response.choices[0].message  # type: ignore[index]
        except (AttributeError, IndexError, KeyError):
            return ""
        content = getattr(message, "content", "")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            fragments = []
            for part in content:
                if isinstance(part, dict):
                    if part.get("type") == "text":
                        fragments.append(str(part.get("text", "")))
                else:
                    text_value = getattr(part, "text", None)
                    if text_value:
                        fragments.append(str(text_value))
            return "\n".join(fragment.strip() for fragment in fragments if fragment).strip()
        return str(content).strip() if content else ""


    def analyze_document(self, text: str, metadata: Dict) -> Dict:
        """Run the full three-stage GPT analysis pipeline."""
        try:
            self.knowledge_base.refresh_category_documents()
        except Exception as exc:  # pragma: no cover - defensive logging
            logging.error("Falha ao atualizar conhecimento documental: %s", exc)
        similar_context = self.knowledge_base.find_similar(text)
        context_summary = self._format_similarity_context(similar_context)
        known_categories = self.knowledge_base.known_categories()
        category_profiles = self.knowledge_base.category_profiles()
        category_document_profiles = self.knowledge_base.category_document_profiles()
        category_feedback_profiles = self.knowledge_base.category_feedback_profile()
        logging.debug("Similar context used in prompt: %s", context_summary)

        if self.offline_mode:
            primary = self._offline_analysis(text, metadata, context_summary)
            cross = {"agreement": "offline", "confidence_adjustment": 0, "notes": "Offline mode - heuristic result"}
            i3 = {
                "explanation": primary.get("justificativa", "An+ilise heur+stica baseada em similaridade."),
                "reliability_reasoning": "Offline similarity heuristic",
            }
        else:
            primary = self._run_primary_prompt(
                text,
                metadata,
                context_summary,
                known_categories,
                category_profiles,
                category_document_profiles,
                category_feedback_profiles,
            )
            cross = self._run_cross_validation(
                primary,
                text,
                metadata,
                known_categories,
                category_profiles,
                category_document_profiles,
                category_feedback_profiles,
            )
            i3 = self._run_i3_layer(
                primary,
                cross,
                text,
                metadata,
                context_summary,
                known_categories,
                category_profiles,
                category_document_profiles,
                category_feedback_profiles,
            )

        combined = self._combine_outputs(primary, cross, i3)
        combined["similar_context"] = [self._serialize_similarity(item) for item in similar_context]
        knowledge_matches = self.knowledge_base.category_match_report(text, top_n=6)
        combined["knowledge_matches"] = knowledge_matches
        document_matches = self.knowledge_base.document_knowledge_match(text, top_n=6)
        combined["document_knowledge_matches"] = document_matches
        self._apply_knowledge_validation(
            combined,
            known_categories,
            knowledge_matches,
            document_matches,
            category_profiles,
            category_document_profiles,
            category_feedback_profiles,
        )
        combined["validation_layers"] = self._build_validation_layers(
            combined,
            knowledge_matches,
            document_matches,
            similar_context,
            category_profiles,
            category_document_profiles,
            category_feedback_profiles,
        )
        combined["known_categories_snapshot"] = known_categories
        combined["category_feedback_snapshot"] = category_feedback_profiles
        self._ensure_category_folders(combined)
        return combined

    def reanalyze_with_reinforcement(self, text: str, metadata: Dict, previous_result: Dict) -> Dict:
        """Run a reinforced analysis when confidence is low."""
        if self.offline_mode:
            return self._offline_analysis(text, metadata, "Offline reanalysis (heuristic)")
        try:
            self.knowledge_base.refresh_category_documents()
        except Exception as exc:  # pragma: no cover - defensive logging
            logging.error("Falha ao atualizar conhecimento documental: %s", exc)
        known_categories = self.knowledge_base.known_categories()
        category_profiles = self.knowledge_base.category_profiles()
        category_document_profiles = self.knowledge_base.category_document_profiles()
        category_feedback_profiles = self.knowledge_base.category_feedback_profile()
        similar_context = self.knowledge_base.find_similar(text)
        messages = [
            {
                "role": "system",
                "content": (
                    "Voc+ + um especialista em classifica+o+uo de documentos corporativos. "
                    "Recebeu uma classifica+o+uo anterior com baixa confian+oa. "
                    "Reavalie cuidadosamente considerando as observa+o+Aes abaixo."
                ),
            },
            {
                "role": "user",
                "content": self._render_reinforcement_prompt(
                    text,
                    metadata,
                    previous_result,
                    known_categories,
                    category_profiles,
                    category_document_profiles,
                    category_feedback_profiles,
                ),
            },
        ]
        response = self._chat_completion(messages, self.config.get("model"))
        if not response:
            return previous_result
        parsed = self._parse_response(response, previous_result)
        parsed["stage"] = "reinforced"
        knowledge_matches = self.knowledge_base.category_match_report(text, top_n=6)
        parsed["knowledge_matches"] = knowledge_matches
        document_matches = self.knowledge_base.document_knowledge_match(text, top_n=6)
        parsed["document_knowledge_matches"] = document_matches
        self._apply_knowledge_validation(
            parsed,
            known_categories,
            knowledge_matches,
            document_matches,
            category_profiles,
            category_document_profiles,
            category_feedback_profiles,
        )
        parsed["validation_layers"] = self._build_validation_layers(
            parsed,
            knowledge_matches,
            document_matches,
            similar_context,
            category_profiles,
            category_document_profiles,
            category_feedback_profiles,
        )
        parsed["similar_context"] = [self._serialize_similarity(item) for item in similar_context]
        parsed["category_feedback_snapshot"] = category_feedback_profiles
        self._ensure_category_folders(parsed)
        return parsed

    # -------------------------------------------------------------------------
    # Prompt Construction Helpers
    # -------------------------------------------------------------------------
    def _run_primary_prompt(
        self,
        text: str,
        metadata: Dict,
        context_summary: str,
        known_categories: List[str],
        category_profiles: Dict[str, Dict[str, List[str]]],
        category_document_profiles: Dict[str, Dict[str, Any]],
        category_feedback_profiles: Dict[str, Dict[str, Any]],
    ) -> Dict:
        prompt = self._render_primary_prompt(
            text,
            metadata,
            context_summary,
            known_categories,
            category_profiles,
            category_document_profiles,
            category_feedback_profiles,
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "Voc+ + um classificador especialista em documenta+o+uo corporativa. "
                    "Classifique documentos por categoria e tema considerando o contexto completo."
                    " Responda sempre em JSON v+ilido."
                ),
            },
            {"role": "user", "content": prompt},
        ]
        response = self._chat_completion(messages, self.config.get("model"))
        return self._parse_response(response)

    def _run_cross_validation(
        self,
        primary: Dict,
        text: str,
        metadata: Dict,
        known_categories: List[str],
        category_profiles: Dict[str, Dict[str, List[str]]],
        category_document_profiles: Dict[str, Dict[str, Any]],
        category_feedback_profiles: Dict[str, Dict[str, Any]],
    ) -> Dict:
        prompt = self._render_cross_prompt(
            primary,
            text,
            metadata,
            known_categories,
            category_profiles,
            category_document_profiles,
            category_feedback_profiles,
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "Voc+ atua como auditor independente validando classifica+o+Aes de documentos. "
                    "Avalie coer+ncia, confian+oa e poss+veis erros. Responda em JSON v+ilido."
                ),
            },
            {"role": "user", "content": prompt},
        ]
        response = self._chat_completion(messages, self.config.get("cross_validation_model", self.config.get("model")))
        parsed = self._parse_optional_response(response)
        parsed["stage"] = "cross-validation"
        return parsed

    def _run_i3_layer(
        self,
        primary: Dict,
        cross: Dict,
        text: str,
        metadata: Dict,
        context_summary: str,
        known_categories: List[str],
        category_profiles: Dict[str, Dict[str, List[str]]],
        category_document_profiles: Dict[str, Dict[str, Any]],
        category_feedback_profiles: Dict[str, Dict[str, Any]],
    ) -> Dict:
        prompt = self._render_i3_prompt(
            primary,
            cross,
            text,
            metadata,
            context_summary,
            known_categories,
            category_profiles,
            category_document_profiles,
            category_feedback_profiles,
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "Voc+ gera explica+o+Aes estruturadas (camada I3 - Insight, Impacto, Infer+ncia) "
                    "para classifica+o+Aes de documentos. Foque em clareza e objetividade. Responda em JSON v+ilido."
                ),
            },
            {"role": "user", "content": prompt},
        ]
        response = self._chat_completion(messages, self.config.get("model"))
        parsed = self._parse_optional_response(response)
        parsed["stage"] = "i3"
        return parsed

    # -------------------------------------------------------------------------
    # Prompt Templates
    # -------------------------------------------------------------------------
    def _render_primary_prompt(
        self,
        text: str,
        metadata: Dict,
        context_summary: str,
        known_categories: List[str],
        category_profiles: Dict[str, Dict[str, List[str]]],
        category_document_profiles: Dict[str, Dict[str, Any]],
        category_feedback_profiles: Dict[str, Dict[str, Any]],
    ) -> str:
        category_brief = {
            cat: profile for cat, profile in category_profiles.items() if profile.get("top_keywords")
        }
        document_brief = {}
        for cat, doc_profile in category_document_profiles.items():
            document_brief[cat] = {
                "top_terms": doc_profile.get("top_terms", [])[:12],
                "recent_documents": doc_profile.get("recent_documents", []),
                "document_count": doc_profile.get("document_count", 0),
                "last_scan": doc_profile.get("last_scan"),
            }
            entry = category_brief.setdefault(cat, {})
            if document_brief[cat]["top_terms"]:
                entry["document_top_terms"] = document_brief[cat]["top_terms"]
            if document_brief[cat]["recent_documents"]:
                entry["document_examples"] = document_brief[cat]["recent_documents"]
        feedback_brief: Dict[str, Dict[str, Any]] = {}
        for cat, feedback in category_feedback_profiles.items():
            feedback_brief[cat] = {
                "positive": feedback.get("positive", 0),
                "negative": feedback.get("negative", 0),
                "approval_ratio": feedback.get("approval_ratio"),
                "reprocess_requests": feedback.get("reprocess_requests", 0),
                "knowledge_approvals": feedback.get("knowledge_approvals", 0),
                "knowledge_rejections": feedback.get("knowledge_rejections", 0),
                "last_update": feedback.get("last_update"),
                "keywords_flagged": [kw for kw, _ in feedback.get("keywords_flagged", [])[:6]],
                "keywords_promoted": [kw for kw, _ in feedback.get("keywords_promoted", [])[:6]],
            }
            entry = category_brief.setdefault(cat, {})
            entry.setdefault("feedback", feedback_brief[cat])
        template = {
            "document_name": metadata.get("file_name"),
            "instructions": {
                "objective": "Classificar o documento por categoria principal, tema e +ireas secund+irias.",
                "confidence_format": "Valor percentual de 0 a 100.",
                "new_category_rule": "Se n+uo houver categoria adequada, proponha uma nova categoria com justificativa.",
                "context": context_summary,
                "known_categories": known_categories or ["tecnologia", "juridico", "financeiro", "compliance", "outros"],
                "knowledge_usage": (
                    "Quando sugerir nova categoria, descreva claramente porque ela difere das categorias conhecidas. "
                    "Sempre forne+oa justificativa baseada em evid+ncias textuais espec+ficas."
                ),
                "document_knowledge_guidance": (
                    "Considere tamb+m os termos caracter+sticos aprendidos a partir de arquivos reais confirmados em cada categoria. "
                    "Se o documento atual divergir radicalmente desse hist+rico, explique a diferen+oa."
                ),
                "feedback_guidance": (
                    "Analise o historico de feedback humano por categoria (aprovacoes, rejections, pedidos de reanalise). "
                    "Evite repetir padroes negativos (keywords_flagged) e realce a aderencia aos sinais positivos."
                ),
                "category_profiles": category_brief,
                "validation_layers": [
                    "Evidencie a ader+ncia da categoria escolhida citando palavras-chave relevantes.",
                    "Informe categorias alternativas relevantes com justificativas e grau de match.",
                    "Classifique a necessidade de multiatribui+o+uo (categorias extras) quando houver forte sobreposi+o+uo."
                ],
            },
            "category_document_profiles": document_brief,
            "category_feedback_profiles": feedback_brief,
            "output_schema": {
                "categoria_principal": "string",
                "tema": "string",
                "areas_secundarias": "array[string]",
                "confianca": "number",
                "justificativa": "string",
                "motivos_chave": "array[string]",
                "nova_categoria_sugerida": "string|null",
            },
            "document_excerpt": text[:4000],
        }
        return json.dumps(template, ensure_ascii=False)

    def _render_cross_prompt(
        self,
        primary: Dict,
        text: str,
        metadata: Dict,
        known_categories: List[str],
        category_profiles: Dict[str, Dict[str, List[str]]],
        category_document_profiles: Dict[str, Dict[str, Any]],
        category_feedback_profiles: Dict[str, Dict[str, Any]],
    ) -> str:
        category_brief = {
            cat: profile for cat, profile in category_profiles.items() if profile.get("top_keywords")
        }
        document_brief = {
            cat: {
                "top_terms": profile.get("top_terms", [])[:12],
                "recent_documents": profile.get("recent_documents", []),
                "document_count": profile.get("document_count", 0),
            }
            for cat, profile in category_document_profiles.items()
        }
        feedback_brief = {
            cat: {
                "positive": stats.get("positive", 0),
                "negative": stats.get("negative", 0),
                "approval_ratio": stats.get("approval_ratio"),
                "reprocess_requests": stats.get("reprocess_requests", 0),
                "knowledge_rejections": stats.get("knowledge_rejections", 0),
            }
            for cat, stats in category_feedback_profiles.items()
        }
        template = {
            "document_name": metadata.get("file_name"),
            "primary_result": primary,
            "feedback_profiles": feedback_brief,
            "instructions": {
                "task": "Validar a an+ilise prim+iria destacando concord+oncia, ajustes de confian+oa e riscos.",
                "expected_fields": {
                    "agreement": "string",
                    "confidence_adjustment": "number (-20 a +20)",
                    "risks": "array[string]",
                    "notes": "string",
                },
                "known_categories": known_categories,
                "category_profiles": category_brief,
                "document_knowledge_profiles": document_brief,
                "feedback_profiles": feedback_brief,
                "feedback_context": (
                    "Considere o historico de feedback humano para confirmar ajustes de confianca e riscos antes da decisao final."
                ),
                "consistency_checks": [
                    "Caso discorde da categoria proposta, indique alternativa e evid+ncias.",
                    "Avalie a necessidade de m+ltiplas categorias e atribua um score de match."
                ],
            },
            "document_excerpt": text[:2000],
        }
        return json.dumps(template, ensure_ascii=False)

    def _render_i3_prompt(
        self,
        primary: Dict,
        cross: Dict,
        text: str,
        metadata: Dict,
        context_summary: str,
        known_categories: List[str],
        category_profiles: Dict[str, Dict[str, List[str]]],
        category_document_profiles: Dict[str, Dict[str, Any]],
        category_feedback_profiles: Dict[str, Dict[str, Any]],
    ) -> str:
        category_brief = {
            cat: profile for cat, profile in category_profiles.items() if profile.get("top_keywords")
        }
        document_brief = {
            cat: {
                "top_terms": profile.get("top_terms", [])[:12],
                "recent_documents": profile.get("recent_documents", []),
                "document_count": profile.get("document_count", 0),
            }
            for cat, profile in category_document_profiles.items()
        }
        feedback_brief = {
            cat: {
                "positive": stats.get("positive", 0),
                "negative": stats.get("negative", 0),
                "approval_ratio": stats.get("approval_ratio"),
                "reprocess_requests": stats.get("reprocess_requests", 0),
                "knowledge_rejections": stats.get("knowledge_rejections", 0),
                "last_update": stats.get("last_update"),
            }
            for cat, stats in category_feedback_profiles.items()
        }
        template = {
            "document_name": metadata.get("file_name"),
            "primary_result": primary,
            "cross_validation": cross,
            "context_summary": context_summary,
            "feedback_profiles": feedback_brief,
            "instructions": {
                "objective": "Gerar explica+o+uo I3 (Insight, Impacto, Infer+ncia) e motivo do score final.",
                "expected_fields": {
                    "insight": "string",
                    "impacto": "string",
                    "inferencia": "string",
                    "reliability_reasoning": "string",
                },
                "known_categories": known_categories,
                "category_profiles": category_brief,
                "document_knowledge_profiles": document_brief,
                "feedback_traceability": (
                    "Relacione a explicacao I3 com os aprendizados de feedback humano (motivos positivos e alertas recorrentes)."
                ),
                "traceability": [
                    "Forne+oa um why-trace conectando evid+ncias +as regras e categorias pr+-definidas.",
                    "Liste palavras-chave determinantes para a decis+uo e correlacione com hist+rico conhecido."
                ],
            },
        }
        return json.dumps(template, ensure_ascii=False)

    def _render_reinforcement_prompt(
        self,
        text: str,
        metadata: Dict,
        previous_result: Dict,
        known_categories: List[str],
        category_profiles: Dict[str, Dict[str, List[str]]],
        category_document_profiles: Dict[str, Dict[str, Any]],
        category_feedback_profiles: Dict[str, Dict[str, Any]],
    ) -> str:
        category_brief = {
            cat: profile for cat, profile in category_profiles.items() if profile.get("top_keywords")
        }
        document_brief = {
            cat: {
                "top_terms": profile.get("top_terms", [])[:12],
                "recent_documents": profile.get("recent_documents", []),
                "document_count": profile.get("document_count", 0),
            }
            for cat, profile in category_document_profiles.items()
        }
        feedback_brief = {
            cat: {
                "positive": stats.get("positive", 0),
                "negative": stats.get("negative", 0),
                "approval_ratio": stats.get("approval_ratio"),
                "reprocess_requests": stats.get("reprocess_requests", 0),
                "knowledge_rejections": stats.get("knowledge_rejections", 0),
            }
            for cat, stats in category_feedback_profiles.items()
        }
        template = {
            "document_name": metadata.get("file_name"),
            "previous_result": previous_result,
            "feedback_profiles": feedback_brief,
            "instructions": {
                "focus": "Busque evid+ncias adicionais no texto para elevar confian+oa. Caso n+uo seja poss+vel, proponha nova categoria.",
                "fallback": "Se persistir incerteza, retorne categoria como 'N+uo identificada' e sugira nova categoria plaus+vel.",
                "known_categories": known_categories,
                "category_profiles": category_brief,
                "document_knowledge_profiles": document_brief,
                "feedback_context": (
                    "Considere o historico de feedback para revisar pontos criticos, palavras sinalizadas e pedidos anteriores de reanalise."
                ),
            },
            "document_excerpt": text[:5000],
        }
        return json.dumps(template, ensure_ascii=False)

    # -------------------------------------------------------------------------
    # Response Parsing and Combination
    # -------------------------------------------------------------------------
    def _parse_response(self, response, fallback: Optional[Dict] = None) -> Dict:
        if not response:
            return fallback or {}
        content = ""
        try:
            content = self._extract_content_text(response)
            if not content:
                raise ValueError("conteudo vazio")
            data = json.loads(content)
            return data
        except (KeyError, AttributeError, json.JSONDecodeError) as exc:
            logging.error("Failed to parse GPT response. Error: %s | raw=%r", exc, content)
            return fallback or {}
        except ValueError as exc:
            logging.error("Failed to parse GPT response. Error: %s | raw=%r", exc, content)
            return fallback or {}

    def _parse_optional_response(self, response) -> Dict:
        parsed = self._parse_response(response, {})
        if not isinstance(parsed, dict):
            return {}
        return parsed

    def _combine_outputs(self, primary: Dict, cross: Dict, i3: Dict) -> Dict:
        confidence_raw = primary.get("confianca", primary.get("confidence", 0))
        cross_adj = cross.get("confidence_adjustment", 0)
        confidence_raw = self._as_float(confidence_raw)
        cross_adj = self._as_float(cross_adj)
        if confidence_raw <= 1.0:
            combined_confidence = (confidence_raw * 100.0) + cross_adj
        else:
            combined_confidence = confidence_raw + cross_adj
        combined_confidence = max(0.0, min(100.0, combined_confidence))
        confidence_ratio = round(combined_confidence / 100.0, 4)
        category = primary.get("categoria_principal") or "N+uo identificada"
        result = {
            "categoria": category,
            "tema": primary.get("tema", "Tema n+uo identificado"),
            "areas_secundarias": primary.get("areas_secundarias", []),
            "confidence_percent": round(combined_confidence, 2),
            "confidence": confidence_ratio,
            "nova_categoria_sugerida": primary.get("nova_categoria_sugerida"),
            "justificativa": primary.get("justificativa", ""),
            "motivos_chave": primary.get("motivos_chave", []),
            "cross_validation": cross,
            "i3_explanation": i3,
            "confidence_reason": i3.get("reliability_reasoning") if i3 else cross.get("notes"),
            "raw_primary": primary,
        }
        return result


    def _apply_knowledge_validation(
        self,
        combined: Dict,
        known_categories: List[str],
        knowledge_matches: List[Dict[str, float]],
        document_matches: List[Dict[str, float]],
        category_profiles: Dict[str, Dict[str, List[str]]],
        category_document_profiles: Dict[str, Dict[str, Any]],
        category_feedback_profiles: Dict[str, Dict[str, Any]],
    ) -> None:
        suggestion = combined.get("nova_categoria_sugerida")
        if suggestion:
            resolved = self._resolve_category_alias(suggestion, known_categories)
            if resolved != suggestion:
                logging.info(
                    "Nova categoria sugerida normalizada: %s -> %s",
                    suggestion,
                    resolved,
                )
                combined["nova_categoria_sugerida"] = resolved

        combined.setdefault("justificativa", "")

        primary_category = combined.get("categoria")
        knowledge_lookup = {item["category"]: item for item in knowledge_matches}
        document_lookup = {item["category"]: item for item in document_matches}
        feedback_lookup = category_feedback_profiles or {}

        primary_score = knowledge_lookup.get(primary_category, {}).get("best_match", 0.0)
        primary_doc_score = document_lookup.get(primary_category, {}).get("score", 0.0)
        feedback_stats = feedback_lookup.get(primary_category, {})

        top_match = knowledge_matches[0] if knowledge_matches else None
        top_document = document_matches[0] if document_matches else None

        if primary_category not in known_categories:
            resolved_top = None
            justification_reason = ""
            if top_match and top_match.get("best_match", 0.0) >= 0.35:
                resolved_candidate = self._resolve_category_alias(top_match["category"], known_categories)
                if resolved_candidate in known_categories:
                    resolved_top = resolved_candidate
                    justification_reason = f"match {top_match['best_match']:.2f} na base estruturada"
            if top_document and top_document.get("score", 0.0) >= 0.4:
                resolved_candidate = self._resolve_category_alias(top_document["category"], known_categories)
                if resolved_candidate in known_categories:
                    if not resolved_top or top_document["score"] > (top_match["best_match"] if top_match else 0.0):
                        resolved_top = resolved_candidate
                        justification_reason = f"similaridade {top_document['score']:.2f} com arquivos reais"
            if resolved_top:
                original_category = primary_category or "nova categoria"
                combined["nova_categoria_sugerida"] = original_category
                combined["categoria"] = resolved_top
                combined["justificativa"] += (
                    f"\nCategoria ajustada para {resolved_top} pela camada documental ({justification_reason})."
                )
                logging.info(
                    "Categoria ajustada de %s para %s devido a %s",
                    original_category,
                    resolved_top,
                    justification_reason,
                )
                primary_category = resolved_top
                primary_score = knowledge_lookup.get(primary_category, {}).get("best_match", 0.0)
                primary_doc_score = document_lookup.get(primary_category, {}).get("score", 0.0)
                feedback_stats = feedback_lookup.get(primary_category, {})

        strong_suggestions: List[Tuple[str, float]] = []
        for match in knowledge_matches:
            score = match.get("best_match", 0.0)
            if score >= self.secondary_strong_threshold:
                strong_suggestions.append((self._resolve_category_alias(match["category"], known_categories), score))
        for match in document_matches:
            score = match.get("score", 0.0)
            if score >= self.secondary_strong_threshold:
                strong_suggestions.append((self._resolve_category_alias(match["category"], known_categories), score))
        if strong_suggestions:
            unique_strong: Dict[str, float] = {}
            for cat, score in strong_suggestions:
                if not cat:
                    continue
                if score > unique_strong.get(cat, 0.0):
                    unique_strong[cat] = score
            combined["strong_category_suggestions"] = [
                (cat, round(score, 4)) for cat, score in unique_strong.items()
            ]
            combined.setdefault("areas_secundarias", [])
            primary_category = combined.get("categoria")
            for cat in unique_strong:
                if not cat or cat == primary_category:
                    continue
                if cat not in combined["areas_secundarias"]:
                    combined["areas_secundarias"].append(cat)

        secondary_candidates: List[Tuple[str, str, float]] = []
        for match in knowledge_matches:
            if match["category"] == primary_category or match.get("best_match", 0.0) < self.secondary_structured_threshold:
                continue
            resolved_secondary = self._resolve_category_alias(match["category"], known_categories)
            if resolved_secondary and resolved_secondary != primary_category:
                secondary_candidates.append((resolved_secondary, "base estruturada", match["best_match"]))
        for match in document_matches:
            if match["category"] == primary_category or match.get("score", 0.0) < self.secondary_document_threshold:
                continue
            resolved_secondary = self._resolve_category_alias(match["category"], known_categories)
            if resolved_secondary and resolved_secondary != primary_category:
                secondary_candidates.append((resolved_secondary, "arquivos reais", match["score"]))
        if secondary_candidates:
            combined.setdefault("areas_secundarias", [])
            for resolved_secondary, origin, value in secondary_candidates:
                if resolved_secondary not in combined["areas_secundarias"]:
                    combined["areas_secundarias"].append(resolved_secondary)
                    combined["justificativa"] += (
                        f"\nCategoria adicional sugerida ({resolved_secondary}) com suporte da {origin} (score {value:.2f})."
                    )

        if top_document:
            resolved_doc = self._resolve_category_alias(top_document["category"], known_categories)
            doc_score = top_document.get("score", 0.0)
            if (
                resolved_doc
                and resolved_doc != primary_category
                and doc_score >= 0.55
                and (
                    doc_score >= primary_doc_score + 0.05
                    or knowledge_lookup.get(resolved_doc, {}).get("best_match", 0.0) >= primary_score + 0.05
                )
            ):
                combined["justificativa"] += (
                    f"\nCategoria ajustada para {resolved_doc} pela similaridade com arquivos reais (score {doc_score:.2f})."
                )
                combined.setdefault("areas_secundarias", [])
                if primary_category and primary_category not in combined["areas_secundarias"]:
                    combined["areas_secundarias"].append(primary_category)
                combined["categoria"] = resolved_doc
                primary_category = resolved_doc
                primary_score = knowledge_lookup.get(primary_category, {}).get("best_match", 0.0)
                primary_doc_score = doc_score
                feedback_stats = feedback_lookup.get(primary_category, feedback_stats or {})

        if primary_score >= 0.5:
            boost = 0.07
        elif primary_score >= 0.4:
            boost = 0.05
        elif primary_score >= 0.3:
            boost = 0.03
        else:
            boost = 0.0

        if primary_doc_score >= 0.6:
            doc_boost = 0.08
        elif primary_doc_score >= 0.5:
            doc_boost = 0.06
        elif primary_doc_score >= 0.4:
            doc_boost = 0.04
        else:
            doc_boost = 0.0

        feedback_adjustment = 0.0
        feedback_details = {
            "approval_ratio": None,
            "positive": 0,
            "negative": 0,
            "reprocess_requests": 0,
            "knowledge_rejections": 0,
            "adjustment": 0.0,
        }
        if feedback_stats:
            approval_ratio = feedback_stats.get("approval_ratio") or 0.0
            positive = feedback_stats.get("positive", 0)
            negative = feedback_stats.get("negative", 0)
            reprocess_requests = feedback_stats.get("reprocess_requests", 0)
            knowledge_rejections = feedback_stats.get("knowledge_rejections", 0)
            if approval_ratio >= 0.75 and positive:
                feedback_adjustment += 0.03
            elif approval_ratio < 0.5 and negative >= positive:
                feedback_adjustment -= 0.05
            penalty = min(0.08, 0.01 * (reprocess_requests + knowledge_rejections))
            feedback_adjustment -= penalty
            feedback_details.update(
                {
                    "approval_ratio": round(approval_ratio, 4),
                    "positive": positive,
                    "negative": negative,
                    "reprocess_requests": reprocess_requests,
                    "knowledge_rejections": knowledge_rejections,
                    "adjustment": round(feedback_adjustment, 4),
                }
            )
            combined["justificativa"] += (
                f"\nFeedback historico da categoria {primary_category}: +{positive}/-{negative}, aprovacao={approval_ratio:.2f}, reprocessos={reprocess_requests}."
            )

        raw_boost = boost + doc_boost + feedback_adjustment
        total_boost = max(-0.12, min(0.15, raw_boost))
        logging.info(
            "Camadas combinadas para categoria %s -> base=%.2f, documental=%.2f, feedback=%.2f, total=%.2f",
            primary_category,
            boost,
            doc_boost,
            feedback_adjustment,
            total_boost,
        )
        if total_boost != 0.0:
            combined["confidence"] = min(0.99, round(combined.get("confidence", 0.0) + total_boost, 4))
            combined["confidence_percent"] = round(combined["confidence"] * 100, 2)
            adjustment_label = "incrementada" if total_boost > 0 else "reduzida"
            combined["justificativa"] += (
                f"\nConfianca {adjustment_label} pelas camadas historicas (delta={total_boost*100:.1f} p.p.)."
            )

        if feedback_stats:
            flagged = [kw for kw, _ in feedback_stats.get("keywords_flagged", [])[:6]]
            promoted = [kw for kw, _ in feedback_stats.get("keywords_promoted", [])[:6]]
            if promoted:
                combined["justificativa"] += (
                    f"\nPalavras reforcadas por feedback: {', '.join(promoted)}."
                )
            if flagged:
                combined["justificativa"] += (
                    f"\nPalavras recorrentes em ajustes: {', '.join(flagged)}."
                )

        feedback_details_parent = combined.setdefault("feedback_adjustment_details", {})
        feedback_details_parent["primary"] = feedback_details

        if primary_category in category_profiles:
            keywords = ", ".join(category_profiles[primary_category].get("top_keywords", [])[:6])
            if keywords:
                combined["justificativa"] += (
                    f"\nPalavras-chave da categoria {primary_category}: {keywords}."
                )
        if primary_category in category_document_profiles:
            terms = category_document_profiles[primary_category].get("top_terms") or []
            if terms:
                combined["justificativa"] += (
                    f"\nTermos caracteristicos dos arquivos reais ({primary_category}): {', '.join(terms[:6])}."
                )

    def _resolve_category_alias(
        self, candidate: str, known_categories: Optional[List[str]] = None
    ) -> str:
        if not candidate:
            return candidate
        normalized = _normalize_category_name(candidate)
        alias_map = {
            "rh": "recursos humanos / saude ocupacional",
            "recursos humanos": "recursos humanos / saude ocupacional",
            "saude ocupacional": "recursos humanos / saude ocupacional",
            "rh/saude ocupacional": "recursos humanos / saude ocupacional",
            "human resources": "recursos humanos / saude ocupacional",
            "inteligencia artificial": "tecnologia",
        }
        if normalized in alias_map:
            candidate = alias_map[normalized]
            normalized = _normalize_category_name(candidate)

        if known_categories:
            lookup = {_normalize_category_name(item): item for item in known_categories}
            if normalized in lookup:
                return lookup[normalized]
            best_match = None
            best_ratio = 0.0
            for item in known_categories:
                ratio = difflib.SequenceMatcher(
                    None, _normalize_category_name(item), normalized
                ).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_match = item
            if best_match and best_ratio >= 0.9:
                return best_match
        return candidate

    def _build_validation_layers(
        self,
        combined: Dict,
        knowledge_matches: List[Dict[str, float]],
        document_matches: List[Dict[str, float]],
        similar_context: List[Tuple[Dict, float]],
        category_profiles: Dict[str, Dict[str, List[str]]],
        category_document_profiles: Dict[str, Dict[str, Any]],
        category_feedback_profiles: Dict[str, Dict[str, Any]],
    ) -> Dict:
        layers = {
            "cross_llm": combined.get("cross_validation", {}),
            "i3": combined.get("i3_explanation", {}),
            "knowledge_matches": knowledge_matches,
            "document_knowledge": document_matches,
            "similar_documents": [
                self._serialize_similarity(item) for item in similar_context[:3]
            ],
        }
        primary_category = combined.get("categoria")
        if primary_category in category_profiles:
            layers["category_profile"] = category_profiles[primary_category]
        if primary_category in category_document_profiles:
            layers["category_document_profile"] = category_document_profiles[primary_category]
        if primary_category in category_feedback_profiles:
            layers["category_feedback_profile"] = category_feedback_profiles[primary_category]
        return layers

    def _ensure_category_folders(self, result: Dict) -> None:
        categories = set()
        primary = result.get("categoria")
        if primary:
            categories.add(primary)
        for secondary in result.get("areas_secundarias") or []:
            if secondary:
                categories.add(secondary)
        suggestion = result.get("nova_categoria_sugerida")
        if suggestion:
            categories.add(suggestion)
        for category in categories:
            try:
                self.knowledge_base.ensure_category_directory(category)
            except Exception as exc:  # pragma: no cover - defensive logging
                logging.warning(
                    "Falha ao garantir pasta de conhecimento para categoria %s: %s",
                    category,
                    exc,
                )

    def _as_float(self, value) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _serialize_similarity(self, item: Tuple[Dict, float]) -> Dict:
        entry, score = item
        return {
            "file_name": entry.get("file_name"),
            "category": entry.get("category"),
            "theme": entry.get("theme"),
            "score": score,
        }

    def _format_similarity_context(self, similar: List[Tuple[Dict, float]]) -> str:
        if not similar:
            return "Sem correspond+ncias fortes em conhecimento local."
        summary = []
        for entry, score in similar:
            summary.append(
                f"- {entry.get('file_name')} | categoria: {entry.get('category')} | tema: {entry.get('theme')} | similaridade: {score:.2f}"
            )
        return "\n".join(summary)

    # -------------------------------------------------------------------------
    # Offline / fallback behaviour
    # -------------------------------------------------------------------------
    def _offline_analysis(self, text: str, metadata: Dict, context_summary: str) -> Dict:
        similar = self.knowledge_base.find_similar(text)
        if similar:
            best_entry, score = similar[0]
            confidence = round(score * 100, 2)
            return {
                "categoria_principal": best_entry.get("category", "outros"),
                "tema": best_entry.get("theme", "Tema baseado em hist+rico"),
                "areas_secundarias": best_entry.get("areas_secundarias", []),
                "confianca": confidence,
                "justificativa": (
                    f"Classifica+o+uo inferida a partir de similaridade hist+rica (score {score:.2f}). Contexto: {context_summary}"
                ),
                "motivos_chave": ["Resultado heur+stico por similaridade textual."],
                "nova_categoria_sugerida": None,
            }
        return {
            "categoria_principal": "outros",
            "tema": "Tema heur+stico",
            "areas_secundarias": [],
            "confianca": 55,
            "justificativa": "Nenhuma correspond+ncia forte encontrada. Classifica+o+uo padr+uo aplicada.",
            "motivos_chave": ["Resultado heur+stico por aus+ncia de contexto hist+rico."],
            "nova_categoria_sugerida": None,
        }

    # -------------------------------------------------------------------------
    # OpenAI chat helper
    # -------------------------------------------------------------------------
    def _chat_completion(self, messages: List[Dict[str, str]], model: Optional[str]):
        client = self._client_instance()
        if client is None:
            raise GPTServiceUnavailable("OpenAI client indisponivel (modo offline).")
        target_model = model or self._chat_model_name()
        payload: Dict = {"model": target_model, "messages": messages}
        temperature = self.config.get("temperature", 0.2)
        if target_model and str(target_model).startswith("gpt-5"):
            # Alguns deployments Azure exigem campo numerico; quando omitido retorna 422.
            temperature = 0.0 if self.azure_enabled else None
        if temperature is not None:
            payload["temperature"] = float(temperature)
        timeout = self.config.get("request_timeout")
        try:
            if timeout:
                response = client.chat.completions.create(timeout=timeout, **payload)
            else:
                response = client.chat.completions.create(**payload)
            return response
        except Exception as exc:
            logging.error("OpenAI chat completion failed: %s", exc)
            raise GPTServiceUnavailable("Falha ao contatar o modelo GPT.", exc)

    def _chat_model_name(self) -> str:
        if self.azure_enabled:
            return self.azure_deployment or ""
        return self.config.get("model") or ""
