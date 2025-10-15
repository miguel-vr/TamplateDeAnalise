import json
import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Dict, Optional

from core.gpt_core import GPTCore, GPTServiceUnavailable
from core.knowledge_base import KnowledgeBase
from core.processor import DocumentProcessor
from core.validator import Validator
from core.taxonomy import TaxonomyRuleEngine
from core.watcher import FeedbackWatcher, IntakeWatcher, JsonEventLogger
from core.notifier import TeamsNotifier

try:
    from dotenv import load_dotenv  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    load_dotenv = None  # type: ignore

BASE_DIR = Path(__file__).parent.resolve()
CONFIG_PATH = BASE_DIR / "config.json"
DEFAULT_CONFIG = {
    "api_key": "",
    "model": "gpt-5",
    "confidence_threshold": 0.8,
    "polling_interval": 10,
    "feedback_polling_interval": 10,
    "processing_workers": 2,
    "log_level": "DEBUG",
    "log_file": "logs/activity.jsonl",
    "text_log_file": "logs/system.log",
    "knowledge_base_path": "knowledge.json",
    "category_knowledge_root": "knowledge_sources",
    "max_retries": 3,
    "teams_activity_webhook_url": "",
    "storage_root": "folders",
    "input_subdir": "entrada",
    "processing_subdir": "em_processamento",
    "processing_fail_subdir": "_falhas",
    "processed_subdir": "processados",
    "feedback_subdir": "feedback",
    "feedback_processed_subdir": "processado",
    "complex_samples_subdir": "complex_samples",
    "cross_validation_model": "gpt-5",
    "temperature": 1.0,
    "request_timeout": 60,
    "azure_keyvault_url": "",
    "use_azure": False,
    "azure_endpoint": "",
    "azure_api_key": "",
    "azure_deployment": "",
    "azure_api_version": "2024-02-01",
    "teams_webhook_url": "",
}


def _env_value(key: str) -> Optional[str]:
    value = os.getenv(key)
    if value is None or value == "":
        return None
    return value


