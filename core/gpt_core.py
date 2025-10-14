import difflib
import os
import unicodedata
import json
import logging
from typing import Dict, List, Optional, Tuple

from core.knowledge_base import KnowledgeBase


def _normalize_category_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    stripped = "".join(ch for ch in normalized if ch.isalnum() or ch.isspace())
    return stripped.lower().strip()


class GPTServiceUnavailable(Exception):
    """Raised when the GPT service cannot be reached or returns an authorization error."""

    def __init__(self, message: str, original: Optional[Exception] = None):
        super().__init__(message)
        self.original = original

try:
    from openai import OpenAI, AzureOpenAI
except ImportError:  # pragma: no cover - library not installed yet
    OpenAI = None  # type: ignore
    AzureOpenAI = None  # type: ignore


class GPTCore:
    """Encapsulates all GPT interactions for document understanding."""

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
        similar_context = self.knowledge_base.find_similar(text)
        context_summary = self._format_similarity_context(similar_context)
        known_categories = self.knowledge_base.known_categories()
        category_profiles = self.knowledge_base.category_profiles()
        category_profiles = self.knowledge_base.category_profiles()
        logging.debug("Similar context used in prompt: %s", context_summary)

        if self.offline_mode:
            primary = self._offline_analysis(text, metadata, context_summary)
            cross = {"agreement": "offline", "confidence_adjustment": 0, "notes": "Offline mode - heuristic result"}
            i3 = {
                "explanation": primary.get("justificativa", "Análise heurística baseada em similaridade."),
                "reliability_reasoning": "Offline similarity heuristic",
            }
        else:
            primary = self._run_primary_prompt(text, metadata, context_summary, known_categories, category_profiles)
            cross = self._run_cross_validation(primary, text, metadata, known_categories, category_profiles)
            i3 = self._run_i3_layer(
                primary, cross, text, metadata, context_summary, known_categories, category_profiles
            )

        combined = self._combine_outputs(primary, cross, i3)
        combined["similar_context"] = [self._serialize_similarity(item) for item in similar_context]
        knowledge_matches = self.knowledge_base.category_match_report(text, top_n=6)
        combined["knowledge_matches"] = knowledge_matches
        self._apply_knowledge_validation(combined, known_categories, knowledge_matches, category_profiles)
        combined["validation_layers"] = self._build_validation_layers(
            combined, knowledge_matches, similar_context, category_profiles
        )
        combined["known_categories_snapshot"] = known_categories
        return combined

    def reanalyze_with_reinforcement(self, text: str, metadata: Dict, previous_result: Dict) -> Dict:
        """Run a reinforced analysis when confidence is low."""
        if self.offline_mode:
            return self._offline_analysis(text, metadata, "Offline reanalysis (heuristic)")
        known_categories = self.knowledge_base.known_categories()
        category_profiles = self.knowledge_base.category_profiles()
        similar_context = self.knowledge_base.find_similar(text)
        messages = [
            {
                "role": "system",
                "content": (
                    "Você é um especialista em classificação de documentos corporativos. "
                    "Recebeu uma classificação anterior com baixa confiança. "
                    "Reavalie cuidadosamente considerando as observações abaixo."
                ),
            },
            {
                "role": "user",
                "content": self._render_reinforcement_prompt(
                    text, metadata, previous_result, known_categories, category_profiles
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
        self._apply_knowledge_validation(parsed, known_categories, knowledge_matches, category_profiles)
        parsed["validation_layers"] = self._build_validation_layers(
            parsed, knowledge_matches, similar_context, category_profiles
        )
        parsed["similar_context"] = [self._serialize_similarity(item) for item in similar_context]
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
    ) -> Dict:
        prompt = self._render_primary_prompt(
            text, metadata, context_summary, known_categories, category_profiles
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "Você é um classificador especialista em documentação corporativa. "
                    "Classifique documentos por categoria e tema considerando o contexto completo."
                    " Responda sempre em JSON válido."
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
    ) -> Dict:
        prompt = self._render_cross_prompt(primary, text, metadata, known_categories, category_profiles)
        messages = [
            {
                "role": "system",
                "content": (
                    "Você atua como auditor independente validando classificações de documentos. "
                    "Avalie coerência, confiança e possíveis erros. Responda em JSON válido."
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
    ) -> Dict:
        prompt = self._render_i3_prompt(
            primary, cross, text, metadata, context_summary, known_categories, category_profiles
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "Você gera explicações estruturadas (camada I3 - Insight, Impacto, Inferência) "
                    "para classificações de documentos. Foque em clareza e objetividade. Responda em JSON válido."
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
    ) -> str:
        category_brief = {
            cat: profile for cat, profile in category_profiles.items() if profile.get("top_keywords")
        }
        template = {
            "document_name": metadata.get("file_name"),
            "instructions": {
                "objective": "Classificar o documento por categoria principal, tema e áreas secundárias.",
                "confidence_format": "Valor percentual de 0 a 100.",
                "new_category_rule": "Se não houver categoria adequada, proponha uma nova categoria com justificativa.",
                "context": context_summary,
                "known_categories": known_categories or ["tecnologia", "juridico", "financeiro", "compliance", "outros"],
                "knowledge_usage": (
                    "Quando sugerir nova categoria, descreva claramente porque ela difere das categorias conhecidas. "
                    "Sempre forneça justificativa baseada em evidências textuais específicas."
                ),
                "category_profiles": category_brief,
                "validation_layers": [
                    "Evidencie a aderência da categoria escolhida citando palavras-chave relevantes.",
                    "Informe categorias alternativas relevantes com justificativas e grau de match.",
                    "Classifique a necessidade de multiatribuição (categorias extras) quando houver forte sobreposição."
                ],
            },
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
    ) -> str:
        category_brief = {
            cat: profile for cat, profile in category_profiles.items() if profile.get("top_keywords")
        }
        template = {
            "document_name": metadata.get("file_name"),
            "primary_result": primary,
            "instructions": {
                "task": "Validar a análise primária destacando concordância, ajustes de confiança e riscos.",
                "expected_fields": {
                    "agreement": "string",
                    "confidence_adjustment": "number (-20 a +20)",
                    "risks": "array[string]",
                    "notes": "string",
                },
                "known_categories": known_categories,
                "category_profiles": category_brief,
                "consistency_checks": [
                    "Caso discorde da categoria proposta, indique alternativa e evidências.",
                    "Avalie a necessidade de múltiplas categorias e atribua um score de match."
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
    ) -> str:
        category_brief = {
            cat: profile for cat, profile in category_profiles.items() if profile.get("top_keywords")
        }
        template = {
            "document_name": metadata.get("file_name"),
            "primary_result": primary,
            "cross_validation": cross,
            "context_summary": context_summary,
            "instructions": {
                "objective": "Gerar explicação I3 (Insight, Impacto, Inferência) e motivo do score final.",
                "expected_fields": {
                    "insight": "string",
                    "impacto": "string",
                    "inferencia": "string",
                    "reliability_reasoning": "string",
                },
                "known_categories": known_categories,
                "category_profiles": category_brief,
                "traceability": [
                    "Forneça um why-trace conectando evidências às regras e categorias pré-definidas.",
                    "Liste palavras-chave determinantes para a decisão e correlacione com histórico conhecido."
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
    ) -> str:
        category_brief = {
            cat: profile for cat, profile in category_profiles.items() if profile.get("top_keywords")
        }
        template = {
            "document_name": metadata.get("file_name"),
            "previous_result": previous_result,
            "instructions": {
                "focus": "Busque evidências adicionais no texto para elevar confiança. Caso não seja possível, proponha nova categoria.",
                "fallback": "Se persistir incerteza, retorne categoria como 'Não identificada' e sugira nova categoria plausível.",
                "known_categories": known_categories,
                "category_profiles": category_brief,
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
        category = primary.get("categoria_principal") or "Não identificada"
        result = {
            "categoria": category,
            "tema": primary.get("tema", "Tema não identificado"),
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
        category_profiles: Dict[str, Dict[str, List[str]]],
    ) -> None:
        if not knowledge_matches:
            return

        suggestion = combined.get("nova_categoria_sugerida")
        if suggestion:
            resolved = self._resolve_category_alias(suggestion, known_categories)
            if resolved != suggestion:
                logging.info("Nova categoria sugerida normalizada: %s -> %s", suggestion, resolved)
                combined["nova_categoria_sugerida"] = resolved

        primary_category = combined.get("categoria")
        match_lookup = {item["category"]: item for item in knowledge_matches}
        primary_score = match_lookup.get(primary_category, {}).get("best_match", 0.0)
        top_match = knowledge_matches[0]

        if primary_category not in known_categories:
            resolved_top = self._resolve_category_alias(top_match["category"], known_categories)
            if resolved_top in known_categories and top_match["best_match"] >= 0.35:
                original = primary_category or "nova categoria"
                combined["nova_categoria_sugerida"] = original
                combined["categoria"] = resolved_top
                combined.setdefault("justificativa", "")
                combined["justificativa"] += (
                    f"\nCategoria ajustada para {resolved_top} pela camada de conhecimento "
                    f"(match {top_match['best_match']:.2f})."
                )
                primary_category = resolved_top
                primary_score = top_match["best_match"]

        secondary_matches = [
            m for m in knowledge_matches if m["category"] != primary_category and m["best_match"] >= 0.4
        ]
        if secondary_matches:
            combined.setdefault("areas_secundarias", [])
            for match in secondary_matches:
                resolved_secondary = self._resolve_category_alias(match["category"], known_categories)
                if resolved_secondary not in combined["areas_secundarias"]:
                    combined["areas_secundarias"].append(resolved_secondary)
            combined.setdefault("justificativa", "")
            combined["justificativa"] += "".join(
                "\nCategoria adicional sugerida ({}) com match {:.2f}.".format(
                    self._resolve_category_alias(match["category"], known_categories),
                    match["best_match"],
                )
                for match in secondary_matches
            )

        if primary_score >= 0.5:
            boost = 0.07
        elif primary_score >= 0.4:
            boost = 0.05
        elif primary_score >= 0.3:
            boost = 0.03
        else:
            boost = 0.0
        if boost:
            combined["confidence"] = min(0.99, round(combined.get("confidence", 0.0) + boost, 4))
            combined["confidence_percent"] = round(combined["confidence"] * 100, 2)

        if primary_category in category_profiles:
            keywords = ", ".join(category_profiles[primary_category].get("top_keywords", [])[:6])
            if keywords:
                combined.setdefault("justificativa", "")
                combined["justificativa"] += (
                    f"\nPalavras-chave da categoria {primary_category}: {keywords}."
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
        similar_context: List[Tuple[Dict, float]],
        category_profiles: Dict[str, Dict[str, List[str]]],
    ) -> Dict:
        layers = {
            "cross_llm": combined.get("cross_validation", {}),
            "i3": combined.get("i3_explanation", {}),
            "knowledge_matches": knowledge_matches,
            "similar_documents": [
                self._serialize_similarity(item) for item in similar_context[:3]
            ],
        }
        primary_category = combined.get("categoria")
        if primary_category in category_profiles:
            layers["category_profile"] = category_profiles[primary_category]
        return layers

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
            return "Sem correspondências fortes em conhecimento local."
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
                "tema": best_entry.get("theme", "Tema baseado em histórico"),
                "areas_secundarias": best_entry.get("areas_secundarias", []),
                "confianca": confidence,
                "justificativa": (
                    f"Classificação inferida a partir de similaridade histórica (score {score:.2f}). Contexto: {context_summary}"
                ),
                "motivos_chave": ["Resultado heurístico por similaridade textual."],
                "nova_categoria_sugerida": None,
            }
        return {
            "categoria_principal": "outros",
            "tema": "Tema heurístico",
            "areas_secundarias": [],
            "confianca": 55,
            "justificativa": "Nenhuma correspondência forte encontrada. Classificação padrão aplicada.",
            "motivos_chave": ["Resultado heurístico por ausência de contexto histórico."],
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
