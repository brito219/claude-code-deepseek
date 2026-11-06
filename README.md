# claude-code-deepseek

Uma skill para o **Claude Code** que manda tarefas para a API da **DeepSeek**. O Claude continua sendo o "cérebro" (planeja, decide e edita seus arquivos), e a DeepSeek faz o trabalho pesado e barato: ler repositórios, resumir, gerar rascunhos e revisar.

Você pede em linguagem natural:

> usa o deepseek pra explorar esse repo e me explicar como funciona a autenticação

> manda pro deepseek resumir esse log

> dispara uns deepseek em paralelo pra revisar esses 3 módulos

## Por que usar

- **Gasta menos da sua assinatura do Claude.** A leitura e a exploração pesadas saem da cota do Claude e vão para a DeepSeek, que cobra centavos.
- **Não enche o contexto do Claude.** A resposta completa vai para um arquivo, e o Claude recebe só uma prévia de cerca de 20 linhas e uma linha de custo. Ele abre o arquivo só se precisar.
- **Aproveita o cache da DeepSeek.** Um input que já está em cache custa cerca de 50x menos. A skill monta os prompts para repetir o mesmo prefixo sempre que possível.

## Suporte

Só funciona no **[Claude Code](https://claude.com/claude-code)** (CLI), em Linux ou macOS. Precisa de **Python 3.8+** e não depende de nenhuma biblioteca externa, só da biblioteca padrão. Não foi feita para o app do Claude nem para outras ferramentas.

## Instalação

**1. Clone direto na pasta de skills do Claude Code:**

```bash
git clone https://github.com/brito219/claude-code-deepseek ~/.claude/skills/deepseek
```

**2. Salve sua chave da DeepSeek.** Crie a chave em [platform.deepseek.com](https://platform.deepseek.com) → API keys e coloque uns dólares de crédito.

Rode este comando **num terminal normal**, fora do Claude Code, para a chave não ficar salva na conversa. Ele pede a chave sem mostrar o que você digita, e assim ela também não fica gravada no histórico do terminal. Rode, cole a chave e aperte Enter:

```bash
mkdir -p ~/.config/deepseek && chmod 700 ~/.config/deepseek && read -rsp 'DeepSeek API key: ' K && printf '%s' "$K" > ~/.config/deepseek/api_key && unset K && chmod 600 ~/.config/deepseek/api_key && echo ' ok'
```

A variável de ambiente `DEEPSEEK_API_KEY` também funciona.

**3. Teste:**

```bash
python3 ~/.claude/skills/deepseek/ds.py --balance
```

Se aparecer algo como `USD 5.00 ... available`, está funcionando. Abra o Claude Code e a skill `deepseek` já aparece. É só pedir.

## Modos

| Quero... | Comando (o Claude escolhe sozinho) | Modelo padrão |
|---|---|---|
| Fazer uma pergunta rápida ou gerar algo | `ds.py "tarefa"` | Flash |
| Perguntar sobre arquivos específicos | `ds.py -f a.py -f b.py "tarefa"` | Flash |
| Fazer várias perguntas sobre o mesmo material | `ds.py -s nome -f ... "pergunta"`, depois `ds.py -s nome "outra"` | Flash |
| Deixar a DeepSeek explorar um repo sozinha | `ds.py --agent /caminho/repo "tarefa"` | **Pro** |
| Forçar o outro modelo | `--pro` ou `--flash` | |
| Ver saldo e gastos | `ds.py --balance`, `ds.py --stats [dias]` | |

**Modo agente:** a DeepSeek ganha três ferramentas **só de leitura** (listar pastas, ler arquivos e buscar texto), todas presas à pasta que você indicar. Ela nunca altera arquivos nem roda comandos. Arquivos com cara de segredo (`.env*`, `*.pem`, `*.key`, `id_rsa*`, credenciais...) ficam bloqueados.

**Subagentes:** para tarefas independentes, o Claude dispara várias chamadas em segundo plano, cada uma gravando no seu próprio arquivo. Não usamos os subagentes nativos do Claude para isso, porque eles gastariam a cota da assinatura, que é justamente o que a skill tenta economizar.

## Como a skill aproveita o cache

A DeepSeek guarda em cache automaticamente o **começo** (prefixo) das requisições. Se o começo de uma nova requisição for idêntico ao de uma anterior, essa parte sai quase de graça. A skill segue estas regras:

1. **Instruções do sistema fixas.** São o mesmo texto em toda chamada, sem data e sem nada que mude.
2. **Arquivos sempre em ordem alfabética, antes da pergunta.** O mesmo conjunto de arquivos gera sempre o mesmo prefixo, e só a pergunta no final muda.
3. **Sessões (`-s`).** As perguntas seguintes reenviam a conversa inteira, que já está em cache, e só a nova pergunta paga preço cheio.
4. **Aquecer antes de paralelizar.** Primeiro roda uma chamada com os arquivos em comum e só depois dispara as paralelas. Se todas saírem juntas, o cache ainda não está pronto e todas pagam preço cheio.
5. **Um modelo por material.** O cache do Flash e o do Pro são separados.

## Preços e economia

Preços oficiais por 1 milhão de tokens ([fonte](https://api-docs.deepseek.com/quick_start/pricing), consultada em nov/2026):

| Modelo | Input em cache | Input sem cache | Output |
|---|---|---|---|
| `deepseek-flash` | US$ 0,003 – 0,006 | US$ 0,15 – 0,30 | US$ 0,60 – 1,20 |
| `deepseek-v4-pro` | US$ 0,022 – 0,044 | US$ 0,66 – 1,32 | US$ 1,98 – 3,96 |

O menor valor de cada faixa vale **fora do horário de pico**, que custa metade. O pico é de segunda a sexta, das 01h às 04h e das 06h às 10h UTC, ou seja, **das 22h à 01h e das 03h às 07h no horário de Brasília**. Durante o dia no Brasil, quase tudo cai fora do pico. A linha de custo de cada chamada avisa quando é `PEAK`.

Os preços mudam. O custo de cada chamada é calculado pela tabela `PRICES` no topo do `ds.py`, então atualize essa tabela se a DeepSeek mudar os valores.

**Estimativa** (não é medição) de uma exploração de repo com 15 passos, cerca de 600 mil tokens de input (90% em cache) e 10 mil de output, fora do pico:

| | Por exploração | Quantas cabem em US$ 5 |
|---|---|---|
| Flash | ~US$ 0,02 | ~300 |
| Pro | ~US$ 0,07 | ~70 |

## Testes reais

Rodei os testes fora do pico, logo depois de instalar. As 6 chamadas custaram **US$ 0,012** no total.

| Teste | Resultado |
|---|---|
| Mesmo arquivo (~5 mil tokens) perguntado 3x seguidas | cache hit **0% → 96% → 96%** |
| Sessão: pergunta + continuação | 96% e 85% de cache hit; a continuação custou **US$ 0,0004** |
| Modo agente (Pro) explorando uma pasta com 5 skills | 4 passos, ferramentas em paralelo, resposta correta, **US$ 0,005** |

Cada chamada termina com uma linha assim:

```
[ds] deepseek-flash | off-peak | in 5053 (cache hit 4864 = 96%) | out 212 | $0.00017
```

Todas as chamadas ficam registradas em `~/.cache/deepseek/ledger.jsonl`, e `ds.py --stats` mostra o resumo.

## Avisos

- **Privacidade:** tudo o que você anexa ou que o agente lê vai para os servidores da DeepSeek, na China. Antes de usar com código de trabalho ou de cliente, confirme se pode.
- **Qualidade:** trate a resposta da DeepSeek como rascunho. O Claude revisa e faz as alterações nos seus arquivos.
- Projeto pessoal, sem relação com a Anthropic nem com a DeepSeek.

## Arquivos

- `SKILL.md`: instruções que o Claude Code lê, incluindo quando usar a skill e as regras de cache.
- `ds.py`: o script que chama a API.
- Seus dados ficam em `~/.cache/deepseek/` (`out/` com as respostas, `sessions/` e `ledger.jsonl`).

## Licença

MIT
