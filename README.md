# Classifica Document Pipeline

## Documentacao complementar
- Guia do usuario: [docs/user_guide.md](docs/user_guide.md)
- Arquitetura e fluxo: [docs/architecture.md](docs/architecture.md)
- Runbook rapido: [docs/runbook_operacional.md](docs/runbook_operacional.md)
- Manual operacional legado: [docs/manual_operacional.md](docs/manual_operacional.md)

## 1. Visao geral
- O sistema monitora continuamente `folders/entrada` e inicia um pipeline de classificacao para cada documento novo.
- O resultado final e um pacote ZIP por documento contendo: arquivo original, `analise.txt` (relatorio tecnico) e `feedback.txt` (formulario editavel), armazenado em `folders/processados/<categoria>/`.
- A decisao combina quatro camadas: modelo GPT (`core/gpt_core.py`), reforco de confianca (`core/validator.py`), heuristicas locais (`core/taxonomy.py`) e base de conhecimento (`core/knowledge_base.py`).
- Todos os eventos estruturados sao registrados em `logs/activity.jsonl` e o log textual completo fica em `logs/system.log`.
- A cada processamento concluido, um Adaptive Card eh enviado para Microsoft Teams (via webhook) com resumo da analise.

## 2. Arquitetura de componentes
- **Watchers**
  - `core/watcher.DirectoryWatcher`: thread de polling generica que dispara callbacks por arquivo.
  - `core/watcher.IntakeWatcher`: move arquivos da pasta de entrada para `folders/em_processamento`, registra estado da fila e aciona o `DocumentProcessor` em paralelo (executor configuravel).
  - `core/watcher.FeedbackWatcher`: interpreta feedbacks (`.json` ou `.txt`), normaliza campos (`documento`, `status`, `nova_categoria`, `observacoes`), processa confirma+�+�es/evid+�ncias por categoria e arquiva tanto o formul+�rio quanto os trechos aprovados em `knowledge_sources/<categoria>/feedback_*.txt`.
- **Pipeline de processamento**
  - `core/processor.DocumentProcessor`: encapsula todo o fluxo. Cada etapa gera eventos via `_ProcessingTimeline`, inclui metricas de duracao, aciona heuristicas, atualiza a base e dispara notificacoes.
  - `core/taxonomy.TaxonomyRuleEngine`: calcula scores de palavras-chave por categoria, ajusta classificacoes (promocao/reducao) e gera composicao de confianca (LLM + heuristica + conhecimento).
  - `core/notifier.TeamsNotifier`: publica o Adaptive Card final e os avisos transacionais (recebido/processado) nos webhooks configurados no `.env`.
- **Camadas de analise**
  - `core/gpt_core.GPTCore`: executa prompts principais, validacao cruzada e camada I3, alem de ajustar categorias com base em conhecimento local.
  - `core/validator.Validator`: reexecuta a analise quando a confianca esta abaixo do limite, normaliza porcentagens e notifica tentativas adicionais.
  - `core/knowledge_base.KnowledgeBase`: persiste entradas, controla categorias (evita duplicatas semelhantes), registra historico de feedback e reforca palavras-chave com alta confianca.

