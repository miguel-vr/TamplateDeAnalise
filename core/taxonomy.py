import math
import unicodedata
from typing import Dict, List, Tuple


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    ascii_only = "".join(ch for ch in normalized if ch.isalnum() or ch.isspace())
    return " ".join(ascii_only.lower().split())


class TaxonomyRuleEngine:
    """Rule-based reinforcement layer to augment GPT and knowledge-base decisions."""

    def __init__(self) -> None:
        self.category_profiles: Dict[str, Dict] = {
            "compliance": {
                "keywords": {
                    "compliance": 1.2,
                    "conformidade": 1.1,
                    "auditoria": 1.1,
                    "regulatorio": 1.0,
                    "regulacao": 1.0,
                    "lgpd": 1.3,
                    "governanca": 1.0,
                    "risco": 0.9,
                    "politica": 0.9,
                    "norma": 0.8,
                    "due diligence": 1.2,
                    "controles internos": 1.2,
                    "seguranca do trabalho": 1.1,
                    "saude e seguranca": 1.1,
                }
            },
            "juridico": {
                "keywords": {
                    "clausula": 1.3,
                    "contrato": 1.2,
                    "obrigacao": 1.0,
                    "juridico": 1.1,
                    "lei": 0.9,
                    "penalidade": 1.1,
                    "disposicao": 0.9,
                    "foro": 1.0,
                    "litigio": 1.2,
                    "termo": 0.9,
                    "acordo": 1.0,
                    "responsabilidade civil": 1.3,
                }
            },
            "financeiro": {
                "keywords": {
                    "nota fiscal": 1.4,
                    "faturamento": 1.1,
                    "orcamento": 1.2,
                    "receita": 1.0,
                    "pagamento": 1.0,
                    "contabil": 1.0,
                    "contabilidade": 1.1,
                    "tributacao": 1.3,
                    "ajuste financeiro": 1.2,
                    "imposto": 1.0,
                    "despesa": 1.0,
                    "fluxo de caixa": 1.2,
                }
            },
            "tecnologia": {
                "keywords": {
                    "sistema": 1.0,
                    "software": 1.1,
                    "aplicacao": 1.0,
                    "infraestrutura": 1.0,
                    "ciber": 1.2,
                    "seguranca da informacao": 1.3,
                    "plataforma": 1.0,
                    "cloud": 1.0,
                    "dados": 0.8,
                    "api": 0.9,
                    "algoritmo": 1.1,
                    "prova de conceito": 1.0,
                    "tecnologia": 1.0,
                    "digital": 0.9,
                }
            },
            "recursos humanos / saude ocupacional": {
                "keywords": {
                    "medicina do trabalho": 1.6,
                    "saude ocupacional": 1.5,
                    "laudo medico": 1.6,
                    "laudo": 1.1,
                    "ergonomia": 1.4,
                    "ajuste de jornada": 1.3,
                    "mobiliario": 1.1,
                    "paciente": 1.0,
                    "colaborador": 1.0,
                    "funcionario": 1.0,
                    "rh confidencial": 1.6,
                    "rh": 0.9,
                    "posto de trabalho": 1.2,
                    "exame ocupacional": 1.5,
                    "medico do trabalho": 1.5,
                    "acidente de trabalho": 1.3,
                }
            },
        }
        self.alias_map = {
            "rh": "recursos humanos / saude ocupacional",
            "recursos humanos": "recursos humanos / saude ocupacional",
            "saude ocupacional": "recursos humanos / saude ocupacional",
            "rh/saude ocupacional": "recursos humanos / saude ocupacional",
            "human resources": "recursos humanos / saude ocupacional",
            "seguranca e saude": "recursos humanos / saude ocupacional",
        }
        self.promote_threshold = 1.8
        self.new_category_threshold = 2.5
        self.high_confidence_threshold = 5.0

    def score_text(self, text: str) -> Dict[str, Dict[str, object]]:
        normalized = _normalize_text(text)
        scores: Dict[str, Dict[str, object]] = {}
        for category, profile in self.category_profiles.items():
            score = 0.0
            matches: List[str] = []
            for keyword, weight in profile.get("keywords", {}).items():
                if keyword in normalized:
                    occurrences = normalized.count(keyword)
                    score += weight * occurrences
                    matches.extend([keyword] * occurrences)
            negative_score = 0.0
            for keyword, penalty in profile.get("negative_keywords", {}).items():
                if keyword in normalized:
                    occurrences = normalized.count(keyword)
                    negative_score += penalty * occurrences
            score = max(0.0, score - negative_score)
            scores[category] = {
                "score": round(score, 3),
                "matches": matches[:20],
                "occurrences": len(matches),
            }
        return scores

    def _resolve_alias(self, category: str) -> str:
        normalized = _normalize_text(category)
        if normalized in self.alias_map:
            return self.alias_map[normalized]
        return category

    def _best_match_from_scores(self, scores: Dict[str, Dict[str, object]]) -> Tuple[str, Dict[str, object]]:
        best_category = "outros"
        best_payload = {"score": 0.0, "matches": [], "occurrences": 0}
        for category, payload in scores.items():
            score = float(payload.get("score", 0.0))
            if score > best_payload.get("score", 0.0):
                best_category = category
                best_payload = payload
        return best_category, best_payload

    def refine(
        self,
        text: str,
        validation_result: Dict,
        known_categories: List[str],
        knowledge_matches: List[Dict[str, float]],
    ) -> Dict[str, object]:
        scores = self.score_text(text)
        top_category, top_info = self._best_match_from_scores(scores)
        top_score = float(top_info.get("score", 0.0))

        result = dict(validation_result)
        current_category = result.get("categoria") or "outros"
        normalized_current = _normalize_text(current_category)
        normalized_lookup = { _normalize_text(cat): cat for cat in scores }
        current_profile_key = normalized_lookup.get(normalized_current)
        current_score = float(scores.get(current_profile_key, {}).get("score", 0.0)) if current_profile_key else 0.0

        known_lookup = { _normalize_text(cat): cat for cat in known_categories }
        resolved_top = self._resolve_alias(top_category)
        normalized_top = _normalize_text(resolved_top)
        top_in_known = normalized_top in known_lookup
        target_category = current_category
        action = "kept"

        matches_list = top_info.get("matches", [])
        key_terms = ", ".join(sorted(set(matches_list))[:5])
        best_kb_match = max((item.get("best_match", 0.0) for item in knowledge_matches), default=0.0)

        if top_score >= self.promote_threshold and normalized_top != normalized_current:
            if top_in_known:
                target_category = known_lookup[normalized_top]
                action = "promoted_existing"
            elif top_score >= self.new_category_threshold and top_info.get("occurrences", 0) >= 2:
                target_category = resolved_top
                action = "promoted_new_category"
                result["nova_categoria_sugerida"] = resolved_top
            elif top_score >= self.promote_threshold + 0.5:
                target_category = resolved_top
                action = "promoted_strong_alias"
                result["nova_categoria_sugerida"] = resolved_top

        if action.startswith("promoted"):
            result["categoria"] = target_category
            result.setdefault("justificativa", "")
            if key_terms:
                result["justificativa"] += (
                    f"\nCamada heuristica consolidou categoria '{target_category}' "
                    f"(palavras-chave: {key_terms})."
                )
        else:
            if top_score >= self.promote_threshold and normalized_top != normalized_current:
                result.setdefault("areas_secundarias", [])
                if resolved_top not in result["areas_secundarias"]:
                    result["areas_secundarias"].append(resolved_top)
                if key_terms:
                    result.setdefault("justificativa", "")
                    result["justificativa"] += (
                        f"\nCamada heuristica sugeriu '{resolved_top}' como area secundaria "
                        f"(palavras-chave: {key_terms})."
                    )

        heuristic_ratio = min(1.0, top_score / self.high_confidence_threshold)
        composite_scores = {
            "llm": round(float(result.get("confidence", 0.0)), 4),
            "heuristic": round(heuristic_ratio, 4),
            "knowledge": round(float(best_kb_match), 4),
        }
        combined_confidence = (
            composite_scores["llm"] * 0.5
            + composite_scores["heuristic"] * 0.35
            + composite_scores["knowledge"] * 0.15
        )
        result["confidence"] = round(min(0.99, combined_confidence), 4)
        result["confidence_percent"] = round(result["confidence"] * 100, 2)
        result["taxonomy_report"] = {
            "scores": scores,
            "top_category": resolved_top,
            "top_score": top_score,
            "action": action,
            "current_score": current_score,
            "composite_scores": composite_scores,
        }
        return {"result": result, "report": result["taxonomy_report"]}
