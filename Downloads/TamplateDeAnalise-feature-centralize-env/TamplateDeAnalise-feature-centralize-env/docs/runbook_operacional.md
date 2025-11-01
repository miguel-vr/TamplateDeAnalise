# Runbook operacional do classificador

Este runbook resume os passos para operar e manter o pipeline enquanto eu estiver fora. Foquei em configuracao, pastas, credenciais e pontos de monitoramento.

## 1. Preparando o ambiente
- Python 3.10+ instalado (validado com 3.11).
- Criar e ativar virtualenv (`python -m venv .venv` e `.venv\Scripts\activate` no Windows ou `source .venv/bin/activate` no Linux/Mac).
- Instalar dependencias: `pip install -r requirements.txt`.

## 2. Configurando via `.env`
1. Gerar o arquivo com os defaults (PowerShell): `Get-Content .env.example | Set-Content -Path .env -Encoding UTF8`.
2. Editar o `.env` e preencher as variaveis obrigatorias (`DOC_ANALYZER_API_KEY`, webhooks, configuracao de storage, parametros Azure se aplicavel).
3. Consultar a secao 8 do README para a tabela completa das variaveis e os exemplos de `relative`, `absolute` e `network`.
4. Documentar o comando real de montagem do compartilhamento em `DOC_ANALYZER_STORAGE_MOUNT_COMMAND` quando o ambiente depender de share.

Checklist rapido do que revisar antes do deploy:
- Chaves (`DOC_ANALYZER_API_KEY` ou `DOC_ANALYZER_AZURE_API_KEY`).
- Modelos (`DOC_ANALYZER_MODEL`, `DOC_ANALYZER_CROSS_MODEL`).
- Cadencia (`DOC_ANALYZER_POLL_INTERVAL`, `DOC_ANALYZER_FEEDBACK_INTERVAL`) e paralelismo (`DOC_ANALYZER_PROCESSING_WORKERS`).
- Logs e base (`DOC_ANALYZER_LOG_FILE`, `DOC_ANALYZER_TEXT_LOG_FILE`, `DOC_ANALYZER_KNOWLEDGE_BASE_PATH`).
- Webhooks (`DOC_ANALYZER_TEAMS_WEBHOOK_URL`, `DOC_ANALYZER_TEAMS_ACTIVITY_WEBHOOK_URL`).

## 3. Ajustando os diretorios de trabalho
1. Escolher `DOC_ANALYZER_STORAGE_MODE`:
   - `relative`: organiza pastas dentro de `<pasta_do_servico>/<DOC_ANALYZER_STORAGE_RELATIVE_ROOT>`.
   - `absolute`: usa exatamente `DOC_ANALYZER_STORAGE_ABSOLUTE_ROOT` (ex.: `D:/classificador_prod`).
   - `network`: igual ao `absolute`, mas apontando para um compartilhamento montado (ex.: `/mnt/classificador`).
2. Para `absolute` ou `network`, definir `DOC_ANALYZER_STORAGE_ABSOLUTE_ROOT`, criar a pasta (ou montar o share) e garantir permissao de escrita para o usuario do servico.
3. Caso a montagem exija credenciais, preencher `DOC_ANALYZER_STORAGE_SERVICE_USER`, `DOC_ANALYZER_STORAGE_SERVICE_PASSWORD`, `DOC_ANALYZER_STORAGE_SERVICE_DOMAIN` e registrar o comando (ex.: `mount -t cifs //<servidor>/classificador /mnt/classificador -o user=$DOC_ANALYZER_STORAGE_SERVICE_USER`).
4. Ajustar subpastas (`DOC_ANALYZER_INPUT_SUBDIR`, `DOC_ANALYZER_PROCESSED_SUBDIR`, etc.) somente quando precisar de nomes diferentes; se informar caminho absoluto, o sistema usa como esta.
5. Manter `DOC_ANALYZER_STORAGE_AUTO_CREATE=true` em ambientes que permitem criacao automatica de pastas; troque para `false` se a infraestrutura exigir que tudo exista previamente.

## 4. Subindo o servico
- **Execucao direta**: `python main.py`.
- **Smoke test temporario**: `python test_run.py` (padrao 30s, ajuste com `CLASSIFIER_TEST_DURATION`).
- **Servico Linux**: systemd chamando o virtualenv + `python /caminho/main.py`. Inclua o comando de montagem antes de iniciar quando o modo for `network`.
- Logs:
  - Estruturado (JSONL): `DOC_ANALYZER_LOG_FILE`.
  - Texto: `DOC_ANALYZER_TEXT_LOG_FILE`.
  - Notificacoes: webhooks configurados no Teams.

## 5. Mapa das classes principais
| Classe | Arquivo | Resumo |
| --- | --- | --- |
| `core.settings.Settings` | `core/settings.py` | Carrega o `.env`, valida tipos e expoe configuracao tipada. |
| `core.processor.DocumentProcessor` | `core/processor.py` | Pipeline completo: extrai texto, chama GPT, aplica heuristicas, gera pacotes e atualiza conhecimento. |
| `core.processor._ProcessingTimeline` | `core/processor.py` | Cronometra etapas, gera eventos estruturados e logs. |
| `core.gpt_core.GPTCore` | `core/gpt_core.py` | Envolve chamadas GPT/Azure, retries e adaptacoes com base em conhecimento local. |
| `core.validator.Validator` | `core/validator.py` | Refaz analise quando a confianca fica baixa e normaliza saidas. |
| `core.taxonomy.TaxonomyRuleEngine` | `core/taxonomy.py` | Ajusta categoria com heuristica de palavras-chave. |
| `core.knowledge_base.KnowledgeBase` | `core/knowledge_base.py` | Persiste conhecimento estruturado, documentos e feedback. |
| `core.notifier.TeamsNotifier` | `core/notifier.py` | Dispara notificacoes (atividade + Adaptive Card) no Teams. |
| `core.watcher.*` | `core/watcher.py` | Implementa watchers de entrada e feedback, alem do logger JSONL. |

## 6. Checklist antes do deploy
1. `.env` preenchido (sem TODO em branco).
2. Share montado ou pasta absoluta criada.
3. Permissoes de escrita verificadas para o usuario do servico.
4. Webhooks validados (curl simples para assegurar resposta 200).
5. `python test_run.py` executado com arquivo de exemplo.
6. Monitoramento funcionando (verificar `logs/system.log` e `logs/activity.jsonl`).

## 7. Rotina de operacao
- Acompanhar `logs/system.log` (erros) e `logs/activity.jsonl` (eventos). Integrar o JSONL em dashboards quando possivel.
- Verificar `folders/em_processamento` diariamente; se houver itens travados, inspecionar o ultimo evento registrado.
- Limpar `folders/em_processamento/_falhas` e reprocessar manualmente quando necessario.
- Validar recebimento de Adaptive Cards e avisos no Teams.
- Revisar feedbacks aplicados, removendo trechos redundantes em `knowledge_sources/<categoria>/`.

## 8. Fallbacks rapidos
- **GPT indisponivel**: o arquivo retorna para a pasta de entrada. Acionar time responsavel ou usar modo manual.
- **Compartilhamento inacessivel**: remonte o share usando o comando documentado em `DOC_ANALYZER_STORAGE_MOUNT_COMMAND`.
- **Documento problematico**: mova para `complex_samples` com uma nota datada, siga com a fila e trate depois.