## 3. Fluxo detalhado (etapas do pipeline)
1. **Entrada**: arquivo chega em `folders/entrada`. `IntakeWatcher` move para `folders/em_processamento`, cria ID de processamento e registra o estado da fila.
2. **Extracao de texto** (`extracao_texto`): leitura de PDF (PyMuPDF), DOCX (python-docx) ou TXT (UTF-8 com fallback latin-1). Logs indicam quantidade de caracteres extraidos.
3. **Analise GPT** (`analise_gpt`): `GPTCore` gera classificacao, resumo tematico, motivos-chave e riscos. Falhas no servico retornam o arquivo para a pasta de entrada.
4. **Validacao** (`validacao`): `Validator` garante que a confianca atinga o limiar (`confidence_threshold`). Caso necessario, reforca a analise com tentativas adicionais.
5. **Refinamento heuristico** (`refinamento_taxonomia`): `TaxonomyRuleEngine` pontua categorias pelo texto bruto, promove categorias existentes (ou sugere nova como `recursos humanos / saude ocupacional`), recalcula confianca composta e publica o relatorio `taxonomy_report`.
6. **Resolucao de categoria** (`resolucao_categoria`): cria a pasta de destino caso ainda nao exista e registra evento `category_folder_created` quando aplicavel.
7. **Geracao de pacote** (`geracao_pacote`): cria um ZIP contendo documento original, relatorio `analise.txt` (com todos os metadados, matches, heuristica) e um formulario de feedback `feedback.txt` atualizado.
8. **Atualizacao da base** (`atualizacao_conhecimento`): grava a entrada na `KnowledgeBase`, reforca palavras-chave de alta confianca e registra `knowledge_entry_added`.
9. **Finalizacao**: `_ProcessingTimeline` encerra com status, gera um resumo de duracoes por etapa, dispara notification event `processing_timeline_summary`, envia o Adaptive Card para Teams e remove o arquivo de trabalho.
10. **Falhas inesperadas**: `_handle_unexpected_failure` armazena o arquivo em `folders/em_processamento/_falhas`, gera evento `processing_internal_error` e mantem a fila ativa.

## 4. Camadas de decisao
- **Modelo GPT**: classifica documentos conforme categorias conhecidas, produz temas, justificativas, riscos e sugestoes de nova categoria.
- **Validator**: garante padronizacao das metricas (`confidence`, `confidence_percent`, `confianca`), reexecuta o GPT ate `max_retries` se necessario.
- **TaxonomyRuleEngine**: pontua termos-chave por categoria. Casos emblematicos:
  - `recursos humanos / saude ocupacional`: dispara com termos como "medicina do trabalho", "laudo medico", "ergonomia".
  - Ajustes de confianca: mistura 50% (LLM) + 35% (heuristica) + 15% (knowledge base).
- **Knowledge Base**: evita categorias duplicadas por nome similar, registra historico de feedback e reforca palavras-chave quando a confianca e >= 0.9.

### Base documental por categoria
- O diretorio configurado em `category_knowledge_root` (padrao `knowledge_sources/`) armazena pastas por categoria com documentos reais ja validados pelo time.
- `KnowledgeBase.refresh_category_documents()` monitora essas pastas, calcula hash para evitar reprocessar arquivos e extrai termos caracteristicos por categoria; cada atualizacao e registrada em log.
- Durante a analise, `GPTCore` injeta esse vocabul+�rio nos prompts (primario, auditoria e I3) e adiciona uma camada de validacao `document_knowledge` que ajusta categorias, areas secundarias e confianca final.
- Novas categorias sao provisionadas automaticamente (pasta + `category.json`) quando o pipeline consolida uma classificacao principal ou secundaria inedita; tambem e possivel criar a pasta manualmente e ela sera absorvida no proximo refresh.
- Para validacao manual, um documento por categoria foi colocado em `knowledge_sources/` (contabilidade, tecnologia, tesouraria) e outros dois exemplos por categoria estao em `samples/` para testes do usuario.

Passo a passo para introduzir uma nova categoria principal:
1. Crie uma pasta dentro de `knowledge_sources/` usando um nome descritivo (ex.: `knowledge_sources/tesouraria`). O sistema normaliza o nome para o modelo e registra metadados em `category.json` na primeira execucao.
2. Adicione ao menos um documento ja validado para essa categoria (TXT, PDF ou DOCX). Os arquivos podem ser adicionados gradualmente; apenas os novos sao processados a cada varredura.
3. Execute o pipeline (`python main.py`). A camada documental sera atualizada nos logs (`KnowledgeBase.refresh_category_documents`), exibindo hashes, termos extraidos e contadores de documentos.
4. Caso o GPT identifique essa categoria como principal ou secundaria, a pasta correspondente em `folders/processados/` sera criada automaticamente e o feedback template passara a sugerir o mesmo nome.
5. (Opcional) Para pre-treinar sem novos documentos de entrada, execute `python main.py` ap+�s adicionar os arquivos a `knowledge_sources/`; o scan inicial popula o vocabul+�rio e fica disponivel para futuras decis+�es.

