# Manual Operacional - Classifica

## 1. Objetivo
Este guia explica o uso diario do Classifica sem comandos tecnicos. A equipe de operacoes seguira os passos abaixo para inserir documentos, acompanhar resultados, revisar pacotes gerados e fornecer feedback.

## 2. Estrutura de pastas
- `folders/entrada/`: coloque aqui os documentos brutos que precisam ser classificados.
- `folders/em_processamento/`: usado automaticamente pelo sistema enquanto o arquivo esta em analise.
- `folders/processados/<categoria>/`: sa+¡da final. Cada documento analisado gera um arquivo ZIP contendo:
  - documento original
  - `analise.txt` com justificativas e motivos
  - `feedback.txt` (modelo para ajuste manual, se desejar)
- `folders/feedback/`: local onde feedbacks preenchidos devem ser colocados (o sistema move para `folders/feedback/processado/` apos leitura).
- `logs/system.log`: registro textual das operacoes (para auditoria).

## 3. Fluxo do operador
1. **Envio do documento**
   - Arraste o arquivo para `folders/entrada/`.
   - O sistema detecta automaticamente, move para `folders/em_processamento/` e inicia a analise.
2. **Monitoramento**
   - Nao e necessario acompanhar o terminal. Uma notificacao no Microsoft Teams (Adaptive Card) aparece ao fim da analise contendo:
     - nome do documento
     - categoria atribuida e confianca
     - link direto para o ZIP gerado
     - resumo do texto, principais motivos e linha do tempo das etapas.
3. **Entrega**
   - Abra o ZIP indicado no card ou navegue ate `folders/processados/<categoria>/`.
   - O arquivo `analise.txt` traz os detalhes tecnicos; encaminhe-o para os responsaveis apropriados, se necessario.

## 4. Feedback de classificacao
O aprendizado ocorre quando feedbacks sao enviados para `folders/feedback/`.

### 4.1 Metodo padrao (arquivo de feedback)
1. Dentro do ZIP gerado ha um `feedback.txt`.
2. Edite o arquivo, marcando `correto` ou `incorreto`. Caso esteja incorreto, preencha a categoria correta e observacoes.
3. Salve o arquivo editado e mova-o para `folders/feedback/`.
4. O sistema processa o feedback automaticamente e arquiva o arquivo em `folders/feedback/processado/`.

### 4.2 Metodo Guiado (Janela grafica)
Para usuarios que preferem uma interface simples:
1. Clique duas vezes em `tools/feedback_gui.py` (pode ser criado um atalho na area de trabalho).
2. Preencha:
   - Documento analisado
   - Status (correto/incorreto)
   - Categoria correta (se aplicavel)
   - Observacoes (campo livre)
   - Autor (opcional)
3. Pressione **Enviar feedback**. O arquivo JSON correspondente sera criado automaticamente em `folders/feedback/`.
4. Uma mensagem de confirmacao exibira o caminho do arquivo gerado.

Ambos os metodos atualizam instantaneamente a base de conhecimento.

## 5. Notificacoes Teams
- Configure os webhooks no `.env` (`DOC_ANALYZER_TEAMS_WEBHOOK_URL` e `DOC_ANALYZER_TEAMS_ACTIVITY_WEBHOOK_URL`). Variaveis legadas (`TEAMS_WEBHOOK_URL`/`TEAMS_ACTIVITY_WEBHOOK_URL`) seguem aceitas.
- Cada card inclui um botao "Abrir pasta do artefato" apontando para o ZIP correspondente.
- Em caso de falha no envio, o log `system.log` registra o erro (sem impactar a classificacao).

## 6. Reprocessamento e falhas
- Caso algum documento apareca em `folders/em_processamento/_falhas/`, mova-o manualmente de volta para `folders/entrada/` apos verificar o motivo no log.
- Se o card comunicar baixa confianca ou categoria "Nao identificada", considere enviar feedback com a categoria correta para reforcar o modelo.

## 7. Checklist rapido
1. Coloque o arquivo na pasta `folders/entrada/`.
2. Aguarde notificacao ou confirme o ZIP em `folders/processados/`.
3. Revise `analise.txt` conforme necessario.
4. Forneca feedback pelo `feedback.txt` ou `tools/feedback_gui.py`.
5. Verifique periodicamente a pasta `_falhas` e o log caso algo nao conclua.

Seguindo esse roteiro a equipe opera o Classifica sem necessidade de comandos ou ajustes tecnicos.
