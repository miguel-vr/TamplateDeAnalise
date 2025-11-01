from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

try:
    from dotenv import load_dotenv  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    load_dotenv = None  # type: ignore

LOGGER = logging.getLogger(__name__)


def _first_env(aliases: Iterable[str]) -> Optional[str]:
    for alias in aliases:
        value = os.getenv(alias)
        if value not in (None, "", "None"):
            return value
    return None


def _parse_bool(value: Optional[str], default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _parse_int(value: Optional[str], default: int, env_key: str) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        LOGGER.warning("Valor invalido para %s=%s (esperado int). Usando %s.", env_key, value, default)
        return default


def _parse_float(value: Optional[str], default: float, env_key: str) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        LOGGER.warning("Valor invalido para %s=%s (esperado float). Usando %s.", env_key, value, default)
        return default


@dataclass
class Settings:
    """Guardo a configuracao tipada carregada do .env para evitar dicionario solto no projeto."""
    api_key: str = ""
    model: str = "gpt-5"
    cross_validation_model: str = "gpt-5"
    confidence_threshold: float = 0.8
    polling_interval: int = 10
    feedback_polling_interval: int = 10
    processing_workers: int = 2
    log_level: str = "INFO"
    log_file: str = "logs/activity.jsonl"
    text_log_file: str = "logs/system.log"
    knowledge_base_path: str = "knowledge.json"
    category_knowledge_root: str = "knowledge_sources"
    max_retries: int = 3
    temperature: float = 1.0
    request_timeout: float = 60.0
    teams_webhook_url: str = ""
    teams_activity_webhook_url: str = ""
    storage_mode: str = "relative"
    storage_relative_root: str = "folders"
    storage_absolute_root: str = ""
    storage_root: str = "folders"
    storage_auto_create: bool = True
    storage_create_default_categories: bool = True
    input_subdir: str = "entrada"
    processing_subdir: str = "em_processamento"
    processing_fail_subdir: str = "_falhas"
    processed_subdir: str = "processados"
    feedback_subdir: str = "feedback"
    feedback_processed_subdir: str = "processado"
    complex_samples_subdir: str = "complex_samples"
    azure_keyvault_url: str = ""
    use_azure: bool = False
    azure_endpoint: str = ""
    azure_api_key: str = ""
    azure_deployment: str = ""
    azure_api_version: str = "2024-02-01"
    storage_service_user: str = ""
    storage_service_password: str = ""
    storage_service_domain: str = ""
    storage_mount_command: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def for_logging(self) -> Dict[str, Any]:
        sanitized = self.to_dict()
        for key in (
            "api_key",
            "azure_api_key",
            "teams_webhook_url",
            "teams_activity_webhook_url",
            "storage_service_password",
        ):
            if sanitized.get(key):
                sanitized[key] = "***redacted***"
        return sanitized


ENV_ALIASES: Dict[str, Iterable[str]] = {
    "api_key": (
        "DOC_ANALYZER_API_KEY",
        "CLASSIFIER_API_KEY",
        "OPENAI_API_KEY",
        "API_KEY",
    ),
    "model": (
        "DOC_ANALYZER_MODEL",
        "LLM_MODEL",
    ),
    "cross_validation_model": (
        "DOC_ANALYZER_CROSS_MODEL",
        "LLM_CROSS_MODEL",
    ),
    "confidence_threshold": (
        "DOC_ANALYZER_CONFIDENCE_THRESHOLD",
        "CLASSIFIER_CONFIDENCE_THRESHOLD",
    ),
    "polling_interval": (
        "DOC_ANALYZER_POLL_INTERVAL",
        "CLASSIFIER_POLL_INTERVAL",
    ),
    "feedback_polling_interval": (
        "DOC_ANALYZER_FEEDBACK_INTERVAL",
        "CLASSIFIER_FEEDBACK_INTERVAL",
    ),
    "processing_workers": (
        "DOC_ANALYZER_PROCESSING_WORKERS",
        "CLASSIFIER_PROCESSING_WORKERS",
    ),
    "log_level": (
        "DOC_ANALYZER_LOG_LEVEL",
        "CLASSIFIER_LOG_LEVEL",
    ),
    "log_file": (
        "DOC_ANALYZER_LOG_FILE",
        "CLASSIFIER_LOG_FILE",
    ),
    "text_log_file": (
        "DOC_ANALYZER_TEXT_LOG_FILE",
        "CLASSIFIER_TEXT_LOG_FILE",
    ),
    "knowledge_base_path": (
        "DOC_ANALYZER_KNOWLEDGE_BASE_PATH",
        "CLASSIFIER_KNOWLEDGE_BASE_PATH",
    ),
    "category_knowledge_root": (
        "DOC_ANALYZER_CATEGORY_KNOWLEDGE_ROOT",
        "CLASSIFIER_CATEGORY_KNOWLEDGE_ROOT",
    ),
    "max_retries": (
        "DOC_ANALYZER_MAX_RETRIES",
        "CLASSIFIER_MAX_RETRIES",
    ),
    "temperature": (
        "DOC_ANALYZER_TEMPERATURE",
        "CLASSIFIER_TEMPERATURE",
    ),
    "request_timeout": (
        "DOC_ANALYZER_REQUEST_TIMEOUT",
        "LLM_TIMEOUT_S",
    ),
    "teams_webhook_url": (
        "DOC_ANALYZER_TEAMS_WEBHOOK_URL",
        "TEAMS_WEBHOOK_URL",
    ),
    "teams_activity_webhook_url": (
        "DOC_ANALYZER_TEAMS_ACTIVITY_WEBHOOK_URL",
        "TEAMS_ACTIVITY_WEBHOOK_URL",
    ),
    "storage_mode": (
        "DOC_ANALYZER_STORAGE_MODE",
        "CLASSIFIER_STORAGE_MODE",
    ),
    "storage_relative_root": (
        "DOC_ANALYZER_STORAGE_RELATIVE_ROOT",
        "CLASSIFIER_STORAGE_ROOT",
    ),
    "storage_absolute_root": (
        "DOC_ANALYZER_STORAGE_ABSOLUTE_ROOT",
        "CLASSIFIER_STORAGE_ABSOLUTE_ROOT",
    ),
    "storage_auto_create": (
        "DOC_ANALYZER_STORAGE_AUTO_CREATE",
        "CLASSIFIER_STORAGE_AUTO_CREATE",
    ),
    "storage_create_default_categories": (
        "DOC_ANALYZER_STORAGE_CREATE_DEFAULT_CATEGORIES",
        "CLASSIFIER_STORAGE_CREATE_DEFAULT_CATEGORIES",
    ),
    "input_subdir": (
        "DOC_ANALYZER_INPUT_SUBDIR",
        "CLASSIFIER_INPUT_SUBDIR",
    ),
    "processing_subdir": (
        "DOC_ANALYZER_PROCESSING_SUBDIR",
        "CLASSIFIER_PROCESSING_SUBDIR",
    ),
    "processing_fail_subdir": (
        "DOC_ANALYZER_PROCESSING_FAIL_SUBDIR",
        "CLASSIFIER_PROCESSING_FAIL_SUBDIR",
    ),
    "processed_subdir": (
        "DOC_ANALYZER_PROCESSED_SUBDIR",
        "CLASSIFIER_PROCESSED_SUBDIR",
    ),
    "feedback_subdir": (
        "DOC_ANALYZER_FEEDBACK_SUBDIR",
        "CLASSIFIER_FEEDBACK_SUBDIR",
    ),
    "feedback_processed_subdir": (
        "DOC_ANALYZER_FEEDBACK_PROCESSED_SUBDIR",
        "CLASSIFIER_FEEDBACK_PROCESSED_SUBDIR",
    ),
    "complex_samples_subdir": (
        "DOC_ANALYZER_COMPLEX_SAMPLES_SUBDIR",
        "CLASSIFIER_COMPLEX_SAMPLES_SUBDIR",
    ),
    "azure_keyvault_url": (
        "DOC_ANALYZER_AZURE_KEYVAULT_URL",
        "AZURE_KEYVAULT_URL",
    ),
    "use_azure": (
        "DOC_ANALYZER_USE_AZURE",
        "USE_AZURE_OPENAI",
    ),
    "azure_endpoint": (
        "DOC_ANALYZER_AZURE_ENDPOINT",
        "AZURE_OPENAI_ENDPOINT",
        "URL_BASE",
    ),
    "azure_api_key": (
        "DOC_ANALYZER_AZURE_API_KEY",
        "AZURE_OPENAI_KEY",
    ),
    "azure_deployment": (
        "DOC_ANALYZER_AZURE_DEPLOYMENT",
        "AZURE_OPENAI_DEPLOYMENT",
        "DEPLOYMENT_NAME",
    ),
    "azure_api_version": (
        "DOC_ANALYZER_AZURE_API_VERSION",
        "AZURE_OPENAI_API_VERSION",
        "OPENAI_API_VERSION",
    ),
    "storage_service_user": (
        "DOC_ANALYZER_STORAGE_SERVICE_USER",
        "CLASSIFIER_STORAGE_SERVICE_USER",
    ),
    "storage_service_password": (
        "DOC_ANALYZER_STORAGE_SERVICE_PASSWORD",
        "CLASSIFIER_STORAGE_SERVICE_PASSWORD",
    ),
    "storage_service_domain": (
        "DOC_ANALYZER_STORAGE_SERVICE_DOMAIN",
        "CLASSIFIER_STORAGE_SERVICE_DOMAIN",
    ),
    "storage_mount_command": (
        "DOC_ANALYZER_STORAGE_MOUNT_COMMAND",
        "CLASSIFIER_STORAGE_MOUNT_COMMAND",
    ),
}


def load_settings(env_path: Optional[Path] = None) -> Settings:
    defaults = Settings()
    env_path = Path(env_path) if env_path else None
    if load_dotenv and env_path:
        load_dotenv(dotenv_path=str(env_path), override=False)
    elif load_dotenv:
        load_dotenv(override=False)

    values: Dict[str, Any] = {}
    for field in fields(Settings):
        if field.name == "storage_root":
            continue  # handled after parsing the other storage knobs
        aliases = ENV_ALIASES.get(field.name, ())
        raw_value = _first_env(aliases)
        default_value = getattr(defaults, field.name)
        if field.type is bool:
            values[field.name] = _parse_bool(raw_value, default_value)
        elif field.type is int:
            values[field.name] = _parse_int(raw_value, default_value, next(iter(aliases), field.name))
        elif field.type is float:
            values[field.name] = _parse_float(raw_value, default_value, next(iter(aliases), field.name))
        else:
            values[field.name] = raw_value if raw_value is not None else default_value

    settings = Settings(**values)

    storage_mode = (settings.storage_mode or "relative").strip().lower()
    if storage_mode not in {"relative", "absolute", "network"}:
        LOGGER.warning(
            "Storage mode '%s' invalido. Usando 'relative'. Opcoes validas: relative, absolute, network.",
            storage_mode,
        )
        storage_mode = "relative"
    settings.storage_mode = storage_mode

    if settings.storage_mode in {"absolute", "network"} and not settings.storage_absolute_root:
        LOGGER.warning(
            "Storage mode configurado como %s, mas DOC_ANALYZER_STORAGE_ABSOLUTE_ROOT nao foi informado. "
            "Aplicando caminho relativo %s.",
            settings.storage_mode,
            settings.storage_relative_root,
        )
        settings.storage_mode = "relative"

    potential_root = settings.storage_relative_root
    if settings.storage_mode in {"absolute", "network"}:
        potential_root = settings.storage_absolute_root or settings.storage_relative_root

    if potential_root:
        candidate = Path(potential_root)
        if candidate.is_absolute():
            settings.storage_root = str(candidate)
            if settings.storage_mode == "relative":
                settings.storage_mode = "absolute"
        else:
            settings.storage_root = potential_root
    else:
        settings.storage_root = defaults.storage_root

    # Backwards compatibility: if CLASSIFIER_STORAGE_ROOT era absoluto, garantir que usamos como tal.
    legacy_root = _first_env(("CLASSIFIER_STORAGE_ROOT",))
    if legacy_root:
        legacy_path = Path(legacy_root)
        if legacy_path.is_absolute():
            settings.storage_root = legacy_root
            settings.storage_mode = "absolute"

    if settings.use_azure and not settings.azure_api_key:
        settings.azure_api_key = settings.api_key

    if env_path:
        settings.storage_mount_command = (
            settings.storage_mount_command or os.getenv("DOC_ANALYZER_STORAGE_MOUNT_COMMAND", "")
        )

    return settings