### Feedback inteligente e aprendizado continuo
- O template `feedback.txt` agora contem campos explicitos (`status`, `confianca_revisada`, `areas_secundarias`, `motivos_relevantes`, `motivos_criticos`, `palavras_relevantes`, `palavras_irrelevantes`, `aprovar_para_conhecimento`, `marcar_reanalise`, `categoria_feedback`, `observacoes`). Separe listas por virgula.
- O watcher `FeedbackWatcher` interpreta esses campos, armazena o feedback detalhado e ajusta dinamicamente: contagens de aprovacao/rejeicao, listas de palavras reforcadas/removidas, pedidos de reanalise e aprovacoes para a base documental.
- Ajustes positivos elevam a confianca final (at+� +0,03) enquanto rejeicoes ou multiplos pedidos de reprocessamento reduzem a nota (ate -0,05). Esses deltas sao registrados em `feedback_adjustment_details` e exibidos na analise.
- Cada feedback processado e arquivado em `folders/feedback/processado/<categoria>/`, facilitando auditoria historica.
- Campos `palavras_relevantes` e `palavras_irrelevantes` alteram imediatamente o dicionario de palavras-chave da categoria; os logs listam as entradas adicionadas ou removidas.
- Quando `marcar_reanalise: sim`, o registro permanece marcado para reforco em futuras execucoes; `aprovar_para_conhecimento: sim` sinaliza que o arquivo pode ser incorporado a `knowledge_sources/` sem passos adicionais.

### Mensageria Teams
- `teams_activity_webhook_url` envia alertas transacionais: documento recebido, processamento iniciado, conclusao ou falha.
- `teams_webhook_url` continua dedicado ao Adaptive Card completo com resumo, linha do tempo e matches.
- As mensagens de atividade compartilham o mesmo formato (Adaptive Card compacto) e incluem fatos como ID do processo, tamanho do arquivo e destino.
- Logs de erro de envio sao registrados em `logs/system.log` sem interromper o pipeline.

## 5. Observabilidade e logs
- `logs/system.log`: cronologia completa com ID de processamento, inicio/fim de etapas, scores e diagnosticos detalhados.
- `logs/activity.jsonl`: eventos estruturados (`processing_started`, `processing_stage`, `taxonomy_refinement`, `processing_timeline_summary`, `processing_internal_error`, etc.). Pode ser ingerido em ferramentas de observabilidade.
- `_ProcessingTimeline.records()`: usado para gerar Adaptive Cards e sumarizar duracoes (exposto via `processing_timeline_summary`).
- Logs adicionais relevantes:
  - Resultado da camada heuristica (acao, categoria promovida, top score, scores compostos).
  - Estado da pasta `em_processamento` apos movimentacoes.
  - Feedbacks aplicados (ou rejeitados) com identificacao do documento.

## 6. Notificacoes via Microsoft Teams
- Configure `DOC_ANALYZER_TEAMS_WEBHOOK_URL` e `DOC_ANALYZER_TEAMS_ACTIVITY_WEBHOOK_URL` no `.env` (vari+�veis legadas `TEAMS_WEBHOOK_URL` e `TEAMS_ACTIVITY_WEBHOOK_URL` ainda funcionam).
- Cada analise concluida envia um Adaptive Card contendo:
  - nome do arquivo, categoria, confianca, caminho do ZIP;
  - resumo das etapas com duracao (linha do tempo);
  - principais matches da base de conhecimento;
  - resumo textual (320 caracteres) e decisao heuristica.
- Botao "Abrir pasta do artefato" aponta para o caminho local do ZIP, facilitando auditoria.
- Logs registram qualquer falha ao enviar o card (`Falha ao enviar notificacao Teams`). Nenhum erro bloqueia o pipeline.

## 7. Feedback colaborativo
- `feedback.txt` agora orienta a revis+�o com perguntas diretas:
  - `confirmar_categoria_principal: sim | nao` e `trecho_evidencia_<slug>` (para a categoria principal e as sugeridas) garantem que o revisor cole o trecho literal do documento.
  - `acao_incluir_conhecimento_<slug>: sim | nao` controla se o trecho ser+� gravado como `knowledge_sources/<categoria>/feedback_<hash>.txt`, alimentando automaticamente a camada documental.
  - `categoria_nome_<slug>` vem preenchido (n+�o alterar) e permite que o watcher associe o slug ao nome oficial da categoria.
  - `categoria_alternativa_<slug>` e `areas_secundarias` consolidam categorias adicionais confirmadas pelo humano.
