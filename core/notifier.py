import json
import logging
import urllib.request
from typing import Dict, List, Optional, Sequence, Tuple


class TeamsNotifier:
    """Disparo os Adaptive Cards no Teams para manter o time avisado sem depender do console."""

    def __init__(self, analysis_webhook_url: str, activity_webhook_url: Optional[str] = None) -> None:
        self.analysis_webhook_url = (analysis_webhook_url or "").strip()
        self.activity_webhook_url = (activity_webhook_url or "").strip()

    def analysis_enabled(self) -> bool:
        return bool(self.analysis_webhook_url)

    def activity_enabled(self) -> bool:
        return bool(self.activity_webhook_url)

    def send_analysis_summary(self, payload: Dict) -> None:
        if not self.analysis_enabled():
            return
        card = self._build_card(payload)
        self._post_card(self.analysis_webhook_url, card, payload.get("file_name"))

    def send_activity_event(
        self,
        title: str,
        message: str,
        facts: Optional[Sequence[Tuple[str, str]]] = None,
        link: Optional[str] = None,
        event_type: str = "",
    ) -> None:
        if not self.activity_enabled():
            return
        fact_items = []
        for item in facts or []:
            if not item:
                continue
            key, value = item
            fact_items.append({"title": str(key), "value": str(value)})
        card_body: List[Dict] = [
            {"type": "TextBlock", "text": title, "weight": "Bolder", "size": "Medium"},
            {"type": "TextBlock", "text": message, "wrap": True, "spacing": "Small"},
        ]
        if fact_items:
            card_body.append({"type": "FactSet", "facts": fact_items})
        if event_type:
            card_body.append(
                {
                    "type": "TextBlock",
                    "text": f"Evento: `{event_type}`",
                    "isSubtle": True,
                    "spacing": "Small",
                }
            )
        card: Dict = {
            "type": "AdaptiveCard",
            "version": "1.4",
            "body": card_body,
        }
        if link:
            card["actions"] = [
                {
                    "type": "Action.OpenUrl",
                    "title": "Abrir recurso",
                    "url": link if link.startswith("http") else f"file:///{link.replace('\\', '/')}",
                }
            ]
        self._post_card(self.activity_webhook_url, card, title)

    def _post_card(self, webhook_url: str, card: Dict, context: Optional[str]) -> None:
        envelope = {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": card,
                }
            ],
        }
        data = json.dumps(envelope).encode("utf-8")
        request = urllib.request.Request(
            webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        logging.debug("Enviando Adaptive Card (%s) para Teams", context or "evento")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                if response.status >= 300:
                    logging.error(
                        "Adaptive Card webhook retornou status %s ao enviar card (%s)",
                        response.status,
                        context or "evento",
                    )
                else:
                    logging.debug("Adaptive Card entregue com sucesso (%s)", context or "evento")
        except Exception as exc:
            logging.exception(
                "Falha ao enviar Adaptive Card (%s): %s",
                context or "evento",
                exc,
            )

    def _build_card(self, payload: Dict) -> Dict:
        file_name = payload.get("file_name", "Documento")
        category = payload.get("category", "N/A")
        confidence = payload.get("confidence_percent", 0.0)
        zip_path = payload.get("zip_path", "-")
        taxonomy = payload.get("taxonomy") or {}
        timeline = payload.get("timeline") or []
        knowledge_matches: List[Dict] = payload.get("knowledge_matches") or []
        summary_text = payload.get("summary", "")[:600]

        timeline_lines = []
        for record in timeline:
            if record.get("status") != "completed":
                continue
            duration = record.get("duration")
            if duration is None:
                continue
            stage = record.get("stage", "etapa")
            timeline_lines.append(f"- {stage}: {duration:.2f}s")
        if not timeline_lines:
            timeline_lines.append("- n/a")

        match_lines = []
        for match in knowledge_matches[:3]:
            match_lines.append(
                f"- {match.get('category')} (top {match.get('best_match', 0.0):.2f} / avg {match.get('average_match', 0.0):.2f})"
            )
        if not match_lines:
            match_lines.append("- sem correspondencias relevantes")

        taxonomy_action = taxonomy.get("action", "kept")
        taxonomy_category = taxonomy.get("top_category", "n/a")
        taxonomy_score = taxonomy.get("top_score", 0.0)
        composite_scores = taxonomy.get("composite_scores") or {}
        composite_line = ", ".join(
            f"{key}={value:.2f}" for key, value in composite_scores.items()
        ) or "n/a"

        card = {
            "type": "AdaptiveCard",
            "version": "1.4",
            "body": [
                {
                    "type": "TextBlock",
                    "text": "Resumo de Classificacao",
                    "weight": "Bolder",
                    "size": "Large",
                },
                {
                    "type": "TextBlock",
                    "text": file_name,
                    "weight": "Bolder",
                    "size": "Medium",
                    "spacing": "Small",
                },
                {
                    "type": "FactSet",
                    "facts": [
                        {"title": "Categoria", "value": category},
                        {"title": "Confianca", "value": f"{confidence:.2f}%"},
                        {"title": "Caminho ZIP", "value": zip_path},
                        {"title": "Taxonomia", "value": f"{taxonomy_action} ({taxonomy_category}, score {taxonomy_score:.2f})"},
                        {"title": "Scores Compostos", "value": composite_line},
                    ],
                },
                {
                    "type": "TextBlock",
                    "text": "Linha do tempo",
                    "weight": "Bolder",
                    "spacing": "Medium",
                },
                {
                    "type": "TextBlock",
                    "text": "\n".join(timeline_lines),
                    "spacing": "Small",
                },
                {
                    "type": "TextBlock",
                    "text": "Conhecimento local",
                    "weight": "Bolder",
                    "spacing": "Medium",
                },
                {
                    "type": "TextBlock",
                    "text": "\n".join(match_lines),
                    "spacing": "Small",
                },
                {
                    "type": "TextBlock",
                    "text": "Resumo",
                    "weight": "Bolder",
                    "spacing": "Medium",
                },
                {
                    "type": "TextBlock",
                    "text": summary_text or "Resumo nao disponivel.",
                    "wrap": True,
                    "spacing": "Small",
                },
            ],
            "actions": [
                {
                    "type": "Action.OpenUrl",
                    "title": "Abrir pasta do artefato",
                    "url": f"file:///{zip_path.replace('\\', '/')}",
                }
            ],
        }
        return card
