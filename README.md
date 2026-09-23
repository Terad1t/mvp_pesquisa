# MVP — Automação de Pesquisas Veritá

Princípio: **parser tenta primeiro, IA faz fallback, código decide.** O objetivo
é extrair os dados numéricos das tabelas — posição, candidato, partido, votos e
percentuais — e não resumir o texto do PDF.

## Instalação (uv)

```bash
uv sync                   # cria .venv e resolve tudo a partir do pyproject.toml
cp .env.example .env      # preencha GEMINI_API_KEY
```

Não é preciso ativar a venv: `uv run` já executa dentro dela.

## Uso

```bash
# Fases 2–4 sem gastar chamada de API (fixture com Governador+Senador+Presidente)
uv run main.py --json exemplo_completo.json

# Fase 1 completa, com PDF real — extrai os TRÊS cargos numa chamada só
uv run main.py PDFs/pesquisa.pdf

# Processar só um cargo, mesmo que os três tenham sido extraídos
uv run main.py PDFs/pesquisa.pdf --cargo GOVERNADOR

# Processar todos os PDFs diretamente dentro de uma pasta
uv run main.py --pasta PDFs --cargo GOVERNADOR

# Executar somente o protótipo do parser, sem Gemini
uv run python table_extractor.py PDFs/pesquisa.pdf

# Executar o parser para Presidente
uv run python table_extractor.py PDFs/pesquisa.pdf --cargo PRESIDENTE

# Forçar uma página específica durante a investigação
uv run python table_extractor.py PDFs/pesquisa.pdf --cargo GOVERNADOR --pagina 7

# Executar os testes automatizados
uv run pytest -q
```

O `main.py` tenta o parser antes do Gemini. Com `--cargo GOVERNADOR` ou
`--cargo PRESIDENTE`, uma extração determinística bem-sucedida evita a chamada
à API. Sem filtro, o parser resolve Governador e Presidente, mas o Gemini ainda
é chamado para completar o Senado; os blocos resolvidos pelo parser prevalecem
sobre os blocos equivalentes da IA.

Com `--pasta`, cada PDF é processado individualmente. Os JSONs são separados
por estado em `saidas/{ESTADO}/` e recebem o nome do PDF, por exemplo
`saidas/PARANÁ/pesquisa.pdf.json`. Se já existir um arquivo com o mesmo nome,
o programa cria uma variante como `_1.json` em vez de sobrescrever a saída.

Depois de rodar com um PDF real (não com `--json`), o arquivo é movido
automaticamente de `PDFs/` para uma subpasta, de acordo com o resultado:

| Resultado | Destino |
|---|---|
| Todos os cargos calculados (ou pendentes por regra faltando, como Senado) | `PDFs/analisados/` |
| Algum cargo não fechou a validação, ou cargo esperado veio faltando | `PDFs/bloqueados/` — revisão humana |
| Nem chegou a extrair (arquivo ilegível, JSON malformado) | `PDFs/falhos/` |
| Gemini sobrecarregado / limite de taxa | fica em `PDFs/` — o próximo run tenta de novo sozinho |

Para adicionar dependências, use `uv add <pacote>` — ele atualiza o
`pyproject.toml` e o `uv.lock` juntos. Editar o `pyproject.toml` à mão e
esquecer o lock é a forma mais comum de os dois saírem de sincronia.

Se algum dia precisar de um `requirements.txt` (deploy antigo, CI que não tem
uv), gere a partir do lock em vez de manter os dois à mão:

```bash
uv export --no-dev --format requirements-txt > requirements.txt
```

Exit codes: `0` sucesso (inclui Senado pendente), `1` bloqueado por validação
ou cargo faltando, `2` falha de extração (erro definitivo — precisa correção),
`3` falha transitória (Gemini sobrecarregado ou limite de taxa — tente de
novo mais tarde).

## Arquivos

| Arquivo | Responsabilidade |
|---|---|
| `extractor.py` | Único módulo que fala com a rede. PDF → `ResultadoExtracao` (um estado, até 3 cargos), UMA chamada ao Gemini por PDF |
| `table_extractor.py` | Parser determinístico experimental com PyMuPDF. Localiza e reconstrói tabelas textuais de Governador e Presidente |
| `tests/test_table_extractor.py` | Testes do parser usando `PDFs/pesquisa.pdf` como fixture real |
| `schemas.py` | Contratos. Separa o que o Gemini pode dizer do que o Python produz |
| `calculator.py` | Só matemática, funções puras. Registro de regras por cargo (`REGRAS`) |
| `validator.py` | Barreira de segurança. Devolve ocorrências, não booleano. Sabe que Senado não segue a base 100% |
| `main.py` | Orquestração, saída no terminal, JSON por estado, organização dos PDFs |
| `RELATORIO_PENDENCIAS.md` | Relatório técnico do que foi implementado e do que ainda falta |
| `pyproject.toml` | Dependências declaradas (o que você pediu) |
| `uv.lock` | Versões exatas resolvidas (o que você recebeu) — **commitar** |