- Cada feedback processado +� arquivado em `folders/feedback/processado/<categoria>/` e gera uma entrada completa no `feedback_history`, incluindo quais trechos viraram evid+�ncia documental.
- O `FeedbackWatcher` passa a:
  - recalibrar confian+�a e +�reas secund+�rias com base nas respostas,
  - incorporar as evid+�ncias aprovadas ao diret+�rio da categoria (refrescando tokens e termos recorrentes),
  - manter estat+�sticas de aprova+�+�o/reprocessamento por categoria.
- Ferramenta r+�pida: `tools/submit_feedback.py` continua dispon+�vel para registrar corre+�+�es simples via CLI (gera `.json` pronto em `folders/feedback/`). Use `--dry-run` para validar o conte+�do antes de gravar.

## 8. Configuracao e parametros (.env)
- Toda a configuracao vive no `.env`. Nunca commite credenciais. Copie `/.env.example` para `.env` e ajuste os valores que estao em branco.

### 8.0 Criando o arquivo `.env`
```powershell
Copy-Item .env.example .env
```
```bash
cp .env.example .env
```
Esses comandos replicam o template com todos os defaults e deixam o arquivo pronto para receber as chaves reais em cada ambiente (dev/homolog/prod).

### 8.1 Escolhendo onde as pastas vao morar (`DOC_ANALYZER_STORAGE_MODE`)
- `relative` (default): base = `<pasta_onde_o_servico_roda>/<DOC_ANALYZER_STORAGE_RELATIVE_ROOT>`. Ex.: rodando em `/opt/classificador`, as pastas ficam em `/opt/classificador/folders/...`.
- `absolute`: informe `DOC_ANALYZER_STORAGE_ABSOLUTE_ROOT` (ex.: `D:/classificador_prod` ou `/srv/classificador`). O processo cria/usa `entrada`, `em_processamento`, etc dentro desse caminho. Antes de subir em producao, crie o diretorio e garanta permissao de escrita para o usuario do servico.
- `network`: igual ao `absolute`, mas apontando para um compartilhamento montado (`\\fileserver\classificador`, `/mnt/classificador`, etc.). Monte o share antes de iniciar o servico e registre o comando em `DOC_ANALYZER_STORAGE_MOUNT_COMMAND`. Se a montagem usar credenciais, documente o usuario/senha/dominio nas variaveis `DOC_ANALYZER_STORAGE_SERVICE_*`.
> Se `DOC_ANALYZER_STORAGE_ABSOLUTE_ROOT` ficar vazio, mesmo nos modos `absolute` ou `network`, o sistema volta a se comportar como `relative`.

### 8.2 Variaveis obrigatorias (LLM e pipeline)
| Variavel | Descricao | Default / exemplo |
| --- | --- | --- |
| `DOC_ANALYZER_API_KEY` | Chave da OpenAI (usada tambem como fallback para Azure). | `` |
| `DOC_ANALYZER_MODEL` | Modelo principal para analise GPT. | `gpt-5` |
| `DOC_ANALYZER_CROSS_MODEL` | Modelo secundario para validacao cruzada. | `gpt-5` |
| `DOC_ANALYZER_CONFIDENCE_THRESHOLD` | Confianca minima (0-1) exigida pelo `Validator`. | `0.8` |
| `DOC_ANALYZER_MAX_RETRIES` | Tentativas adicionais quando a confianca nao atinge o corte. | `3` |
| `DOC_ANALYZER_TEMPERATURE` | Temperatura aplicada nas chamadas GPT. | `1.0` |
| `DOC_ANALYZER_REQUEST_TIMEOUT` | Timeout em segundos por chamada GPT. | `60` |
| `DOC_ANALYZER_POLL_INTERVAL` | Intervalo (s) de varredura da pasta de entrada. | `10` |
| `DOC_ANALYZER_FEEDBACK_INTERVAL` | Intervalo (s) de varredura da pasta de feedback. | `15` |
| `DOC_ANALYZER_PROCESSING_WORKERS` | Threads paralelas do `DocumentProcessor`. | `2` |
| `DOC_ANALYZER_LOG_LEVEL` | Nivel de log (`DEBUG`, `INFO`, etc.). | `INFO` |
| `DOC_ANALYZER_LOG_FILE` | Caminho do log estruturado (`.jsonl`). | `logs/activity.jsonl` |
| `DOC_ANALYZER_TEXT_LOG_FILE` | Caminho do log textual. | `logs/system.log` |
| `DOC_ANALYZER_KNOWLEDGE_BASE_PATH` | Arquivo JSON da base de conhecimento. | `knowledge.json` |
| `DOC_ANALYZER_CATEGORY_KNOWLEDGE_ROOT` | Pasta com artefatos por categoria. | `knowledge_sources` |

