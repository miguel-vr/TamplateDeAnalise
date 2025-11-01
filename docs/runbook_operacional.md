# Runbook operacional do classificador

Este runbook serve como cola r+�pida para quem precisa operar ou ajustar o pipeline enquanto eu estiver fora. Foquei no que normalmente d+� mais trabalho: configura+�+�o, diret+�rios, credenciais e o papel de cada classe estrat+�gica.

## 1. Preparando o ambiente
- Python 3.10+ instalado (o projeto foi validado com 3.11).
- Crie e ative um virtualenv (`python -m venv .venv && source .venv/bin/activate` no Linux ou `.venv\Scripts\activate` no Windows).
- Instale as depend+�ncias listadas no `requirements.txt` (rodar `pip install -r requirements.txt`). Se algum pacote opcional estiver faltando (ex.: `python-docx`, `PyMuPDF`, `openai`), o c+�digo avisa no log.

## 2. Configurando via `.env`
Toda a configura+�+�o agora mora no `.env`. Renomeie o `/.env.example` e preencha os campos relevantes.

Principais knobs:
- `DOC_ANALYZER_API_KEY`: chave da OpenAI ou da inst+�ncia Azure (se for Azure, a chave tamb+�m pode ir para `DOC_ANALYZER_AZURE_API_KEY`).
- `DOC_ANALYZER_MODEL` e `DOC_ANALYZER_CROSS_MODEL`: modelos prim+�rio e de valida+�+�o cruzada.
- `DOC_ANALYZER_CONFIDENCE_THRESHOLD`: corte m+�nimo de confian+�a (0.0 ��� 1.0).
- `DOC_ANALYZER_POLL_INTERVAL` e `DOC_ANALYZER_FEEDBACK_INTERVAL`: tempo em segundos para varrer as pastas de entrada e feedback.
- `DOC_ANALYZER_PROCESSING_WORKERS`: quantidade de workers paralelos para processar arquivos.
- `DOC_ANALYZER_LOG_FILE` e `DOC_ANALYZER_TEXT_LOG_FILE`: caminhos (relativos ou absolutos) dos logs estruturado e de texto.
- `DOC_ANALYZER_STORAGE_MODE`: define onde as pastas de trabalho vivem. Valores suportados:
  - `relative` (default): cria tudo dentro do reposit+�rio, respeitando `DOC_ANALYZER_STORAGE_RELATIVE_ROOT`.
  - `absolute`: usa o caminho informado em `DOC_ANALYZER_STORAGE_ABSOLUTE_ROOT`.
  - `network`: igual ao `absolute`, mas deixei expl+�cito para lembrar que a pasta vem de compartilhamento de rede.
- `DOC_ANALYZER_STORAGE_AUTO_CREATE`: se `true`, o c+�digo cria a estrutura sozinho; se `false`, ele assume que o caminho j+� existe.
- `DOC_ANALYZER_STORAGE_CREATE_DEFAULT_CATEGORIES`: controla a cria+�+�o inicial das cinco categorias padr+�o.
- `DOC_ANALYZER_STORAGE_SERVICE_USER`, `DOC_ANALYZER_STORAGE_SERVICE_PASSWORD`, `DOC_ANALYZER_STORAGE_SERVICE_DOMAIN`: guarde aqui as credenciais do usu+�rio de servi+�o caso algu+�m precise montar o compartilhamento manualmente (o script n+�o monta sozinho, mas os valores ficam centralizados).
- `DOC_ANALYZER_STORAGE_MOUNT_COMMAND`: campo livre para registrar o comando shell usado para montar o share (ex.: `mount -t cifs ...` no Linux). Bom para documentar a opera+�+�o junto com o .env.
- Webhooks do Teams: `DOC_ANALYZER_TEAMS_WEBHOOK_URL` (relat+�rios) e `DOC_ANALYZER_TEAMS_ACTIVITY_WEBHOOK_URL` (eventos de fila).

> **Dica**: mantive vari+�veis legadas (`OPENAI_API_KEY`, `TEAMS_WEBHOOK_URL`, etc.) por compatibilidade. Se aparecer algum script antigo, ele continua funcionando sem altera+�+�o.

## 3. Ajustando os diret+�rios de trabalho
1. Defina `DOC_ANALYZER_STORAGE_MODE`:
   - Para rede, use `network` e aponte `DOC_ANALYZER_STORAGE_ABSOLUTE_ROOT` para o share j+� montado (ex.: `/mnt/classificador`).
   - Para testes locais, mantenha `relative` e personalize `DOC_ANALYZER_STORAGE_RELATIVE_ROOT` se quiser isolar os diret+�rios.
