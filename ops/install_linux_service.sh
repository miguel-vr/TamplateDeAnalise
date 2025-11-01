#!/usr/bin/env bash
# Provisiona o classificador como servico systemd em maquinas Linux.
# Execute este script com sudo para permitir a criacao/atualizacao do servico.

set -euo pipefail

# Nome padrao do servico. Sobrescreva exportando SERVICE_NAME antes de rodar.
SERVICE_NAME="${SERVICE_NAME:-gpt-document-classifier}"

# Diretorio raiz do projeto. Usa a pasta um nivel acima deste script por padrao.
APP_DIR="${APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

# Caminho do interpretador Python que executa o main.py (virtualenv do projeto).
PYTHON_BIN="${PYTHON_BIN:-$APP_DIR/.venv/bin/python}"

# Arquivo .env com as variaveis de ambiente consumidas pelo aplicativo.
ENV_FILE="${ENV_FILE:-$APP_DIR/.env}"

# Usuario/grupo que executara o servico. Usa o dono do terminal por padrao.
RUN_AS_USER="${RUN_AS_USER:-$(logname 2>/dev/null || whoami)}"
RUN_AS_GROUP="${RUN_AS_GROUP:-$RUN_AS_USER}"

# Pasta de trabalho usada pelo processo (mantem paths relativos do projeto).
WORKING_DIR="${WORKING_DIR:-$APP_DIR}"

# Pasta de logs (apenas para garantir que exista). Systemd envia os logs ao journal.
LOG_DIR="${LOG_DIR:-$APP_DIR/logs}"

SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

# Resolve caminhos absolutos sem depender de utilitarios especificos.
resolve_path() {
    local target="$1"
    if command -v realpath >/dev/null 2>&1; then
        realpath "$target"
    else
        python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$target"
    fi
}

require_file() {
    local path="$1" label="$2"
    if [[ ! -e "$path" ]]; then
        echo "Erro: ${label} nao encontrado em $path" >&2
        exit 1
    fi
}

require_executable() {
    local path="$1" label="$2"
    if [[ ! -x "$path" ]]; then
        echo "Erro: ${label} nao executavel em $path" >&2
        exit 1
    fi
}

if [[ $EUID -ne 0 ]]; then
    echo "Este script precisa ser executado como root (ex.: sudo $0)" >&2
    exit 1
fi

PYTHON_BIN="$(resolve_path "$PYTHON_BIN")"
ENV_FILE="$(resolve_path "$ENV_FILE")"
WORKING_DIR="$(resolve_path "$WORKING_DIR")"
APP_DIR="$(resolve_path "$APP_DIR")"
LOG_DIR="$(resolve_path "$LOG_DIR")"

require_executable "$PYTHON_BIN" "interpretador Python"
require_file "$ENV_FILE" "arquivo .env"
require_file "$APP_DIR/main.py" "main.py"

if ! getent group "$RUN_AS_GROUP" >/dev/null 2>&1; then
    RUN_AS_GROUP="$RUN_AS_USER"
fi

mkdir -p "$LOG_DIR"
chown "$RUN_AS_USER":"$RUN_AS_GROUP" "$LOG_DIR"

cat >"$SERVICE_FILE" <<EOF
[Unit]
Description=GPT Document Classifier
After=network.target

[Service]
Type=simple
User=${RUN_AS_USER}
Group=${RUN_AS_GROUP}
WorkingDirectory=${WORKING_DIR}
EnvironmentFile=${ENV_FILE}
Environment=PYTHONUNBUFFERED=1
ExecStart=${PYTHON_BIN} ${APP_DIR}/main.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

chmod 644 "$SERVICE_FILE"

systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}"

echo "Servico '${SERVICE_NAME}' provisionado com sucesso."
echo "Verifique logs com: journalctl -u ${SERVICE_NAME} -f"
