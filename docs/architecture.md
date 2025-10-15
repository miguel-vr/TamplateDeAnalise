# Arquitetura e Fluxo Operacional

## 1. Visão geral
O Classifica Document Pipeline combina uma esteira assíncrona de processamento de arquivos com múltiplas camadas de validação (LLM, heurísticas, conhecimento local e feedback humano). O sistema opera continuamente, notificando usuários via Microsoft Teams e enriquecendo a base de conhecimento a cada iteração.

## 2. Modelo C4 – Contexto
```mermaid
C4Context
    title Contexto do Classifica Document Pipeline
    Person(user, "Usuário de Negócio", "Arrasta documentos e consome os relatórios.")
    Person(analyst, "Analista de Revisão", "Avalia feedbacks e monitora notificações.")
    System(pipeline, "Classifica Document Pipeline")
    System_Ext(teams, "Microsoft Teams", "Recebe notificações (webhooks).")
    System_Ext(openai, "Serviço GPT/Azure OpenAI", "Fornece inferência LLM.")
    user -> pipeline : Envia documentos\n(consome artefatos)
    analyst -> pipeline : Envia feedback
    pipeline -> teams : Adaptive Card\n+ avisos de atividade
    pipeline -> openai : Requisições\nchat completion
```

## 3. Modelo C4 – Contêineres
```mermaid
C4Container
    title Contêineres principais
    System_Boundary(pipeline, "Classifica Document Pipeline") {
        Container(cli, "CLI / main.py", "Python", "Processo long-running que orquestra watchers e componentes.")
        Container(watchers, "Watchers", "Python threads", "Monitora entrada e feedback.")
        Container(processor, "DocumentProcessor", "Python", "Executa extração de texto, LLM, heurísticas e geração de artefatos.")
        Container(kb, "KnowledgeBase", "JSON + memória", "Persistência de conhecimento estruturado, documentos reais e feedbacks.")
        Container(notifier, "TeamsNotifier", "HTTP Webhook", "Publica mensagens no Teams.")
    }
    System_Ext(fs, "Sistema de arquivos / rede") 
    System_Ext(openai, "Serviço GPT/Azure OpenAI")
    System_Ext(teams, "Microsoft Teams")

    cli -> watchers : Configuração / criação
    watchers -> processor : Submete arquivos para análise
    processor -> kb : Atualiza conhecimento e categorias
    processor -> notifier : Envia Adaptive Card
    watchers -> notifier : Envia avisos de atividade (recebido/enfileirado)
    processor -> fs : Lê/escreve documentos e artefatos
    processor -> openai : Requisições de inferência
    notifier -> teams : Webhook HTTP
```

## 4. Componentes internos (resumo)

| Componente | Função principal | Entradas | Saídas |
|------------|------------------|----------|--------|
| `IntakeWatcher` | Detecta novos arquivos, move para área de processamento e aciona o pipeline | Pasta de entrada | Fila interna, log estruturado, aviso Teams (recebido) |
| `DocumentProcessor` | Extrai texto, chama GPT, aplica validações (cross-LMM, heurística, conhecimento, feedback), gera pacote zip | Documento, metadados, conhecimento local | Artefatos (`analise.txt`, `feedback.txt`), Adaptive Card, aviso Teams (concluído) |
| `KnowledgeBase` | Persiste conhecimento estruturado, mantém tokens de documentos reais, consolida feedback humano | Resultados validados, feedback richer | Similaridade, perfis de categoria, ajustes de confiança |
| `TeamsNotifier` | Canaliza notificações para webhooks distintos | Payloads de monitoramento | Mensagens no Teams |
| `FeedbackWatcher` | Converte feedback em eventos de aprendizado | Arquivos em `feedback/` | Atualização de categorias, ajuste de palavras-chave, arquivamento por categoria |

### Camadas de inteligência na análise
1. **LLM primário**: classifica e justifica.
2. **Cross-validation**: auditoria independente do resultado primário.
3. **I3**: agrega insight, impacto e inferência para explicabilidade.
4. **Taxonomia heurística**: reforça categorias com base em palavras-chave.
5. **Conhecimento local**: compara com entradas anteriores (`knowledge.json`).
6. **Conhecimento documental**: tokens agregados de arquivos reais (`knowledge_sources/<categoria>`).
7. **Feedback humano**: ajusta confiança, categorias e vocabulário mediante histórico.

## 5. Fluxo operacional detalhado
1. **Recebimento** – arquivo depositado na pasta de entrada. `IntakeWatcher` move para a fila, registra evento `processing_enqueued` e notifica o Teams (webhook de atividade).
2. **Processamento** – `DocumentProcessor` extrai texto, envia ao GPT, aplica validações e integra heuristic/taxonomia/feedback.
3. **Geração de artefatos** – cria pacote `.zip` (original + `analise.txt` + `feedback.txt`).
4. **Notificações** – 
   - Teams (atividade): “Processamento iniciado” e “Processamento concluído”.
   - Teams (Adaptive Card): resumo completo, incluindo linha do tempo, matches e palavras-chave.
5. **Persistência** – 
   - `KnowledgeBase` recebe nova entrada (tokens, justificativa, categoria).
   - Atualiza perfis de categorias com termos de arquivos reais e estatísticas de feedback.
6. **Feedback** � usu�rios preenchem `feedback.txt` (com `confirmar_categoria_principal`, `trecho_evidencia_<slug>` e `acao_incluir_conhecimento_<slug>`) ou utilizam `tools/submit_feedback.py`. O `FeedbackWatcher` aplica ajustes imediatos, registra trechos aprovados em `knowledge_sources/<categoria>/feedback_*.txt` e atualiza confian�a/vocabul�rio.

## 6. Tecnologias e integrações
- **Python 3**: execução do pipeline, watchers e integrações.
- **openai / azure-openai**: clientes oficiais para chamadas GPT/Chat Completions.
- **PyMuPDF (fitz), python-docx**: extração de texto de PDF e DOCX (opcionais).
- **Microsoft Teams Webhooks**: Adaptive Card para resultados e cartão simples para eventos de fila/processamento.
- **Mermaid (documentação)**: diagramas C4 renderizados nos arquivos Markdown.

## 7. Pontos de extensibilidade
- **Novos canais de alerta**: `TeamsNotifier` centraliza o envio; basta implementar métodos adicionais para outros webhooks ou integrações (ex.: Slack, e-mail).
- **Novos formatos de arquivo**: estender `SUPPORTED_EXTENSIONS` e implementar `_read_<ext>()` em `DocumentProcessor`.
- **Persistência alternativa**: `KnowledgeBase` hoje usa arquivo JSON; pode ser adaptada para bancos NoSQL/SQL mantendo a interface pública.
- **Ciência de dados**: os logs estruturados (`logs/activity.jsonl`) podem alimentar dashboards ou pipelines de monitoramento.

