# Manual operacional (usuarios finais)

## 1. Objetivo
Este manual descreve como a equipe operacional deve usar o Classifica Document Pipeline para enviar documentos, acompanhar resultados e registrar feedback.

## 2. Recebimento de documentos
1. Solte os arquivos na pasta de entrada configurada (`DOC_ANALYZER_INPUT_SUBDIR`).
2. O sistema move o arquivo para a fila (`em_processamento`) e envia um aviso de atividade no Teams.
3. Quando a analise termina, o arquivo sai da fila e um pacote `.zip` e criado na pasta `processados/<categoria>/`.

## 3. Conteudo do pacote gerado
Cada pacote contem:
- Arquivo original.
- `analise.txt`: relatorio com categoria, confianca, justificativas, matches e resumo.
- `feedback.txt`: modelo para revisores confirmarem ou corrigirem a classificacao.

## 4. Notificacoes Teams
- Configure os webhooks no `.env` (`DOC_ANALYZER_TEAMS_WEBHOOK_URL` e `DOC_ANALYZER_TEAMS_ACTIVITY_WEBHOOK_URL`).
- O webhook de atividade mostra eventos de entrada/fim de processamento.
- O Adaptive Card apresenta o resumo completo, caminhos de saida e links uteis.

## 5. Envio de feedback
1. Abra o `feedback.txt` correspondente ao documento analisado.
2. Preencha os campos `confirmar_categoria_principal`, `nova_categoria` (se aplicavel) e `observacoes`.
3. Informe trechos de evidencias nos campos `trecho_evidencia_<slug>` para reforcar o conhecimento.
4. Salve o arquivo na pasta de feedback configurada (`DOC_ANALYZER_FEEDBACK_SUBDIR`).
5. O sistema processa o arquivo, move para `feedback/processado/` e registra o aprendizado na `KnowledgeBase`.

## 6. Tratamento de falhas
- Arquivos problematicos vao para `em_processamento/_falhas`.
- Consulte `logs/system.log` para entender o erro.
- Depois de corrigir o problema (permissao, formato, etc.), mova o arquivo de volta para a pasta de entrada.

## 7. Boas praticas
- Mantenha a pasta de entrada limpa: mova arquivos antigos para um arquivo frio.
- Acompanhe o canal do Teams diariamente para garantir que as notificacoes continuam chegando.
- Registre feedback sempre que identificar categoria incorreta ou oportunidade de reforcar o texto do relatorio.
