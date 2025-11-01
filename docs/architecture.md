# Arquitetura e Fluxo Operacional

## 1. Visao geral
O Classifica Document Pipeline combina uma esteira assincrona de processamento de arquivos com varias camadas de validacao (LLM, heuristicas, conhecimento local e feedback humano). O fluxo roda continuamente, notifica via Microsoft Teams e reforca a base de conhecimento a cada ciclo.

## 2. Modelo C4 - Contexto
```mermaid
C4Context
    title Contexto do Classifica Document Pipeline
    Person(user, "Usuario de Negocio", "Arrasta documentos e consome relatorios gerados.")
    Person(analyst, "Analista de Revisao", "Avalia feedbacks e monitora notificacoes.")
    System(pipeline, "Classifica Document Pipeline")
    System_Ext(teams, "Microsoft Teams", "Recebe notificacoes via webhook.")
    System_Ext(openai, "Servico GPT/Azure OpenAI", "Fornece inferencia LLM.")
    user -> pipeline : Envia documentos\n(consome artefatos)
    analyst -> pipeline : Envia feedback
    pipeline -> teams : Adaptive Card\n+ avisos de atividade
    pipeline -> openai : Requisicoes\nchat completion
```

## 3. Modelo C4 - Containers
```mermaid
C4Container
    title Containers principais
    System_Boundary(pipeline, "Classifica Document Pipeline") {
        Container(cli, "CLI / main.py", "Python", "Processo long-running que orquestra watchers e componentes.")
        Container(watchers, "Watchers", "Python threads", "Monitoram entrada e feedback.")
        Container(processor, "DocumentProcessor", "Python", "Extrai texto, chama LLM, aplica heuristicas e gera artefatos.")
        Container(kb, "KnowledgeBase", "JSON + memoria", "Persistencia de conhecimento estruturado, documentos reais e feedbacks.")
        Container(notifier, "TeamsNotifier", "HTTP Webhook", "Publica mensagens no Teams.")
    }
    System_Ext(fs, "Sistema de arquivos / rede")
    System_Ext(openai, "Servico GPT/Azure OpenAI")
    System_Ext(teams, "Microsoft Teams")

    cli -> watchers : Configuracao / criacao
    watchers -> processor : Submete arquivos para analise
    processor -> kb : Atualiza conhecimento e categorias
    processor -> notifier : Envia Adaptive Card
    watchers -> notifier : Avisos de atividade (recebido/enfileirado)
    processor -> fs : Le/escreve documentos e artefatos
    processor -> openai : Requisicoes de inferencia
    notifier -> teams : Webhook HTTP
```

## 4. Componentes internos

| Componente | Funcao principal | Entradas | Saidas |
|------------|------------------|----------|--------|
| `IntakeWatcher` | Detecta novos arquivos, move para area de processamento e aciona o pipeline | Pasta de entrada | Fila interna, log estruturado, aviso Teams (recebido) |
| `DocumentProcessor` | Extrai texto, chama GPT, aplica validacoes (cross-LLM, heuristica, conhecimento), gera pacote zip | Documento, metadados, conhecimento local | Artefatos (`analise.txt`, `feedback.txt`), Adaptive Card, aviso Teams (concluido) |
| `KnowledgeBase` | Persiste conhecimento estruturado e tokens de documentos reais | Resultados validados, feedback | Similaridade, perfis de categoria, ajuste de confianca |
| `TeamsNotifier` | Canaliza notificacoes para webhooks distintos | Payloads de monitoramento | Mensagens no Teams |
| `FeedbackWatcher` | Converte feedback em eventos de aprendizado | Arquivos em `feedback/` | Atualizacao de categorias, ajuste de palavras-chave, arquivamento por categoria |

### Camadas de inteligencia na analise
1. **LLM primario**: classifica e gera justificativas.
2. **Cross-validation**: audita o resultado primario.
3. **I3**: agrega insight, impacto e inferencia para explicabilidade.
4. **Taxonomia heuristica**: reforca categorias com base em palavras-chave.
5. **Conhecimento local**: compara com entradas anteriores (`knowledge.json`).
6. **Conhecimento documental**: tokens agregados de arquivos reais (`knowledge_sources/<categoria>`).
7. **Feedback humano**: ajusta confianca, categorias e vocabulario.

## 5. Fluxo operacional detalhado
1. **Recebimento**: arquivo depositado na pasta de entrada. `IntakeWatcher` move para a fila, registra evento `processing_enqueued` e envia aviso de atividade.
2. **Processamento**: `DocumentProcessor` extrai texto, envia ao GPT, aplica validacoes e integra heuristicas/taxonomia/feedback.
3. **Geracao de artefatos**: cria pacote `.zip` (documento original + `analise.txt` + `feedback.txt`).
4. **Notificacoes**: Teams recebe aviso de atividade e Adaptive Card com resumo completo.
5. **Persistencia**: `KnowledgeBase` armazena resultado, tokens e estatisticas de feedback por categoria.
6. **Feedback**: revisores preenchem `feedback.txt` ou usam `tools/submit_feedback.py`. `FeedbackWatcher` aplica ajustes imediatos, arquiva trechos aprovados e recalibra confianca.

## 6. Tecnologias e integracoes
- **Python 3**: linguagem do pipeline.
- **openai / azure-openai**: SDK para chamadas GPT.
- **PyMuPDF (fitz) e python-docx**: extraem texto de PDF/DOCX (opcionais).
- **Microsoft Teams**: recebe notificacoes via webhook.
- **Sistema de arquivos ou share de rede**: armazenamento de entrada/saida.
