# Classifica Document Pipeline

## Documentacao complementar
- Guia do usuario: [docs/user_guide.md](docs/user_guide.md)
- Arquitetura e fluxo: [docs/architecture.md](docs/architecture.md)
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
  - `core/watcher.FeedbackWatcher`: interpreta feedbacks (`.json` ou `.txt`), normaliza campos (`documento`, `status`, `nova_categoria`, `observacoes`), processa confirmações/evidências por categoria e arquiva tanto o formulário quanto os trechos aprovados em `knowledge_sources/<categoria>/feedback_*.txt`.
- **Pipeline de processamento**
  - `core/processor.DocumentProcessor`: encapsula todo o fluxo. Cada etapa gera eventos via `_ProcessingTimeline`, inclui metricas de duracao, aciona heuristicas, atualiza a base e dispara notificacoes.
  - `core/taxonomy.TaxonomyRuleEngine`: calcula scores de palavras-chave por categoria, ajusta classificacoes (promocao/reducao) e gera composicao de confianca (LLM + heuristica + conhecimento).
  - `core/notifier.TeamsNotifier`: publica o Adaptive Card final e os avisos transacionais (recebido/processado) nos webhooks configurados em `config.json`.
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
- Durante a analise, `GPTCore` injeta esse vocabulário nos prompts (primario, auditoria e I3) e adiciona uma camada de validacao `document_knowledge` que ajusta categorias, areas secundarias e confianca final.
- Novas categorias sao provisionadas automaticamente (pasta + `category.json`) quando o pipeline consolida uma classificacao principal ou secundaria inedita; tambem e possivel criar a pasta manualmente e ela sera absorvida no proximo refresh.
- Para validacao manual, um documento por categoria foi colocado em `knowledge_sources/` (contabilidade, tecnologia, tesouraria) e outros dois exemplos por categoria estao em `samples/` para testes do usuario.

Passo a passo para introduzir uma nova categoria principal:
1. Crie uma pasta dentro de `knowledge_sources/` usando um nome descritivo (ex.: `knowledge_sources/tesouraria`). O sistema normaliza o nome para o modelo e registra metadados em `category.json` na primeira execucao.
2. Adicione ao menos um documento ja validado para essa categoria (TXT, PDF ou DOCX). Os arquivos podem ser adicionados gradualmente; apenas os novos sao processados a cada varredura.
3. Execute o pipeline (`python main.py`). A camada documental sera atualizada nos logs (`KnowledgeBase.refresh_category_documents`), exibindo hashes, termos extraidos e contadores de documentos.
4. Caso o GPT identifique essa categoria como principal ou secundaria, a pasta correspondente em `folders/processados/` sera criada automaticamente e o feedback template passara a sugerir o mesmo nome.
5. (Opcional) Para pre-treinar sem novos documentos de entrada, execute `python main.py` após adicionar os arquivos a `knowledge_sources/`; o scan inicial popula o vocabulário e fica disponivel para futuras decisões.

### Feedback inteligente e aprendizado continuo
- O template `feedback.txt` agora contem campos explicitos (`status`, `confianca_revisada`, `areas_secundarias`, `motivos_relevantes`, `motivos_criticos`, `palavras_relevantes`, `palavras_irrelevantes`, `aprovar_para_conhecimento`, `marcar_reanalise`, `categoria_feedback`, `observacoes`). Separe listas por virgula.
- O watcher `FeedbackWatcher` interpreta esses campos, armazena o feedback detalhado e ajusta dinamicamente: contagens de aprovacao/rejeicao, listas de palavras reforcadas/removidas, pedidos de reanalise e aprovacoes para a base documental.
- Ajustes positivos elevam a confianca final (até +0,03) enquanto rejeicoes ou multiplos pedidos de reprocessamento reduzem a nota (ate -0,05). Esses deltas sao registrados em `feedback_adjustment_details` e exibidos na analise.
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
- Configure `teams_webhook_url` em `config.json` (ou via variavel de ambiente `TEAMS_WEBHOOK_URL`).
- Cada analise concluida envia um Adaptive Card contendo:
  - nome do arquivo, categoria, confianca, caminho do ZIP;
  - resumo das etapas com duracao (linha do tempo);
  - principais matches da base de conhecimento;
  - resumo textual (320 caracteres) e decisao heuristica.
- Botao "Abrir pasta do artefato" aponta para o caminho local do ZIP, facilitando auditoria.
- Logs registram qualquer falha ao enviar o card (`Falha ao enviar notificacao Teams`). Nenhum erro bloqueia o pipeline.

## 7. Feedback colaborativo
- `feedback.txt` agora orienta a revisão com perguntas diretas:
  - `confirmar_categoria_principal: sim | nao` e `trecho_evidencia_<slug>` (para a categoria principal e as sugeridas) garantem que o revisor cole o trecho literal do documento.
  - `acao_incluir_conhecimento_<slug>: sim | nao` controla se o trecho será gravado como `knowledge_sources/<categoria>/feedback_<hash>.txt`, alimentando automaticamente a camada documental.
  - `categoria_nome_<slug>` vem preenchido (não alterar) e permite que o watcher associe o slug ao nome oficial da categoria.
  - `categoria_alternativa_<slug>` e `areas_secundarias` consolidam categorias adicionais confirmadas pelo humano.
- Cada feedback processado é arquivado em `folders/feedback/processado/<categoria>/` e gera uma entrada completa no `feedback_history`, incluindo quais trechos viraram evidência documental.
- O `FeedbackWatcher` passa a:
  - recalibrar confiança e áreas secundárias com base nas respostas,
  - incorporar as evidências aprovadas ao diretório da categoria (refrescando tokens e termos recorrentes),
  - manter estatísticas de aprovação/reprocessamento por categoria.