2. Se precisar montar o share via servi+�o systemd/cron, documente o comando no `DOC_ANALYZER_STORAGE_MOUNT_COMMAND` e configure o mount fora do script (systemd unit, fstab, script de bootstrap, etc.).
3. Caso a opera+�+�o exija usu+�rio espec+�fico, deixe o usu+�rio/senha/dominio populados nas vari+�veis correspondentes. Assim o pessoal de infraestrutura sabe onde procurar.
4. Se `DOC_ANALYZER_STORAGE_AUTO_CREATE=false`, garanta que as pastas `entrada`, `em_processamento`, `processados`, etc. j+� estejam criadas. O log vai avisar e n+�o vai for+�ar a cria+�+�o.

## 4. Subindo o servi+�o
- **Execu+�+�o direta**: `python main.py`. O script j+� carrega o `.env`, configura logging e inicia os watchers.
- **Modo teste tempor+�rio**: `python test_run.py` (roda por 30 segundos; ajuste `CLASSIFIER_TEST_DURATION` se quiser mais tempo).
- **Servi+�o Linux**: crie uma unidade systemd chamando o virtualenv + `python /caminho/main.py`. Adicione os passos de montagem do share antes de iniciar o servi+�o quando o modo for `network`.
- Logs:
  - Estruturados em JSONL: `DOC_ANALYZER_LOG_FILE`.
  - Texto tradicional: `DOC_ANALYZER_TEXT_LOG_FILE`.
  - Eventos pontuais (Teams) dependem dos webhooks configurados.

## 5. Mapa das classes principais
| Classe | Onde vive | Por que importa |
| --- | --- | --- |
| `core.settings.Settings` | `core/settings.py` | Carrega o `.env`, valida tipos e exp+�e tudo tipado para o resto do sistema. |
| `core.processor.DocumentProcessor` | `core/processor.py` | Pipeline completo do documento: extrai texto, chama GPT, aplica heur+�sticas, gera ZIP e atualiza a base. |
| `core.processor._ProcessingTimeline` | `core/processor.py` | Guarda o tempo de cada etapa e emite eventos estruturados para facilitar troubleshooting. |
| `core.gpt_core.GPTCore` | `core/gpt_core.py` | Wrapper das chamadas GPT/Azure, incluindo retries, prompts auxiliares e cross-check. |
| `core.validator.Validator` | `core/validator.py` | Refaz a an+�lise quando a confian+�a n+�o bate o limite e normaliza os campos de sa+�da. |
| `core.taxonomy.TaxonomyRuleEngine` | `core/taxonomy.py` | Ajusta a categoria com base em palavras-chave quando o GPT hesita ou erra por pouco. |
| `core.knowledge_base.KnowledgeBase` | `core/knowledge_base.py` | Persist+�ncia das an+�lises e hist+�rico de feedback (evita retrabalho e alimenta heur+�sticas). |
| `core.notifier.TeamsNotifier` | `core/notifier.py` | Dispara os Adaptive Cards no Teams (status e relat+�rios). |
| `core.watcher.JsonEventLogger` | `core/watcher.py` | Escreve eventos estruturados em JSONL para auditoria. |
| `core.watcher.DirectoryWatcher` | `core/watcher.py` | Loop de polling que dispara callbacks quando aparece arquivo novo. |
| `core.watcher.IntakeWatcher` | `core/watcher.py` | Move arquivos da entrada para processamento e gerencia o executor. |
| `core.watcher.FeedbackWatcher` | `core/watcher.py` | Processa feedback humano e joga os ajustes na base de conhecimento. |

Se algu+�m novo chegar no projeto, essa tabela +� o melhor caminho para entender quem chama quem.

## 6. Checklist r+�pido antes de ir pra produ+�+�o
1. `.env` preenchido e sem valores padr+�o sens+�veis.
2. Share de rede montado (se `storage_mode` = `network` ou `absolute`).
3. Pastas com permiss+�o de escrita para o usu+�rio do servi+�o.
4. Webhooks do Teams validados com um curl simples (opcional, mas evita surpresa).
5. Rodar `python test_run.py` com um arquivo de exemplo para garantir que o fluxo est+� montado.
6. Acompanhar os logs no primeiro ciclo completo para confirmar que os paths foram resolvidos conforme esperado.

Com isso o time consegue operar o classificador sem depender de mim. Qualquer ajuste espec+�fico de neg+�cio pode ser feito mexendo nos mesmos pontos indicados aqui.