## Fluxo atual

### Fluxo principal

```text
PDF
 ↓
Parser determinístico
 ├── cargo solicitado resolvido → CargoExtraido
 └── tabela ausente/insegura → Gemini
          ↓
        CargoExtraido
 ↓
Validator
 ↓
Calculator
 ↓
PesquisaFinal
 ↓
saidas/{ESTADO}/pesquisa.json
```

O Gemini recebe o PDF inteiro em uma chamada e tenta extrair Governador,
Senador e Presidente. O Python valida cada cargo separadamente. Governador e
Presidente podem ser calculados; Senado é validado, mas fica pendente porque
a regra para dois votos por eleitor ainda não foi definida.

### Parser experimental

```text
PDF
 ↓
PyMuPDF
 ↓
localização da pergunta estimulada
 ↓
agrupamento das palavras por coordenada Y
 ↓
nome, partido, votos e percentuais estruturados
```

O parser já foi testado contra `PDFs/pesquisa.pdf` e extraiu corretamente:

- Governador: 8 candidatos da página 7;
- Presidente: 12 candidatos da página 14;
- nomes partidos em mais de uma linha, como `DOUTOR ALEXANDRE SALOMÃO` e
  `VETERINÁRIO WILSON GRASSI`.

Cada linha extraída contém `posicao`, `nome`, `partido`, `votos`, `porcentual`,
`porcentagem_valida` e `porcentagem_acumulada`.

O parser rejeita explicitamente páginas que não correspondem ao cargo ou que
não permitem reconstruir linhas com segurança. Nesses casos, o fluxo chama o
Gemini como fallback. No modo completo, o parser ainda não cobre Senado, então
o Gemini permanece necessário para esse cargo.

## Saída organizada por estado

Cada PDF processado com sucesso (total ou parcial) gera
`saidas/{ESTADO}/pesquisa.json`, com os cargos calculados e um marcador
`"completo": false` + `"cargos_faltando"` quando algum cargo não veio na
extração. Essa é a organização que o robô de integração com o PesquisaPRO vai
consumir mais adiante — o site também separa a apresentação por estado.

## Estado atual

- O parser determinístico funciona isoladamente para Governador e Presidente
  em tabelas textuais com o layout observado no PDF real.
- O parser e o pipeline híbrido possuem 13 testes automatizados, cobrindo
  extração, posições, votos, percentuais, nomes quebrados, fallback e falhas esperadas.
- PyMuPDF está declarado em `pyproject.toml`, `uv.lock` e `requirements.txt`.
- Governador e Presidente estão implementados no cálculo principal com a mesma
  regra: voto único e base de 100%.
- Um PDF gera UMA chamada ao Gemini, extraindo os três cargos de uma vez —
  evita 3x o custo e 3x a chance de bater em limite de taxa para o mesmo
  documento.
- Cada cargo é validado e calculado de forma independente: um erro em Senado
  não trava Governador nem Presidente.
- Senado é extraído e validado estruturalmente, mas o cálculo de "Outros"
  fica pendente (`NotImplementedError` proposital) até a fórmula de dois
  votos por eleitor ser definida. Isso não bloqueia o PDF.
- `CandidatoExtraido` aceita `posicao` e `votos` opcionais, e `CargoExtraido`
  preserva a identificação textual da `pergunta`.
- O parser já está integrado ao `main.py`: evita o Gemini para Governador e
  Presidente quando um cargo é solicitado, e tem precedência sobre esses
  cargos no modo completo.
- Sem geração de arte, sem integração com o PesquisaPRO, sem browser. Ainda
  não sabemos se o PesquisaPRO tem API — isso é o próximo desconhecido antes
  da Fase 6.

## O que falta

1. Estudar as tabelas do Senado, incluindo a consolidação das duas perguntas,
   sem aplicar ainda a fórmula de cargo não definida.
2. Generalizar a identificação de cenários, perguntas e cargos para outros
  layouts e institutos.
3. Criar suporte determinístico ao Senado e decidir como consolidar as duas
  perguntas.
4. Ampliar o fallback para preservar diagnósticos de confiança e tabelas
  parcialmente reconstruídas.
5. Implementar geração das artes e integração com o PesquisaPRO.

O fallback já funciona: parser confiável usa dados estruturados; parser incapaz
   de reconstruir a tabela chama o Gemini.

## Testes

```bash
uv run pytest -q
```

Resultado atual: 15 testes passando, cobrindo parser, contrato com os schemas,
fallback Gemini, validação de posições e processamento em lote. Os testes de extração usam o PDF real
local; portanto, dependem da presença de `PDFs/pesquisa.pdf`.