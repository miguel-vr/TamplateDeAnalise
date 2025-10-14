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
    "max_retries": 2,
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
        "azure_endpoint": _env_value("AZURE_OPENAI_ENDPOINT"),
        "azure_api_key": _env_value("AZURE_OPENAI_API_KEY"),
        "azure_deployment": _env_value("AZURE_OPENAI_DEPLOYMENT"),
        "azure_api_version": _env_value("AZURE_OPENAI_API_VERSION"),
        "teams_webhook_url": _env_value("TEAMS_WEBHOOK_URL"),
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


def setup_logging(config: Dict) -> None:
    log_level = getattr(logging, config.get("log_level", "INFO").upper(), logging.INFO)
    text_log_path = BASE_DIR / config.get("text_log_file", "logs/system.log")
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


def ensure_structure() -> None:
    folders = [
        BASE_DIR / "core",
        BASE_DIR / "folders",
        BASE_DIR / "folders" / "entrada",
        BASE_DIR / "folders" / "em_processamento",
        BASE_DIR / "folders" / "em_processamento" / "_falhas",
        BASE_DIR / "folders" / "processados",
        BASE_DIR / "folders" / "processados" / "tecnologia",
        BASE_DIR / "folders" / "processados" / "juridico",
        BASE_DIR / "folders" / "processados" / "financeiro",
        BASE_DIR / "folders" / "processados" / "compliance",
        BASE_DIR / "folders" / "processados" / "outros",
        BASE_DIR / "folders" / "feedback",
        BASE_DIR / "folders" / "feedback" / "processado",
        BASE_DIR / "logs",
    ]
    for directory in folders:
        if directory.exists():
            logging.debug("Pasta ja existia: %s", directory)
        else:
            directory.mkdir(parents=True, exist_ok=True)
            logging.info("Pasta criada: %s", directory)


def create_components(config: Dict):
    ensure_structure()
    knowledge_path = BASE_DIR / config.get("knowledge_base_path", "knowledge.json")
    log_file = BASE_DIR / config.get("log_file", "logs/activity.jsonl")
    event_logger = JsonEventLogger(log_file)
    knowledge_base = KnowledgeBase(str(knowledge_path))

    gpt_core = GPTCore(config, knowledge_base)
    gpt_core.ensure_available()
    validator = Validator(config, gpt_core)
    taxonomy_engine = TaxonomyRuleEngine()
    teams_notifier = TeamsNotifier(config.get("teams_webhook_url", ""))
    processor = DocumentProcessor(
        gpt_core=gpt_core,
        validator=validator,
        knowledge_base=knowledge_base,
        base_folder=str(BASE_DIR),
        event_emitter=event_logger.emit,
        taxonomy_engine=taxonomy_engine,
        teams_notifier=teams_notifier,
    )

    intake_watcher = IntakeWatcher(
        entrada_dir=BASE_DIR / "folders" / "entrada",
        processamento_dir=BASE_DIR / "folders" / "em_processamento",
        processor=processor,
        interval=int(config.get("polling_interval", 10)),
        logger=event_logger,
        max_workers=int(config.get("processing_workers", 2)),
    )
    feedback_watcher = FeedbackWatcher(
        feedback_dir=BASE_DIR / "folders" / "feedback",
        processed_feedback_dir=BASE_DIR / "folders" / "feedback" / "processado",
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
