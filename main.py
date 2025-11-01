import logging
import signal
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from core.gpt_core import GPTCore, GPTServiceUnavailable
from core.knowledge_base import KnowledgeBase
from core.processor import DocumentProcessor
from core.validator import Validator
from core.taxonomy import TaxonomyRuleEngine
from core.watcher import FeedbackWatcher, IntakeWatcher, JsonEventLogger
from core.notifier import TeamsNotifier
from core.settings import Settings, load_settings

BASE_DIR = Path(__file__).parent.resolve()


def _resolve_path(value: str) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    return BASE_DIR / candidate


def setup_logging(settings: Settings) -> None:
    log_level_name = (settings.log_level or "INFO").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)
    text_log_value = settings.text_log_file or "logs/system.log"
    text_log_path = _resolve_path(text_log_value)
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


def resolve_storage_paths(settings: Settings) -> Dict[str, Path]:
    storage_root_value = settings.storage_root or "folders"
    storage_root = _resolve_path(storage_root_value)

    def _resolve_relative(value: Optional[str], default: str) -> Path:
        raw = value if value not in {None, ""} else default
        candidate = Path(raw)
        if candidate.is_absolute():
            return candidate
        return storage_root / candidate

    input_dir = _resolve_relative(settings.input_subdir, "entrada")
    processing_dir = _resolve_relative(settings.processing_subdir, "em_processamento")

    processing_fail_raw = settings.processing_fail_subdir or "_falhas"
    processing_fail_dir = Path(processing_fail_raw)
    if not processing_fail_dir.is_absolute():
        processing_fail_dir = processing_dir / processing_fail_raw

    processed_dir = _resolve_relative(settings.processed_subdir, "processados")
    feedback_dir = _resolve_relative(settings.feedback_subdir, "feedback")
    feedback_processed_raw = settings.feedback_processed_subdir or "processado"
    feedback_processed_dir = Path(feedback_processed_raw)
    if not feedback_processed_dir.is_absolute():
        feedback_processed_dir = feedback_dir / feedback_processed_raw

    complex_samples_dir = _resolve_relative(settings.complex_samples_subdir, "complex_samples")

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


def ensure_structure(
    paths: Dict[str, Path],
    auto_create: bool,
    create_default_categories: bool,
) -> None:
    if not auto_create:
        logging.info(
            "Criacao automatica de pastas desabilitada. "
            "Certifique-se de que %s e subpastas estejam acessiveis.",
            paths["storage_root"],
        )
        return
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
    if create_default_categories:
        default_categories = ["tecnologia", "juridico", "financeiro", "compliance", "outros"]
        for category in default_categories:
            base_directories.append(paths["processed_dir"] / category)
    for directory in base_directories:
        if directory.exists():
            logging.debug("Pasta ja existia: %s", directory)
        else:
            directory.mkdir(parents=True, exist_ok=True)
            logging.info("Pasta criada: %s", directory)


def create_components(settings: Settings) -> Tuple[IntakeWatcher, FeedbackWatcher]:
    config_dict = settings.to_dict()
    storage_paths = resolve_storage_paths(settings)
    ensure_structure(
        storage_paths,
        settings.storage_auto_create,
        settings.storage_create_default_categories,
    )
    logging.info(
        "Estrutura de armazenamento configurada (modo=%s, raiz=%s).",
        settings.storage_mode,
        storage_paths["storage_root"],
    )
    knowledge_path = _resolve_path(settings.knowledge_base_path or "knowledge.json")
    category_root_cfg = settings.category_knowledge_root or "knowledge_sources"
    category_root_path = Path(category_root_cfg)
    if not category_root_path.is_absolute():
        category_root_path = (BASE_DIR / category_root_path).resolve()
    category_root_path.mkdir(parents=True, exist_ok=True)
    log_file = _resolve_path(settings.log_file or "logs/activity.jsonl")
    event_logger = JsonEventLogger(log_file)
    knowledge_base = KnowledgeBase(str(knowledge_path), str(category_root_path))

    gpt_core = GPTCore(config_dict, knowledge_base)
    gpt_core.ensure_available()
    validator = Validator(config_dict, gpt_core)
    taxonomy_engine = TaxonomyRuleEngine()
    teams_notifier = TeamsNotifier(
        settings.teams_webhook_url,
        settings.teams_activity_webhook_url,
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
        interval=int(settings.polling_interval),
        logger=event_logger,
        max_workers=int(settings.processing_workers),
    )
    feedback_watcher = FeedbackWatcher(
        feedback_dir=storage_paths["feedback_dir"],
        processed_feedback_dir=storage_paths["feedback_processed_dir"],
        knowledge_base=knowledge_base,
        interval=int(settings.feedback_polling_interval),
        logger=event_logger,
    )

    return intake_watcher, feedback_watcher


def main() -> None:
    settings = load_settings(BASE_DIR / ".env")
    setup_logging(settings)
    logging.info("Configuracao carregada: %s", settings.for_logging())

    try:
        intake_watcher, feedback_watcher = create_components(settings)
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

    logging.info("GPT Document Classifier pronto. Monitorando %s.", intake_watcher.entrada_dir)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        shutdown_handler()


def load_config(env_path: Optional[Path] = None) -> Dict[str, Any]:
    """
    Compat helper to expose Settings como dict.

    Preferir chamar `load_settings` quando precisar dos tipos prontos.
    """
    target_path = env_path if env_path else BASE_DIR / ".env"
    return load_settings(target_path).to_dict()


if __name__ == "__main__":
    main()