- Ferramenta rápida: `tools/submit_feedback.py` continua disponível para registrar correções simples via CLI (gera `.json` pronto em `folders/feedback/`). Use `--dry-run` para validar o conteúdo antes de gravar.

## 8. Configuracao e parametros (config.json)
- `api_key`, `model`, `cross_validation_model`: credenciais/modelos usados pelo GPT.
- `use_azure`, `azure_endpoint`, `azure_api_key`, `azure_deployment`, `azure_api_version`: configuracoes para usar Azure OpenAI (ver secao 9).
- `confidence_threshold`, `max_retries`: controle de reforco da camada Validator (padrao 0.8 e 3 tentativas, com reanalise automatica ate superar 80%).
- `polling_interval`, `feedback_polling_interval`: frequencia de varredura dos watchers (segundos).
- `processing_workers`: numero de threads paralelas para analise.
- `log_level`, `log_file`, `text_log_file`: configuracao de log.
- `knowledge_base_path`: caminho do arquivo JSON da base de conhecimento.
- `category_knowledge_root`: pasta raiz usada para armazenar os documentos de referencia por categoria (auto-criada e monitorada continuamente).
- `teams_webhook_url`: URL do webhook do Microsoft Teams para envio dos Adaptive Cards (string vazia desativa).
- `teams_activity_webhook_url`: webhook adicional para alertas de entrada/processamento (pode ser o mesmo canal do card).
- `storage_root`: raiz onde ficam entrada, processamento, processados, feedback e complex_samples (aceita caminho absoluto).
- `input_subdir`, `processing_subdir`, `processed_subdir`, `processing_fail_subdir`: subpastas (ou caminhos absolutos) configuraveis para adequar a rede da empresa.
- `feedback_subdir`, `feedback_processed_subdir`: controlam onde feedbacks chegam e sao arquivados.
- `complex_samples_subdir`: armazena casos de teste complexos.
- Todos os parametros podem ser sobrescritos via variaveis de ambiente listadas em `load_config()` (ex.: `CLASSIFIER_POLL_INTERVAL`, `TEAMS_WEBHOOK_URL`). Valores numericos sao convertidos automaticamente.
- **Criando um `.env` rapidamente (PowerShell)**:
  ```powershell
  @"
  AZURE_OPENAI_ENDPOINT=COLE_SEU_OPENAI_ENPOINT
  AZURE_OPENAI_KEY=COLE_SUA_CHAVE_AQUI
  AZURE_OPENAI_DEPLOYMENT=gpt4o-mini
  OPENAI_API_VERSION=2024-08-01-preview
  TEAMS_WEBHOOK_URL=https://contoso.webhook.office.com/webhookb2/...
  USE_AZURE_OPENAI=true
  "@ | Out-File -FilePath .env -Encoding UTF8
  ```
  Ajuste os valores antes de executar. Para ambientes sem PowerShell, crie o arquivo `.env` manualmente com essas chaves.

## 9. Integracao Azure OpenAI
- Defina `use_azure: true` no `config.json` e informe:
  - `azure_endpoint`: URL do recurso Azure OpenAI (ex.: `https://seu-recurso.openai.azure.com`).
  - `azure_api_key`: chave do recurso (pode ser definida via `AZURE_OPENAI_API_KEY`).
  - `azure_deployment`: nome do deployment do modelo chat (ex.: `gpt-4o-mini` configurado na empresa).
  - `azure_api_version`: versao da API (padrao `2024-02-01`, ajuste conforme politica interna).
- Opcionalmente, use as variaveis `USE_AZURE_OPENAI`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT`, `AZURE_OPENAI_API_VERSION` e `AZURE_OPENAI_API_KEY` para sobrescrever valores.
  - Alias suportados: `URL_BASE` (endpoint), `API_KEY` (chave) e `DEPLOYMENT_NAME` (deployment).
- Quando `use_azure` estiver ativo, `GPTCore` passa a utilizar a classe `AzureOpenAI` do SDK oficial (`openai` >= 1.16) e a chamada `chat.completions.create` com o deployment configurado.

## 10. Manual operacional (usuarios finais)
- Consultar `docs/manual_operacional.md` para o passo a passo sem comandos (entrada, notificacoes Teams, coleta dos ZIPs e envio de feedback).
- Inclui orientacao para uso do `tools/feedback_gui.py`, que abre uma janela simples para registrar feedback sem utilizar o terminal.
- Recomenda-se distribuir o manual em PDF ou wiki interna e criar atalhos para `tools/feedback_gui.py` nas estações da equipe.

## 11. Demonstracao de fluxo end-to-end
1. Gere amostras: `python tools/create_sample_documents.py --drop-into-entrada`.
2. Inicie o sistema: `python main.py`.
3. Observe em `logs/system.log` a sequencia `extracao_texto -> analise_gpt -> validacao -> refinamento_taxonomia -> ...`.
4. Ao final, confira a pasta `folders/processados/<categoria>/` para o ZIP gerado e o card recebido no Teams.
5. Caso identifique classificacao incorreta, rode `python tools/submit_feedback.py ...` para enviar feedback. O watcher aplicara o aprendizado e arquivara o JSON automaticamente.
6. Reexecute a amostra se desejar verificar a melhoria (qualquer nova classificacao passara a considerar a categoria ajustada).

## 12. Tecnologias e dependencias
- Python 3.11+ (recomendado) com bibliotecas opcionais: `PyMuPDF (fitz)`, `python-docx`. Sem elas, PDFs/DOCX nao sao processados.
- OpenAI ou Azure OpenAI (modelos chat) configuraveis via `config.json`.
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
