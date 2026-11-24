# Auditoria offline: resolução de imagens openai-codex

## Escopo e método

Caracterização do código existente em `43e566f77eaf01293086eb7cb99a21e240d60634`, sem alterar runtime, modelos de conversa/execução, configs reais ou credenciais. Testes no worktree exclusivo `test/codex-image-contract`. Não houve chamada de geração externa, instalação, restart ou publicação. A rede nos novos testes é bloqueada por fixture; fronteiras de credenciais e cliente externo são simuladas. GitHub é utilizado apenas para entrega do PR.

## Configuração recomendada (não aplicada nesta auditoria)

Adicionar/mesclar em cada config do profile alvo, preservando outras chaves:

```yaml
image_gen:
  provider: openai-codex
  openai-codex:
    model: gpt-image-2-medium
```

`image_gen.model: gpt-image-2-medium` também é aceito, mas a subseção acima tem prioridade e não conflita com um modelo FAL legado. Não alterar `model`, `delegation.model` ou modelos conversacionais. Verificar `OPENAI_IMAGE_MODEL` no ambiente de cada processo: um tier válido nessa variável sobrepõe os dois campos YAML; não imprimir o ambiente completo.

## Schema e precedência comprovados no código

- `image_generate`: somente `prompt` obrigatório e `aspect_ratio` opcional (`landscape`, `square`, `portrait`). Provider/model não são selecionáveis pelo agente via schema.
- `image_gen` e `image_gen.openai-codex` devem ser mappings YAML; provider e modelo devem ser strings. Um provider com shape incompatível é tratado como ausente (não produz erro de validação explícito).
- Tier: `OPENAI_IMAGE_MODEL` válido > `image_gen.openai-codex.model` válido > `image_gen.model` válido > `gpt-image-2-medium`.
- Tiers aceitos pelo plugin nesta revisão: `gpt-image-2-low`, `gpt-image-2-medium`, `gpt-image-2-high`. `gpt-image-2` sozinho e modelos conversacionais não são tiers válidos e são ignorados.
- O plugin envia `model=gpt-5.4` como host interno de Responses e `tools[].model=gpt-image-2` como modelo de imagem; não deriva o host de `model.default` nem altera a configuração conversacional.
- Tamanhos enviados: landscape 1536x1024, square 1024x1024, portrait 1024x1536. Portanto a descrição genérica do schema (16:9) não garante saída 16:9 nesse provider; consumidores devem medir/adaptar proporção sem supor dimensões.

## Disponibilidade não é roteamento nem geração

1. `check_image_generation_requirements()` aceita FAL pronto ou qualquer plugin cujo `is_available()` retorne true. Isso expõe a ferramenta, mas não seleciona esse plugin.
2. Provider ausente ou `fal`: `_dispatch_to_plugin_provider()` retorna None e o handler segue para FAL, mesmo com openai-codex disponível. Não há autodetecção que selecione Codex.
3. Provider explícito `openai-codex` registrado: chama `generate()` desse plugin. Erro de autenticação/SDK/API é devolvido; não tenta FAL depois disso.
4. Provider explícito não registrado: tenta refresh de discovery uma vez, depois `provider_not_registered`, sem fallback FAL. Exceção na primeira importação/discovery/consulta é diferente: o `except` retorna None, permitindo FAL; atenção ao diagnóstico de logs.
5. `is_available()` do Codex verifica somente token retornado pelo reader e importabilidade de `openai`. Não testa assinatura/entitlement remoto, endpoint, headers, aceitação do host/modelo/tools nem sucesso de streaming. Mesmo disponível, a chamada pode retornar `api_error`.

## Autenticação e SDK

O plugin não usa `OPENAI_API_KEY`; delega a `_read_codex_access_token()` em `agent.auxiliary_client`. Essa rotina consulta pool `openai-codex` (retorna antecipadamente se houver token selecionado) e depois tokens do auth store através de `hermes_cli.auth._read_codex_tokens()`. No fallback do auth store rejeita JWT expirado quando consegue interpretar `exp`; token não-JWT ou erro de decode é aceito localmente, não validado remotamente. Não é uma garantia uniforme de checagem de expiração para tokens retornados pelo pool.

O cliente é `openai.OpenAI` com base `https://chatgpt.com/backend-api/codex` e headers produzidos por `_codex_cloudflare_headers(token)`. Requer SDK importável, `responses.stream()`, context manager, eventos de imagem e `get_final_response()`. O ambiente de teste já tinha openai 2.24.0: nenhuma instalação. Isso não estabelece uma versão mínima oficialmente suportada nem compatibilidade real do servidor. Falha no construtor também aparece como `auth_required`, não necessariamente credencial inválida.

Não foram lidos tokens reais nem executado o reader real nesta auditoria. A autenticação de conversa da sessão atual não prova autenticação do plugin de imagens de outro profile. O setup schema exibe badge `free`; isso é metadado do código, não evidência de gratuidade, disponibilidade no plano ou autorização para chamada paga.

## Resultados e reprodução

Dependências existentes pré-verificadas (`pytest`, `pytest_split`, `xdist`, `openai`). O wrapper foi inspecionado antes da execução: pode instalar pytest-split se ausente; o pré-check confirmou presença, portanto esse ramo não foi executado.

Comando a partir do worktree (venv existente acessível via symlink local não versionado `.venv`):

```sh
scripts/run_tests.sh tests/plugins/image_gen/test_codex_resolution_contract.py tests/plugins/image_gen/test_openai_codex_provider.py tests/tools/test_image_generation_plugin_dispatch.py tests/agent/test_image_gen_registry.py -q --basetemp="$PWD/.test-tmp"
```

PASS: 45 testes em 11,72s; inclui 14 novos testes de caracterização e 31 existentes. Não há mudança de produção: não se alega ciclo RED/GREEN de correção.

PASS nos testes novos: plugin disponível com provider ausente não se auto-seleciona; seleção explícita chega ao plugin real e devolve auth_required sem FAL; provider inexistente gera erro; shapes incompatíveis são tratados como ausentes; precedência e fallbacks de tier; configs e diretórios de cache distintos ao alternar HERMES_HOME A/B/A; disponibilidade true não impede api_error.

O isolamento testado é de resolução de config e caminho de saída com HERMES_HOME temporário e Path.home() isolado. Não prova isolamento de credenciais reais, processos gateway compartilhados ou ausência de herança de variáveis de ambiente entre processos. Os bytes do teste de cache são fixture sintética, não imagem gerada externamente.

PENDING por desenho: credenciais/entitlement reais e chamada remota. PENDING: nova sessão operacional de cada profile após aplicar config (atividade do cartão dependente). Suíte global não executada: a autorização deste cartão restringe a testes offline com mocks/fixtures desta integração; não afirmar regressão global verde. Preview Cloudflare não aplicável: PR somente testes/documentação do Hermes, não altera portal/UI.

## Referências de código

- `tools/image_generation_tool.py:847-886`, `:930-1086` — disponibilidade, schema, dispatch/fallback.
- `plugins/image_gen/openai-codex/__init__.py:88-161`, `:164-214`, `:233-240`, `:269-368` — config, auth/SDK, request e resultado.
- `agent/auxiliary_client.py:1333-1373` — reader canônico de token.
- `tests/plugins/image_gen/test_codex_resolution_contract.py` — novas fixtures offline.

Recomendação: aplicar somente provider/tier compatíveis no cartão responsável; validar resolução numa sessão nova por profile. Relatar separadamente ferramenta exposta, provider selecionado, SDK/credenciais localmente disponíveis e chamada real (quando autorizada). Não usar sucesso de mock como evidência de imagem operacional.