def load_config() -> Dict:
    if load_dotenv:
        load_dotenv()
    if not CONFIG_PATH.exists():
        with open(CONFIG_PATH, "w", encoding="utf-8") as handler:
            json.dump(DEFAULT_CONFIG, handler, indent=2, ensure_ascii=False)
        return dict(DEFAULT_CONFIG)
    with open(CONFIG_PATH, "r", encoding="utf-8") as handler:
        data = json.load(handler)
    # merge defaults to guarantee required keys
    merged = dict(DEFAULT_CONFIG)
    merged.update(data)

    env_overrides = {
        "api_key": _env_value("OPENAI_API_KEY"),
        "model": _env_value("LLM_MODEL"),
        "cross_validation_model": _env_value("LLM_CROSS_MODEL"),
        "confidence_threshold": _env_value("CLASSIFIER_CONFIDENCE_THRESHOLD"),
        "polling_interval": _env_value("CLASSIFIER_POLL_INTERVAL"),
        "feedback_polling_interval": _env_value("CLASSIFIER_FEEDBACK_INTERVAL"),
        "processing_workers": _env_value("CLASSIFIER_PROCESSING_WORKERS"),
        "log_level": _env_value("CLASSIFIER_LOG_LEVEL"),
        "temperature": _env_value("CLASSIFIER_TEMPERATURE"),
        "azure_keyvault_url": _env_value("AZURE_KEYVAULT_URL"),
        "use_azure": _env_value("USE_AZURE_OPENAI"),
        "azure_endpoint": _env_value("AZURE_OPENAI_ENDPOINT") or _env_value("URL_BASE"),
        "azure_api_key": _env_value("AZURE_OPENAI_KEY") or _env_value("API_KEY"),
        "azure_deployment": _env_value("AZURE_OPENAI_DEPLOYMENT") or _env_value("DEPLOYMENT_NAME"),
        "azure_api_version": _env_value("AZURE_OPENAI_API_VERSION") or _env_value("OPENAI_API_VERSION"),
        "teams_webhook_url": _env_value("TEAMS_WEBHOOK_URL"),
        "teams_activity_webhook_url": _env_value("TEAMS_ACTIVITY_WEBHOOK_URL"),
        "storage_root": _env_value("CLASSIFIER_STORAGE_ROOT"),
        "input_subdir": _env_value("CLASSIFIER_INPUT_SUBDIR"),
        "processing_subdir": _env_value("CLASSIFIER_PROCESSING_SUBDIR"),
        "processing_fail_subdir": _env_value("CLASSIFIER_PROCESSING_FAIL_SUBDIR"),
        "processed_subdir": _env_value("CLASSIFIER_PROCESSED_SUBDIR"),
        "feedback_subdir": _env_value("CLASSIFIER_FEEDBACK_SUBDIR"),
        "feedback_processed_subdir": _env_value("CLASSIFIER_FEEDBACK_PROCESSED_SUBDIR"),
        "complex_samples_subdir": _env_value("CLASSIFIER_COMPLEX_SAMPLES_SUBDIR"),
    }

    for key, value in env_overrides.items():
        if not value:
            continue
        if key in {"polling_interval", "feedback_polling_interval", "processing_workers"}:
            try:
                merged[key] = int(value)
            except ValueError:
                logging.warning("Could not convert env override %s=%s to int. Keeping config value.", key, value)
        elif key in {"confidence_threshold", "temperature"}:
            try:
                merged[key] = float(value)
            except ValueError:
                logging.warning("Could not convert env override %s=%s to float. Keeping config value.", key, value)
        elif key == "use_azure":
            merged[key] = str(value).strip().lower() in {"1", "true", "yes", "on"}
        else:
            merged[key] = value

    timeout_value = _env_value("LLM_TIMEOUT_S")
    if timeout_value:
        try:
            merged["request_timeout"] = float(timeout_value)
        except ValueError:
            logging.warning("Could not convert LLM_TIMEOUT_S=%s to float.", timeout_value)

    return merged


def _resolve_path(value: str) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    return BASE_DIR / candidate


def setup_logging(config: Dict) -> None:
    log_level = getattr(logging, config.get("log_level", "INFO").upper(), logging.INFO)
    text_log_path = _resolve_path(config.get("text_log_file", "logs/system.log"))
    text_log_path.parent.mkdir(parents=True, exist_ok=True)

    handlers = [logging.StreamHandler(sys.stdout)]
    file_handler = logging.FileHandler(text_log_path, encoding="utf-8")
    handlers.append(file_handler)

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers,
        force=True,
    )
    logging.info("Logging configurado. Saida principal em %s", text_log_path)


def resolve_storage_paths(config: Dict) -> Dict[str, Path]:
    storage_root = _resolve_path(config.get("storage_root", "folders"))

    def _resolve_relative(value: Optional[str], default: str) -> Path:
        raw = value if value not in {None, ""} else default
        candidate = Path(raw)
        if candidate.is_absolute():
            return candidate
        return storage_root / candidate

    input_dir = _resolve_relative(config.get("input_subdir"), "entrada")
    processing_dir = _resolve_relative(config.get("processing_subdir"), "em_processamento")

    processing_fail_raw = config.get("processing_fail_subdir", "_falhas")
    processing_fail_dir = Path(processing_fail_raw)
    if not processing_fail_dir.is_absolute():
        processing_fail_dir = processing_dir / processing_fail_raw

    processed_dir = _resolve_relative(config.get("processed_subdir"), "processados")
    feedback_dir = _resolve_relative(config.get("feedback_subdir"), "feedback")
    feedback_processed_raw = config.get("feedback_processed_subdir", "processado")
    feedback_processed_dir = Path(feedback_processed_raw)
    if not feedback_processed_dir.is_absolute():
        feedback_processed_dir = feedback_dir / feedback_processed_raw

    complex_samples_dir = _resolve_relative(config.get("complex_samples_subdir"), "complex_samples")

    return {
        "storage_root": storage_root,
        "input_dir": input_dir,
        "processing_dir": processing_dir,
        "processing_fail_dir": processing_fail_dir,
        "processed_dir": processed_dir,
        "feedback_dir": feedback_dir,
        "feedback_processed_dir": feedback_processed_dir,
        "complex_samples_dir": complex_samples_dir,
    }


