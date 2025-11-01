# Linux Service Helper

Este diretório contém utilidades para instalar o classificador como serviço em máquinas Linux (systemd).

## 1. Pré-requisitos
- Python 3 instalado no servidor.
- Ambiente virtual configurado com as dependências do projeto (`python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`).
- Arquivo `.env` preenchido com as chaves e caminhos corretos.
- Permissões de superusuário (`sudo`) para escrever em `/etc/systemd/system/`.

## 2. Script `install_linux_service.sh`
O script provisiona um serviço systemd que executa `main.py` de forma contínua.

### 2.1 Uso básico
```bash
chmod +x ops/install_linux_service.sh
sudo ops/install_linux_service.sh
```

Após rodar:
- O unit file `/etc/systemd/system/gpt-document-classifier.service` é criado/atualizado.
- O daemon do systemd é recarregado.
- O serviço é habilitado para subir no boot e iniciado imediatamente.

### 2.2 Variáveis configuráveis
Você pode exportar variáveis antes de executar o script para alterar padrões:

| Variável        | Valor padrão                                  | Descrição                                            |
|-----------------|-----------------------------------------------|------------------------------------------------------|
| `SERVICE_NAME`  | `gpt-document-classifier`                     | Nome do serviço systemd.                             |
| `APP_DIR`       | diretório raiz do projeto                      | Caminho onde estão `main.py` e `.env`.               |
| `PYTHON_BIN`    | `$APP_DIR/.venv/bin/python`                    | Interpretador Python usado para rodar `main.py`.     |
| `ENV_FILE`      | `$APP_DIR/.env`                                | Arquivo de variáveis de ambiente carregado pelo serviço. |
| `RUN_AS_USER`   | usuário logado (resultado de `logname`/`whoami`)| Usuário que executará o serviço.                     |
| `RUN_AS_GROUP`  | mesmo valor de `RUN_AS_USER`                   | Grupo associado ao processo.                         |
| `WORKING_DIR`   | `$APP_DIR`                                     | Diretório de trabalho do processo.                   |
| `LOG_DIR`       | `$APP_DIR/logs`                                | Diretório criado para armazenar artefatos de log.    |

Exemplo mudando usuário e chave do serviço:
```bash
export SERVICE_NAME=doc-analyzer
export RUN_AS_USER=svcclassificador
export RUN_AS_GROUP=svcclassificador
sudo ops/install_linux_service.sh
```

### 2.3 Arquivo `.env`
O serviço carrega as variáveis do arquivo apontado por `ENV_FILE` (padrão `.env` na raiz do projeto). Certifique-se de:
- Preencher as chaves do modelo (`DOC_ANALYZER_API_KEY`, `OPENAI_API_KEY`, `DOC_ANALYZER_USE_AZURE`, etc.).
- Informar diretórios corretos (`DOC_ANALYZER_STORAGE_MODE`, `DOC_ANALYZER_STORAGE_RELATIVE_ROOT` ou `DOC_ANALYZER_STORAGE_ABSOLUTE_ROOT`).
- Ajustar webhooks (`DOC_ANALYZER_TEAMS_WEBHOOK_URL`, `DOC_ANALYZER_TEAMS_ACTIVITY_WEBHOOK_URL`) se utilizar notificações.

### 2.4 Logs e monitoramento
- O fluxo do serviço pode ser acompanhado via `journalctl -u <service-name> -f`.
- Os logs estruturados continuam sendo gravados em `logs/activity.jsonl` e `logs/system.log` dentro do diretório configurado.

### 2.5 Reiniciar, parar e remover
```bash
sudo systemctl restart <service-name>
sudo systemctl stop <service-name>
sudo systemctl disable --now <service-name>
sudo rm /etc/systemd/system/<service-name>.service
sudo systemctl daemon-reload
```

## 3. Fluxo recomendado de implantação
1. Clone ou copie o projeto para a máquina alvo.
2. Configure o virtualenv e instale as dependências.
3. Copie `.env.example` para `.env` e ajuste as variáveis.
4. Teste localmente com `python main.py` ou `python test_run.py`.
5. Execute `sudo ops/install_linux_service.sh`.
6. Acompanhe os logs (`journalctl -u <service-name> -f`) no primeiro processamento para garantir que watchers, GPT e notificações estão funcionando.

## 4. Troubleshooting
- **Serviço falha ao iniciar:** verifique erros com `systemctl status <service-name>` e o journal. Confirme se o caminho do Python e do `.env` estão corretos.
- **Permissões de pasta:** garanta que o usuário configurado no serviço tenha acesso de leitura/escrita às pastas `folders/`, `logs/` e aos compartilhamentos configurados.
- **Timeout no GPT:** confirme acesso à internet e chaves válidas. O script não altera configuração de rede; problemas de conectividade aparecerão no log como `Falha ao contatar o modelo GPT`.

