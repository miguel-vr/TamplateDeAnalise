import os
import time

from main import create_components, load_config, setup_logging


def main(duration: int = 30) -> None:
    """Execute the classifier pipeline for a limited duration (testing helper)."""
    config = load_config()
    setup_logging(config)
    intake_watcher, feedback_watcher = create_components(config)

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
