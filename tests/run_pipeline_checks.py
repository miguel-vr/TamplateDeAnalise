import json
import shutil
import subprocess
import sys
import time
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
PYTHON_EXE = sys.executable
TIMEOUT = 60


def run_step(cmd, cwd=BASE_DIR, check=True):
    result = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"Comando falhou: {' '.join(cmd)}\n{result.stdout}\n{result.stderr}")
    return result


def clean_workspace():
    entrada = BASE_DIR / "folders" / "entrada"
    processamento = BASE_DIR / "folders" / "em_processamento"
    for folder in (entrada, processamento):
        folder.mkdir(parents=True, exist_ok=True)
        for item in folder.iterdir():
            if item.is_file():
                item.unlink(missing_ok=True)
    falhas = processamento / "_falhas"
    if falhas.exists():
        shutil.rmtree(falhas)
        falhas.mkdir(parents=True, exist_ok=True)


def run_compile_check():
    run_step([PYTHON_EXE, "-m", "compileall", "core", "main.py", "tools"], check=True)


def prepare_samples():
    run_step([PYTHON_EXE, "tools/create_sample_documents.py", "--overwrite", "--drop-into-entrada"], check=True)


def run_pipeline(duration: int = 20):
    env = dict(**os.environ)
    env.setdefault("CLASSIFIER_TEST_DURATION", str(duration))
    result = subprocess.run(
        [PYTHON_EXE, "test_run.py"],
        cwd=BASE_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=max(TIMEOUT, duration + 10),
    )
    if result.returncode != 0:
        raise RuntimeError(f"test_run.py falhou\n{result.stdout}\n{result.stderr}")


def validate_outputs():
    processed_dir = BASE_DIR / "folders" / "processados"
    if not processed_dir.exists():
        raise RuntimeError("Pasta folders/processados nao encontrada apos execucao.")
    zip_files = list(processed_dir.rglob("*.zip"))
    if not zip_files:
        raise RuntimeError("Nenhum pacote ZIP foi gerado durante o teste automatizado.")


def main():
    clean_workspace()
    run_compile_check()
    prepare_samples()
    run_pipeline(duration=25)
    validate_outputs()
    print("Pipeline automatizado validado com sucesso.")


if __name__ == "__main__":
    import os

    main()
