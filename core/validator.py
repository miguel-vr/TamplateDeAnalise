import logging
from typing import Dict

from core.gpt_core import GPTCore


class Validator:
    """Confere a confianca do GPT e puxa novas tentativas quando o score nao bate o corte minimo."""

    def __init__(self, config: Dict, gpt_core: GPTCore):
        self.config = config
        self.gpt_core = gpt_core
        self.threshold = float(config.get("confidence_threshold", 0.8))
        self.max_retries = int(config.get("max_retries", 2))

    def ensure_confidence(self, result: Dict, text: str, metadata: Dict) -> Dict:
        """Check confidence and optionally trigger reinforced GPT passes."""
        current = self._normalize_entry(dict(result))
        attempt = 0
        while current.get("confidence", 0) < self.threshold and attempt < self.max_retries:
            attempt += 1
            logging.info(
                "Confidence %.2f below threshold %.2f. Triggering reinforced analysis (attempt %s).",
                current.get("confidence", 0),
                self.threshold,
                attempt,
            )
            reanalysis = self.gpt_core.reanalyze_with_reinforcement(text, metadata, current)
            if not reanalysis:
                break
            merged = dict(current)
            merged.update(reanalysis)
            current = self._normalize_entry(merged)

        if current.get("confidence", 0) < self.threshold:
            logging.warning(
                "Confidence remains below threshold after %s attempts. Flagging as N+uo identificada.",
                self.max_retries,
            )
            current["categoria"] = "N+uo identificada"
            current.setdefault("nova_categoria_sugerida", "Categoria a ser definida pelo usu+irio")
            current.setdefault(
                "justificativa",
                current.get("justificativa", "")
                + "\nConfian+oa insuficiente. Recomenda-se revis+uo humana e eventual cria+o+uo de nova categoria.",
            )
            current = self._normalize_entry(current, force_min=self.threshold / 2)
        current["validation_attempts"] = attempt
        return current

    def _normalize_entry(self, data: Dict, force_min: float = 0.0) -> Dict:
        """Ensure confidence metrics stay consistent between 0-1 ratio and 0-100 percent."""
        confidence_percent = data.get("confidence_percent")
        confidence_field = data.get("confidence")
        confianca = data.get("confianca")

        values = [confidence_percent, confidence_field, confianca]
        numeric_values = []
        for value in values:
            if value is None:
                continue
            try:
                numeric_values.append(float(value))
            except (TypeError, ValueError):
                continue

        confidence_ratio = 0.0
        if numeric_values:
            best = max(numeric_values)
            if best <= 1.0:
                confidence_ratio = best
                confidence_percent = best * 100.0
            else:
                confidence_percent = best
                confidence_ratio = best / 100.0
        else:
            confidence_percent = force_min * 100.0
            confidence_ratio = force_min

        if confidence_ratio < force_min:
            confidence_ratio = force_min
            confidence_percent = force_min * 100.0

        data["confidence"] = round(confidence_ratio, 4)
        data["confidence_percent"] = round(confidence_percent, 2)
        data["confianca"] = round(confidence_percent, 2)
        return data
