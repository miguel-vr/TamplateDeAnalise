# Runbook operacional do classificador

Este runbook serve como cola r+ipida para quem precisa operar ou ajustar o pipeline enquanto eu estiver fora. Foquei no que normalmente d+i mais trabalho: configura+o+uo, diret+rios, credenciais e o papel de cada classe estrat+gica.

## 1. Preparando o ambiente
- Python 3.10+ instalado (o projeto foi validado com 3.11).
- Crie e ative um virtualenv (`python -m venv .venv && source .venv/bin/activate` no Linux ou `.venv\Scripts\activate` no Windows).
- Instale as depend+ncias listadas no `requirements.txt` (rodar `pip install -r requirements.txt`). Se algum pacote opcional estiver faltando (ex.: `python-docx`, `PyMuPDF`, `openai`), o c+digo avisa no log.

## 2. Configurando via `.env`
1. Gere o arquivo com os defaults: Windows (PowerShell) `Copy-Item .env.example .env`; Linux/macOS `cp .env.example .env`.
2. Abra o `.env` e preencha os campos obrigatorios (`DOC_ANALYZER_API_KEY`, webhooks, parametros de storage e Azure se aplicavel).
3. O README (secao 8) traz a tabela completa das variaveis; mantenha o arquivo fora do Git/publico.
4. Guarde o comando real de montagem de compartilhamento em `DOC_ANALYZER_STORAGE_MOUNT_COMMAND` para consulta rapida da operacao.

Principais variaveis a revisar rapidamente antes do deploy:
- `DOC_ANALYZER_API_KEY` / `DOC_ANALYZER_AZURE_API_KEY` (chaves do modelo).
- `DOC_ANALYZER_MODEL` / `DOC_ANALYZER_CROSS_MODEL` (modelos usados).
- `DOC_ANALYZER_POLL_INTERVAL`, `DOC_ANALYZER_FEEDBACK_INTERVAL` e `DOC_ANALYZER_PROCESSING_WORKERS` (cadencia e paralelismo).
- `DOC_ANALYZER_LOG_FILE`, `DOC_ANALYZER_TEXT_LOG_FILE`, `DOC_ANALYZER_KNOWLEDGE_BASE_PATH`.
- `DOC_ANALYZER_TEAMS_WEBHOOK_URL` e `DOC_ANALYZER_TEAMS_ACTIVITY_WEBHOOK_URL` (notificacoes).
## 3. Ajustando os diretorios de trabalho
1. Defina `DOC_ANALYZER_STORAGE_MODE` conforme o ambiente:
   - `relative`: usa `<pasta_onde_o_servico_roda>/<DOC_ANALYZER_STORAGE_RELATIVE_ROOT>` (bom para dev).
   - `absolute`: usa exatamente `DOC_ANALYZER_STORAGE_ABSOLUTE_ROOT` (ex.: `D:/classificador_prod`).
   - `network`: igual ao `absolute`, mas apontando para um compartilhamento montado (`\\fileserver\classificador`, `/mnt/classificador`, etc.).
