import os
import time

from core.settings import load_settings
from main import create_components, setup_logging


def main(duration: int = 30) -> None:
    """Execute the classifier pipeline for a limited duration (testing helper)."""
    settings = load_settings()
    setup_logging(settings)
    intake_watcher, feedback_watcher = create_components(settings)

    intake_watcher.start()
    feedback_watcher.start()
    try:
        time.sleep(duration)
    finally:
        intake_watcher.stop()
        feedback_watcher.stop()


if __name__ == "__main__":
    runtime = int(os.environ.get("CLASSIFIER_TEST_DURATION", "30"))
    main(runtime)