### 8.3 Subpastas e estrutura interna
| Variavel | Descricao | Default / exemplo |
| --- | --- | --- |
| `DOC_ANALYZER_STORAGE_MODE` | Modo de resolucao de pastas (`relative`, `absolute`, `network`). | `relative` |
| `DOC_ANALYZER_STORAGE_RELATIVE_ROOT` | Base local quando o modo e `relative`. | `folders` |
| `DOC_ANALYZER_STORAGE_ABSOLUTE_ROOT` | Base absoluta para `absolute` ou `network`. | `` |
| `DOC_ANALYZER_STORAGE_AUTO_CREATE` | Cria automaticamente a estrutura se `true`. | `true` |
| `DOC_ANALYZER_STORAGE_CREATE_DEFAULT_CATEGORIES` | Gera pastas padrao de categorias se `true`. | `true` |
| `DOC_ANALYZER_INPUT_SUBDIR` | Nome da subpasta de entrada (ou caminho absoluto). | `entrada` |
| `DOC_ANALYZER_PROCESSING_SUBDIR` | Subpasta de processamento ativo. | `em_processamento` |
| `DOC_ANALYZER_PROCESSING_FAIL_SUBDIR` | Subpasta de falhas internas. | `_falhas` |
| `DOC_ANALYZER_PROCESSED_SUBDIR` | Subpasta de saida (contendo os ZIPs). | `processados` |
| `DOC_ANALYZER_FEEDBACK_SUBDIR` | Subpasta onde chegam feedbacks. | `feedback` |
| `DOC_ANALYZER_FEEDBACK_PROCESSED_SUBDIR` | Subpasta de feedback processado. | `processado` |
| `DOC_ANALYZER_COMPLEX_SAMPLES_SUBDIR` | Repositorio de casos complexos para QA. | `complex_samples` |
> Quando o valor definido for relativo, o sistema resolve `<raiz_escolhida>/<subpasta>`; se for absoluto, ele usa exatamente o caminho informado.

### 8.4 Integracoes (Teams e Azure)
| Variavel | Descricao | Default / exemplo |
| --- | --- | --- |
| `DOC_ANALYZER_TEAMS_WEBHOOK_URL` | Webhook para Adaptive Card final (vazio desativa). | `` |
| `DOC_ANALYZER_TEAMS_ACTIVITY_WEBHOOK_URL` | Webhook para eventos de atividade. | `` |
| `DOC_ANALYZER_USE_AZURE` | Ativa modo Azure OpenAI (`true`/`false`). | `false` |
| `DOC_ANALYZER_AZURE_ENDPOINT` | Endpoint completo do recurso Azure. | `https://seu-recurso.openai.azure.com` |
| `DOC_ANALYZER_AZURE_API_KEY` | Chave do recurso Azure. | `` |
| `DOC_ANALYZER_AZURE_DEPLOYMENT` | Deployment configurado no Azure. | `` |
| `DOC_ANALYZER_AZURE_API_VERSION` | Versao da API utilizada. | `2024-02-01` |