2. Para `absolute` ou `network`, preencha `DOC_ANALYZER_STORAGE_ABSOLUTE_ROOT`, crie o diretorio (ou monte o share) e garanta permissao de escrita para o usuario do servico.
3. Se a montagem exigir credenciais, documente-as em `DOC_ANALYZER_STORAGE_SERVICE_*` e mantenha o comando em `DOC_ANALYZER_STORAGE_MOUNT_COMMAND` (ex.: `mount -t cifs //<servidor>/classificador /mnt/classificador -o user=$DOC_ANALYZER_STORAGE_SERVICE_USER`).
4. Ajuste as subpastas (`DOC_ANALYZER_INPUT_SUBDIR`, `DOC_ANALYZER_PROCESSED_SUBDIR`, etc.) apenas quando precisar de nomes diferentes; se informar caminhos absolutos, eles serao usados como estao.
5. Deixe `DOC_ANALYZER_STORAGE_AUTO_CREATE=true` para que o servico crie a estrutura automaticamente ou troque para `false` quando a empresa exigir que tudo ja exista previamente.
## 4. Subindo o servi+oo
- **Execu+o+uo direta**: `python main.py`. O script j+i carrega o `.env`, configura logging e inicia os watchers.
- **Modo teste tempor+irio**: `python test_run.py` (roda por 30 segundos; ajuste `CLASSIFIER_TEST_DURATION` se quiser mais tempo).
- **Servi+oo Linux**: crie uma unidade systemd chamando o virtualenv + `python /caminho/main.py`. Adicione os passos de montagem do share antes de iniciar o servi+oo quando o modo for `network`.
- Logs:
  - Estruturados em JSONL: `DOC_ANALYZER_LOG_FILE`.
  - Texto tradicional: `DOC_ANALYZER_TEXT_LOG_FILE`.
  - Eventos pontuais (Teams) dependem dos webhooks configurados.

## 5. Mapa das classes principais
| Classe | Onde vive | Por que importa |
| --- | --- | --- |
| `core.settings.Settings` | `core/settings.py` | Carrega o `.env`, valida tipos e exp+Ae tudo tipado para o resto do sistema. |
| `core.processor.DocumentProcessor` | `core/processor.py` | Pipeline completo do documento: extrai texto, chama GPT, aplica heur+sticas, gera ZIP e atualiza a base. |
| `core.processor._ProcessingTimeline` | `core/processor.py` | Guarda o tempo de cada etapa e emite eventos estruturados para facilitar troubleshooting. |
| `core.gpt_core.GPTCore` | `core/gpt_core.py` | Wrapper das chamadas GPT/Azure, incluindo retries, prompts auxiliares e cross-check. |
| `core.validator.Validator` | `core/validator.py` | Refaz a an+ilise quando a confian+oa n+uo bate o limite e normaliza os campos de sa+da. |
| `core.taxonomy.TaxonomyRuleEngine` | `core/taxonomy.py` | Ajusta a categoria com base em palavras-chave quando o GPT hesita ou erra por pouco. |
| `core.knowledge_base.KnowledgeBase` | `core/knowledge_base.py` | Persist+ncia das an+ilises e hist+rico de feedback (evita retrabalho e alimenta heur+sticas). |
| `core.notifier.TeamsNotifier` | `core/notifier.py` | Dispara os Adaptive Cards no Teams (status e relat+rios). |
| `core.watcher.JsonEventLogger` | `core/watcher.py` | Escreve eventos estruturados em JSONL para auditoria. |
| `core.watcher.DirectoryWatcher` | `core/watcher.py` | Loop de polling que dispara callbacks quando aparece arquivo novo. |
| `core.watcher.IntakeWatcher` | `core/watcher.py` | Move arquivos da entrada para processamento e gerencia o executor. |
| `core.watcher.FeedbackWatcher` | `core/watcher.py` | Processa feedback humano e joga os ajustes na base de conhecimento. |

Se algu+m novo chegar no projeto, essa tabela + o melhor caminho para entender quem chama quem.

## 6. Checklist r+ipido antes de ir pra produ+o+uo
1. `.env` preenchido e sem valores padr+uo sens+veis.
2. Share de rede montado (se `storage_mode` = `network` ou `absolute`).
3. Pastas com permiss+uo de escrita para o usu+irio do servi+oo.
4. Webhooks do Teams validados com um curl simples (opcional, mas evita surpresa).
5. Rodar `python test_run.py` com um arquivo de exemplo para garantir que o fluxo est+i montado.
6. Acompanhar os logs no primeiro ciclo completo para confirmar que os paths foram resolvidos conforme esperado.

Com isso o time consegue operar o classificador sem depender de mim. Qualquer ajuste espec+fico de neg+cio pode ser feito mexendo nos mesmos pontos indicados aqui.
