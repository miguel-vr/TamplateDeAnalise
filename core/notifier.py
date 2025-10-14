import json
import logging
import urllib.request
from typing import Dict, List, Optional


class TeamsNotifier:
    """Send Adaptive Card summaries to Microsoft Teams via incoming webhook."""

    def __init__(self, webhook_url: str) -> None:
        self.webhook_url = (webhook_url or "").strip()

    def enabled(self) -> bool:
        return bool(self.webhook_url)

    def send_analysis_summary(self, payload: Dict) -> None:
        if not self.enabled():
            return
        card = self._build_card(payload)
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
            self.webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                if response.status >= 300:
                    logging.error(
                        "Adaptive Card webhook retornou status %s ao enviar resumo para %s",
                        response.status,
                        payload.get("file_name"),
                    )
        except Exception as exc:
            logging.exception(
                "Falha ao enviar Adaptive Card para arquivo %s: %s",
                payload.get("file_name"),
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
