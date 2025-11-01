# Guia do usuario - Classifica Document Pipeline

## 1. Visao geral
- O pipeline monitora continuamente a pasta de entrada configurada e processa arquivos `.pdf`, `.docx` e `.txt`.
- Cada documento gera um pacote `.zip` com o arquivo original, `analise.txt` e `feedback.txt`.
- Notificacoes sao enviadas para o Microsoft Teams (atividade + Adaptive Card).

## 2. Preparacao do ambiente
1. Clonar o projeto e instalar dependencias (`pip install -r requirements.txt`).
2. Copiar `/.env.example` para `.env` e preencher as variaveis obrigatorias (veja README, secao 8).
3. Ajustar as pastas (`DOC_ANALYZER_STORAGE_MODE`, `DOC_ANALYZER_STORAGE_RELATIVE_ROOT` ou `DOC_ANALYZER_STORAGE_ABSOLUTE_ROOT`).
4. Executar `python main.py` para iniciar o servico.

## 3. Operacao diaria
1. Novos documentos sao colocados na pasta de entrada.
2. A cada processamento concluido, conferir o pacote gerado em `processados/<categoria>/`.
3. Utilizar `feedback.txt` para confirmar ou corrigir a classificacao.
4. Verificar notificacoes no Teams para acompanhar andamento e erros.

## 4. Consumindo os resultados
- `analise.txt` apresenta categoria principal, confianca, resumo, palavras-chave e sugestao de novas categorias.
- O Adaptive Card no Teams mostra os mesmos dados com links para o arquivo gerado.
- `feedback.txt` orienta o revisor a confirmar categoria, sugerir alternativa e marcar trechos relevantes.

## 5. Feedback e aprendizado
1. Preencher o arquivo `feedback.txt` (campos obrigatorios indicados no template).
2. Salvar na pasta de feedback. O sistema processa automaticamente e move para `feedback/processado/`.
3. Entradas aprovadas viram evidencias em `knowledge_sources/<categoria>/feedback_*.txt`.
4. Ajuste quando categorias secundarias aparecem modificando os cortes `DOC_ANALYZER_SECONDARY_STRUCT_THRESHOLD`, `DOC_ANALYZER_SECONDARY_DOC_THRESHOLD` e `DOC_ANALYZER_SECONDARY_STRONG_THRESHOLD` no `.env` (0 a 1).

## 6. Ajuste rapido de caminhos
- `DOC_ANALYZER_STORAGE_MODE=relative`: usa `<repo>/<DOC_ANALYZER_STORAGE_RELATIVE_ROOT>`.
- `DOC_ANALYZER_STORAGE_MODE=absolute`: usa exatamente `DOC_ANALYZER_STORAGE_ABSOLUTE_ROOT`.
- `DOC_ANALYZER_STORAGE_MODE=network`: identico ao absolute, mas pensado para compartilhamentos montados.
- Subpastas (`DOC_ANALYZER_INPUT_SUBDIR`, `DOC_ANALYZER_PROCESSED_SUBDIR`, etc.) aceitam valores absolutos. Quando voce fornecer um caminho absoluto, o sistema usa exatamente o que foi informado.

## 7. Resolucao de problemas
- **Webhook sem resposta**: revisar URLs no `.env` e buscar erros `Falha ao enviar notificacao Teams` em `logs/system.log`.
- **Documento ignorado**: verifique extensao, permissao de leitura e tamanho minimo de texto.
- **Feedback nao aplicado**: campos obrigatorios faltando; o log aponta o motivo.
- **Pastas inexistentes**: confirmar `DOC_ANALYZER_STORAGE_MODE` e se `DOC_ANALYZER_STORAGE_AUTO_CREATE` esta habilitado.