### 8.5 Credenciais de rede / montagem (opcional)
| Variavel | Descricao | Default / exemplo |
| --- | --- | --- |
| `DOC_ANALYZER_STORAGE_SERVICE_USER` | Usuario de servico para montar o compartilhamento. | `` |
| `DOC_ANALYZER_STORAGE_SERVICE_PASSWORD` | Senha do usuario de servico (documente ou mantenha no cofre). | `` |
| `DOC_ANALYZER_STORAGE_SERVICE_DOMAIN` | Dominio ou realm do usuario de rede. | `` |
| `DOC_ANALYZER_STORAGE_MOUNT_COMMAND` | Comando documentado para montar o compartilhamento. | `mount -t cifs //<servidor>/classificador /mnt/classificador -o user=$DOC_ANALYZER_STORAGE_SERVICE_USER,domain=$DOC_ANALYZER_STORAGE_SERVICE_DOMAIN` |

### 8.6 Variaveis legadas
- `OPENAI_API_KEY`, `CLASSIFIER_*`, `TEAMS_WEBHOOK_URL`, `TEAMS_ACTIVITY_WEBHOOK_URL`, `URL_BASE`, `API_KEY`, `DEPLOYMENT_NAME` e similares continuam reconhecidos pela camada de compatibilidade. Em novos ambientes, prefira sempre os nomes `DOC_ANALYZER_*`.
## 9. Integracao Azure OpenAI
- Defina `DOC_ANALYZER_USE_AZURE=true` no `.env` e informe:
  - `DOC_ANALYZER_AZURE_ENDPOINT`: URL do recurso Azure OpenAI (ex.: `https://seu-recurso.openai.azure.com`).
  - `DOC_ANALYZER_AZURE_API_KEY`: chave do recurso (pode ser compartilhada com `DOC_ANALYZER_API_KEY` se preferir manter uma unica variavel).
  - `DOC_ANALYZER_AZURE_DEPLOYMENT`: nome do deployment do modelo chat (ex.: `gpt-4o-mini` configurado na empresa).
  - `DOC_ANALYZER_AZURE_API_VERSION`: versao da API (padrao `2024-02-01`, ajuste conforme politica interna).
