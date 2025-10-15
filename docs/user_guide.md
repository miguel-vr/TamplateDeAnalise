# Guia do Usuário – Classifica Document Pipeline

## 1. Visão geral
- O pipeline monitora continuamente a pasta de entrada configurada (`storage_root` + `input_subdir`) e processa cada documento compatível (`.pdf`, `.docx`, `.txt`).
- Cada documento gera um pacote `.zip` contendo o arquivo original, um relatório de análise (`analise.txt`) e o modelo de feedback (`feedback.txt`).
- As notificações de status são enviadas ao Microsoft Teams: um aviso no canal de atividade quando o documento é recebido, outro quando o processamento termina, e um Adaptive Card detalhado no canal de resultados.
- A base de conhecimento combina três camadas: histórico estruturado (`knowledge.json`), documentos reais por categoria (`knowledge_sources/`) e feedback humano consolidado.

## 2. Preparação do ambiente
1. **Clonar o projeto** e instalar dependências opcionais (`pip install -r requirements.txt` se existir).
2. **Configurar credenciais** (`config.json` ou variáveis de ambiente):
   - `api_key` ou parâmetros Azure para o modelo usado pelo GPT.
   - `teams_webhook_url`: webhook do Teams que recebe o Adaptive Card final.
   - `teams_activity_webhook_url`: webhook (pode ser o mesmo chat) para avisos de “documento recebido” e “documento processado”.
3. **Ajustar caminhos** conforme necessário:
   - `storage_root`: diretório base onde ficarão entrada, processamento, processados e feedback.
   - `input_subdir`, `processing_subdir`, `processed_subdir`, etc., podem ser alterados para refletir compartilhamentos de rede ou convenções internas.
   - `knowledge_base_path` pode ser apontado para um caminho absoluto compartilhado.
4. **Executar `python main.py`**. O serviço cria as pastas ausentes, verifica a conectividade com a API e inicia os watchers.

## 3. Ciclo operacional
1. **Colocar arquivos para análise** na pasta configurada (por padrão `folders/entrada/`).
2. **Receber notificações**:
   - Teams – Atividade: mensagem “Documento recebido” com ID do processamento, caminho e tamanho.
   - Teams – Atividade: mensagem “Processamento concluído” com categoria e link do artefato.
   - Teams – Resultados: Adaptive Card detalhando confiança, matches e cronograma das etapas.
3. **Consultar artefatos** em `processed_dir/<categoria>/<arquivo>.zip`. O `analise.txt` inclui todo o histórico de decisão e os insumos das camadas de validação.
4. **Enviar feedback**:
   - Abrir `feedback.txt` do pacote correspondente ou utilizar `tools/submit_feedback.py`.
   - Preencher os campos (ver seção 4). Salvar e mover para `feedback_dir` (padrão `folders/feedback/`).
   - O watcher processa, atualiza a base de conhecimento e arquiva o arquivo em `feedback_dir/processado/<categoria>/`.
5. **Auditar logs**: `logs/system.log` (texto) e `logs/activity.jsonl` (evento por linha) registram todo o fluxo.

## 4. Campos de feedback
Cada feedback impacta diretamente os pr�ximos resultados:
- `status`: `correto` ou `incorreto`.
- `confirmar_categoria_principal`: `sim` ou `nao`; confirma a decis�o principal e alimenta o hist�rico da categoria.
- `trecho_evidencia_<slug>`: cole o trecho literal que comprova cada categoria (prim�ria e sugeridas).
- `acao_incluir_conhecimento_<slug>`: marque `sim` para salvar o trecho como `knowledge_sources/<categoria>/feedback_<hash>.txt` e refor�ar a camada documental.
- `categoria_nome_<slug>`: campo pr�-preenchido usado pelo sistema para mapear o slug � categoria (n�o alterar).
- `categoria_alternativa_<slug>` e `areas_secundarias`: confirme categorias adicionais (marque `sim`/`nao` e liste as aprovadas).
- `nova_categoria`: utilize quando a classifica��o principal estiver incorreta.
- `confianca_revisada`: percentual opcional para calibrar a confian�a final.
- `motivos_relevantes` / `motivos_criticos`: evid�ncias favor�veis ou bloqueios encontrados.
- `palavras_relevantes` / `palavras_irrelevantes`: termos que devem ser refor�ados ou evitados.
- `aprovar_para_conhecimento`: `sim` quando o documento completo pode treinar a base.
- `marcar_reanalise`: `sim` para reprocessar automaticamente.
- `categoria_feedback`: permite arquivar o formul�rio em uma subpasta espec�fica (por padr�o usa a categoria atual).
- `observacoes`: coment�rios livres usados para auditoria.
## 5. Gerenciando categorias e conhecimento
1. **Adicionar categoria**: criar uma subpasta em `knowledge_sources/<nova_categoria>` e inserir documentos validados (TXT/PDF/DOCX). O próximo ciclo atualizará os termos característicos e listarão nos relatórios.
2. **Remover ou renomear**: ajuste o nome da pasta e o arquivo `category.json` gerado automaticamente. O sistema normaliza sinônimos via feedback.
3. **Monitorar aprendizados**: `analise.txt` mostra as palavras reforçadas/removidas e o balanço de feedback por categoria.

## 6. Customização rápida de caminhos
- Ajuste `storage_root` e subdiretórios no `config.json` para apontar para compartilhamentos de rede.
- Diretórios informados com caminhos absolutos são usados diretamente; strings relativas são resolvidas a partir de `storage_root`.
- Para logs ou base de conhecimento externos, configure `log_file`, `text_log_file` e `knowledge_base_path` com caminhos absolutos.

## 7. Resolução de problemas
- **Webhook não recebe mensagens**: verificar URLs (`teams_webhook_url`, `teams_activity_webhook_url`) e logs de erro (`Adaptive Card webhook retornou status ...`).
- **Documento não analisado**: checar logs `processing_internal_error`, extensão suportada e se o texto extraído tem tamanho mínimo.
- **Feedback ignorado**: campos inválidos são descartados com aviso em `system.log`. Verifique formatação (listas separadas por vírgula).
- **Reprocessamento automático**: quando `max_retries` é atingido e a confiança continua abaixo do limiar, o relatório avisa que a revisão humana é necessária.

## 8. Encerramento
Pressione `Ctrl + C` na janela do processo. O serviço sinaliza os watchers para parar, aguarda as tarefas em execução e encerra de forma segura.