def ensure_structure(paths: Dict[str, Path]) -> None:
    base_directories = [
        paths["storage_root"],
        paths["input_dir"],
        paths["processing_dir"],
        paths["processing_fail_dir"],
        paths["processed_dir"],
        paths["feedback_dir"],
        paths["feedback_processed_dir"],
        paths["complex_samples_dir"],
        BASE_DIR / "logs",
    ]
    default_categories = ["tecnologia", "juridico", "financeiro", "compliance", "outros"]
    for category in default_categories:
        base_directories.append(paths["processed_dir"] / category)
    for directory in base_directories:
        if directory.exists():
            logging.debug("Pasta ja existia: %s", directory)
        else:
            directory.mkdir(parents=True, exist_ok=True)
            logging.info("Pasta criada: %s", directory)


def create_components(config: Dict):
    storage_paths = resolve_storage_paths(config)
    ensure_structure(storage_paths)
    knowledge_path = _resolve_path(config.get("knowledge_base_path", "knowledge.json"))
    category_root_cfg = config.get("category_knowledge_root", "knowledge_sources")
    category_root_path = Path(category_root_cfg)
    if not category_root_path.is_absolute():
        category_root_path = (BASE_DIR / category_root_path).resolve()
    category_root_path.mkdir(parents=True, exist_ok=True)
    log_file = _resolve_path(config.get("log_file", "logs/activity.jsonl"))
    event_logger = JsonEventLogger(log_file)
    knowledge_base = KnowledgeBase(str(knowledge_path), str(category_root_path))

    gpt_core = GPTCore(config, knowledge_base)
    gpt_core.ensure_available()
    validator = Validator(config, gpt_core)
    taxonomy_engine = TaxonomyRuleEngine()
    teams_notifier = TeamsNotifier(
        config.get("teams_webhook_url", ""),
        config.get("teams_activity_webhook_url", ""),
    )
    processor = DocumentProcessor(
        gpt_core=gpt_core,
        validator=validator,
        knowledge_base=knowledge_base,
        base_folder=str(BASE_DIR),
        event_emitter=event_logger.emit,
        taxonomy_engine=taxonomy_engine,
        teams_notifier=teams_notifier,
        storage_paths=storage_paths,
    )

    intake_watcher = IntakeWatcher(
        entrada_dir=storage_paths["input_dir"],
        processamento_dir=storage_paths["processing_dir"],
        processor=processor,
        interval=int(config.get("polling_interval", 10)),
        logger=event_logger,
        max_workers=int(config.get("processing_workers", 2)),
    )
    feedback_watcher = FeedbackWatcher(
        feedback_dir=storage_paths["feedback_dir"],
        processed_feedback_dir=storage_paths["feedback_processed_dir"],
        knowledge_base=knowledge_base,
        interval=int(config.get("feedback_polling_interval", 15)),
        logger=event_logger,
    )

    return intake_watcher, feedback_watcher


def main() -> None:
    config = load_config()
    setup_logging(config)
    logging.info("Configuracao carregada: %s", {k: v for k, v in config.items() if k != "api_key"})

    try:
        intake_watcher, feedback_watcher = create_components(config)
    except GPTServiceUnavailable as exc:
        logging.error("Falha ao iniciar devido ao GPT: %s", exc)
        sys.exit(1)
    intake_watcher.start()
    feedback_watcher.start()

    def shutdown_handler(*_args):
        logging.info("Encerrando watchers...")
        intake_watcher.stop()
        feedback_watcher.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    logging.info("GPT Document Classifier pronto. Monitore a pasta 'folders/entrada/'.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        shutdown_handler()


if __name__ == "__main__":
    main()