- Variaveis legadas seguem valendo: `USE_AZURE_OPENAI`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT`, `AZURE_OPENAI_API_VERSION`, `AZURE_OPENAI_KEY`, `URL_BASE`, `API_KEY`, `DEPLOYMENT_NAME`.
- Quando o modo Azure estiver ativo, `GPTCore` usa `AzureOpenAI` (SDK `openai` >= 1.16) e faz as chamadas em cima do deployment informado.

## 10. Manual operacional (usuarios finais)
- Consultar `docs/manual_operacional.md` para o passo a passo sem comandos (entrada, notificacoes Teams, coleta dos ZIPs e envio de feedback).
- Inclui orientacao para uso do `tools/feedback_gui.py`, que abre uma janela simples para registrar feedback sem utilizar o terminal.
- Recomenda-se distribuir o manual em PDF ou wiki interna e criar atalhos para `tools/feedback_gui.py` nas esta+�+�es da equipe.

## 11. Demonstracao de fluxo end-to-end
1. Gere amostras: `python tools/create_sample_documents.py --drop-into-entrada`.
2. Inicie o sistema: `python main.py`.
3. Observe em `logs/system.log` a sequencia `extracao_texto -> analise_gpt -> validacao -> refinamento_taxonomia -> ...`.
4. Ao final, confira a pasta `folders/processados/<categoria>/` para o ZIP gerado e o card recebido no Teams.
5. Caso identifique classificacao incorreta, rode `python tools/submit_feedback.py ...` para enviar feedback. O watcher aplicara o aprendizado e arquivara o JSON automaticamente.
6. Reexecute a amostra se desejar verificar a melhoria (qualquer nova classificacao passara a considerar a categoria ajustada).

## 12. Tecnologias e dependencias
- Python 3.11+ (recomendado) com bibliotecas opcionais: `PyMuPDF (fitz)`, `python-docx`. Sem elas, PDFs/DOCX nao sao processados.
- OpenAI ou Azure OpenAI (modelos chat) configuraveis via `.env`.
- Adaptive Cards (Microsoft Teams) a necessita apenas do webhook; nenhuma SDK adicional foi utilizada (envio via `urllib.request`).
- Logs estruturados em JSON (compativeis com observabilidade centralizada) e arquivos de texto para auditoria rapida.

## 13. Procedimentos de execucao e manutencao
- **Execucao**: `python main.py`. Use `Ctrl+C` para desligamento limpo (aguarda tarefas pendentes).
- **Teste temporizado**: `python test_run.py` ou defina `CLASSIFIER_TEST_DURATION` para segundos desejados.
- **Esteira automatizada**: `python tests/run_pipeline_checks.py` executa compilacao, gera amostras, roda o pipeline em modo teste e valida a criacao dos ZIPs.
- **Validacao rapida**: `python -m compileall core main.py tools/create_sample_documents.py` (verificacao sintatica rapida).
- **Limpeza de falhas**: revisar periodicamente `folders/em_processamento/_falhas`. Os arquivos permanecem la para revisao manual.
- **Monitoramento**: utilize `logs/activity.jsonl` para integrar com dashboards (cada linha e um JSON independente). O campo `records` do evento `processing_timeline_summary` lista duracao de todas as etapas.
- **Extensoes futuras**: para novos tipos de arquivo, adicione a extensao em `SUPPORTED_EXTENSIONS` e implemente o metodo `_read_<ext>()`; para novas notificacoes, estenda `TeamsNotifier` ou crie novos notifiers seguindo a mesma interface (`send_analysis_summary`). 
- **Configuracao de credenciais**: antes de rodar em producao, defina as variaveis de ambiente (ou um `.env`) com as chaves reais (`AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_KEY`, `AZURE_OPENAI_DEPLOYMENT`, `OPENAI_API_VERSION`, `TEAMS_WEBHOOK_URL`, etc.).
## 14. Guia de operacao para a equipe (ferias)

### 14.1 Checklist de preparacao
1. Atualize o repositorio local: `git fetch origin` e `git checkout feature/centralize-env` (ou crie um clone novo a partir dessa branch).
2. Crie/ative um virtualenv (`python -m venv .venv` e `.\venv\Scripts\activate` no Windows ou `source .venv/bin/activate` no Linux).
3. Instale dependencias: `pip install -r requirements.txt` (repita em producao sempre que o arquivo mudar).
4. Copie `/.env.example` para `.env` e preencha cada variavel seguindo a tabela da secao 8. Guarde o arquivo real fora do Git (Ansible, KeyVault, secrets manager, etc.).
5. Se `DOC_ANALYZER_STORAGE_MODE` for `absolute` ou `network`, garanta que o caminho informado exista e tenha permissao de escrita para o usuario do servico. Documente o comando de montagem no campo `DOC_ANALYZER_STORAGE_MOUNT_COMMAND`.
6. Confirme conectividade com a OpenAI/Azure executando `python test_run.py` (o primeiro ciclo ja valida as credenciais). Se preferir algo mais rapido, utilize `python -c "from core.settings import load_settings; from core.gpt_core import GPTCore; from core.knowledge_base import KnowledgeBase; s = load_settings(); GPTCore(s.to_dict(), KnowledgeBase(s.knowledge_base_path, s.category_knowledge_root)).ensure_available(); print(\"ok\")"`.
7. Valide os webhooks do Teams com um `curl -X POST <url> -d '{"text":"ping"}'` para evitar surpresas na hora do deploy.
8. Tenha pelo menos um documento de teste em `samples/` e use o script `tools/create_sample_documents.py --drop-into-entrada` para gerar amostras sempre que precisar validar o fluxo completo.

### 14.2 Como testar antes da liberacao
- **Lint sintatico rapido**: `python -m compileall core main.py tools/create_sample_documents.py`.
- **Smoke test do pipeline**: defina `CLASSIFIER_TEST_DURATION=30` e execute `python test_run.py`. Verifique se arquivos entram/saem das pastas configuradas e se os logs registram `processing_timeline_summary` sem erros.
- **Teste com amostra real**: solte um PDF/DOCX na pasta de entrada configurada e acompanhe a movimentacao ate `processados/<categoria>/`. Abra o ZIP e confira `analise.txt` e `feedback.txt`.
- **Feedback round-trip**: preencha o `feedback.txt`, salve na pasta de feedback e confirme se o item aparece em `feedback/processado/` e se a base (`knowledge.json`) recebeu o ajuste.
- **Notificacoes Teams**: apos o smoke test, abra o canal configurado e confirme o recebimento dos Adaptive Cards. Em caso de erro, o log `system.log` trara a mensagem `Falha ao enviar notificacao Teams`.
- **Integracao Azure**: quando `DOC_ANALYZER_USE_AZURE=true`, execute `python - <<"PY"` carregando `from core.settings import load_settings; from core.gpt_core import GPTCore` para validar `gpt_core.ensure_available()` antes do deploy.

### 14.3 Rotina diaria de operacao
- Monitorar `logs/system.log` (erros) e `logs/activity.jsonl` (eventos) � importar o JSONL em dashboards ajuda a rastrear gargalos e taxa de sucesso.
- Conferir a fila de `em_processamento` a cada inicio de turno; se houver arquivos presos, abra o log e verifique o ultimo evento (`processing_internal_error`, `taxonomy_refinement`, etc.).
- Validar que os webhooks continuam ativos (os Teams Cards mostram o ID do processo e o caminho gerado; se sumirem, revisar secret ou firewall).
- Revisar `folders/em_processamento/_falhas` diariamente. Itens nessa pasta precisam de tratamento manual; depois de corrigir, mova o arquivo de volta para `entrada`.
- Atualizar a base de conhecimento com feedback positivo relevante: confirme se os trechos gerados em `knowledge_sources/<categoria>/feedback_*.txt` fazem sentido e arquive os redundantes.

### 14.4 Ajustes frequentes
- **Trocar path de saida**: ajuste `DOC_ANALYZER_STORAGE_MODE` e atualize `DOC_ANALYZER_STORAGE_ABSOLUTE_ROOT` ou `DOC_ANALYZER_STORAGE_RELATIVE_ROOT`. Rode `python test_run.py` para validar permissoes antes de voltar ao modo daemon.
- **Alterar frequencia de polling**: modifique `DOC_ANALYZER_POLL_INTERVAL` ou `DOC_ANALYZER_FEEDBACK_INTERVAL` (segundos). Valores muito baixos (<5s) podem gerar carga desnecessaria em discos de rede.
- **Aumentar throughput**: eleve `DOC_ANALYZER_PROCESSING_WORKERS`. Sempre monitore CPU/RAM do servidor e ajuste o limite conforme a margem disponivel.
- **Novo webhook/ambiente**: atualize `DOC_ANALYZER_TEAMS_WEBHOOK_URL` e `DOC_ANALYZER_TEAMS_ACTIVITY_WEBHOOK_URL`, reinicie o servico (`Ctrl+C` + `python main.py`) e valide com um documento de teste.
- **Credenciais rotacionadas**: substitua a chave no `.env`, reinicie o processo e acompanhe o log inicial (`GPTCore configurado...`). Se estiver usando Azure Key Vault, mantenha a URL em `DOC_ANALYZER_AZURE_KEYVAULT_URL` (variavel opcional) para future hooks.

### 14.5 Fallbacks rapidos
- **GPT indisponivel**: o watcher move o arquivo de volta para `entrada` e registra `gpt_indisponivel`. Mantenha um plano B (ex.: acionar o time para usar o modo manual de classificacao) e tente novamente apos confirmar status da API.
- **Compartilhamento inacessivel**: use o comando documentado em `DOC_ANALYZER_STORAGE_MOUNT_COMMAND` para remontar. Se precisar, utilize `net use` (Windows) ou `mount -t cifs` (Linux) com as credenciais `DOC_ANALYZER_STORAGE_SERVICE_*`.
- **Erro persistente em documento especifico**: mova o arquivo para `complex_samples` com um sufixo de data, registre o erro e avance com os proximos. Posteriormente trate o caso manualmente ou ajuste heuristicas/taxonomia conforme necessidade.

