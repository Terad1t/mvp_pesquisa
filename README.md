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

# Fase 1 completa, com PDF real — parser tenta os três cargos antes do Gemini
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

# Abrir o frontend desktop CustomTkinter
uv run python desktop_app.py
```

O `main.py` tenta o parser antes do Gemini. Com `--cargo GOVERNADOR`,
`--cargo PRESIDENTE` ou `--cargo SENADOR`, uma extração determinística
bem-sucedida evita a chamada à API. Sem filtro, o parser tenta os três cargos;
o Gemini só é chamado se alguma tabela não puder ser reconstruída.

O frontend desktop usa as mesmas funções do backend e executa todo o
processamento em uma thread separada da interface. Assim, retries longos do
Gemini não travam a janela. Ele cobre seleção e execução, consulta dos dados
do JSON, exportação para XLSX, fila de bloqueados, histórico por estado e
configurações básicas.

Com `--pasta`, cada PDF é processado individualmente. Os JSONs são separados
por estado em `saidas/{ESTADO}/` e recebem o nome do PDF, por exemplo
`saidas/PARANÁ/pesquisa.pdf.json`. Se já existir um arquivo com o mesmo nome,
o programa cria uma variante como `_1.json` em vez de sobrescrever a saída.

Depois de rodar com um PDF real (não com `--json`), o arquivo é movido
automaticamente de `PDFs/` para uma subpasta, de acordo com o resultado:

| Resultado | Destino |
|---|---|
| Todos os cargos calculados e validados | `PDFs/analisados/` |
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

Exit codes: `0` sucesso, `1` bloqueado por validação
ou cargo faltando, `2` falha de extração (erro definitivo — precisa correção),
`3` falha transitória (Gemini sobrecarregado ou limite de taxa — tente de
novo mais tarde).

## Arquivos

| Arquivo | Responsabilidade |
|---|---|
| `extractor.py` | Único módulo que fala com a rede. PDF → `ResultadoExtracao` quando o parser precisa do Gemini |
| `table_extractor.py` | Parser determinístico com PyMuPDF. Localiza e reconstrói tabelas textuais dos três cargos do PDF de referência |
| `tests/test_table_extractor.py` | Testes do parser usando `PDFs/pesquisa.pdf` como fixture real |
| `schemas.py` | Contratos. Separa o que o Gemini pode dizer do que o Python produz |
| `calculator.py` | Só matemática, funções puras. Registro de regras por cargo (`REGRAS`) |
| `validator.py` | Barreira de segurança. Devolve ocorrências, não booleano. Sabe que Senado não segue a base 100% |
| `main.py` | Orquestração, saída no terminal, JSON por estado, organização dos PDFs |
| `RELATORIO_PENDENCIAS.md` | Relatório técnico do que foi implementado e do que ainda falta |
| `desktop_app.py` | Interface desktop CustomTkinter para seleção e execução do pipeline |
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

O Gemini recebe o PDF inteiro em uma chamada somente quando o parser falha ou
quando o layout não é reconhecido. O Python valida cada cargo separadamente e
calcula Governador, Presidente e Senado.

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
- Senado: 8 candidatos da consolidação das perguntas 05 e 06, na página 13;
- nomes partidos em mais de uma linha, como `DOUTOR ALEXANDRE SALOMÃO` e
  `VETERINÁRIO WILSON GRASSI`.

Cada linha extraída contém `posicao`, `nome`, `partido`, `votos`, `porcentual`,
`porcentagem_valida` e `porcentagem_acumulada`.

O parser rejeita explicitamente páginas que não correspondem ao cargo ou que
não permitem reconstruir linhas com segurança. As colunas são inferidas a
partir dos números e cabeçalhos encontrados na própria página, sem depender de
uma coordenada fixa específica do PDF atual. Nesses casos, o fluxo chama o
Gemini como fallback e preserva no resultado o cargo e o motivo da falha.

### Formatos de entrada

- **PDF textual:** é o formato principal. O PyMuPDF consegue usar texto e
  coordenadas das tabelas.
- **PDF escaneado ou print inserido como imagem:** o parser não lê números de
  uma imagem sem camada de texto. Esse caso segue para o Gemini; OCR pode ser
  adicionado futuramente se houver necessidade e amostras para validar.
- **Print solto (`.png`, `.jpg`):** não é aceito pela CLI atual, que recebe PDF.
  É preciso converter a imagem para PDF ou implementar uma entrada de imagem
  específica no Gemini.
- **Planilha Excel (`.xlsx`):** não é aceita pelo pipeline atual. O parser foi
  projetado para PDF; uma etapa separada com `openpyxl`/`pandas` seria
  necessária para importar planilhas, validar colunas e converter ao mesmo
  contrato `CargoExtraido`.

## Contrato para o frontend

Cada JSON de saída pode ser consumido sem recalcular os dados:

```json
{
  "estado": "PARANÁ",
  "completo": true,
  "avisos": [],
  "cargos": {
    "GOVERNADOR": {
      "origem": "parser",
      "candidatos": []
    }
  }
}
```

`origem` identifica `parser`, `gemini` ou `desconhecida`. `avisos` informa
fallbacks e problemas de extração. `cargos_pendentes` representa cargos
extraídos e validados que ainda não têm uma etapa posterior pronta; no PDF de
referência atual, os três cargos possuem cálculo implementado. `completo`
indica apenas que nenhum cargo esperado faltou.

O frontend deve exibir origem e avisos como informação de confiança e não deve
recalcular percentuais.

## Saída organizada por estado

Cada PDF processado com sucesso (total ou parcial) gera
`saidas/{ESTADO}/pesquisa.json`, com os cargos calculados e um marcador
`"completo": false` + `"cargos_faltando"` quando algum cargo não veio na
extração. Essa é a organização que o robô de integração com o PesquisaPRO vai
consumir mais adiante — o site também separa a apresentação por estado.

## Estado atual

- O parser determinístico funciona isoladamente para Governador, Presidente e
  a consolidação do Senado em tabelas textuais com o layout observado no PDF real.
- O parser e o pipeline híbrido possuem 23 testes automatizados, cobrindo
  extração, posições, votos, percentuais, nomes quebrados, fallback e falhas esperadas.
- PyMuPDF está declarado em `pyproject.toml`, `uv.lock` e `requirements.txt`.
- Governador e Presidente estão implementados no cálculo principal com a mesma
  regra: voto único e base de 100%.
- Quando necessário, um PDF gera UMA chamada ao Gemini para os três cargos;
  quando o parser resolve o documento, não há chamada à API.
- Cada cargo é validado e calculado de forma independente: um erro em Senado
  não trava Governador nem Presidente.
- Senado é extraído da tabela de consolidação, validado na base de 200% e
  calculado com a regra de dois votos por eleitor. A apresentação preserva os
  cinco candidatos mais bem colocados, enquanto os demais cargos usam top 3.
- `CandidatoExtraido` aceita `posicao` e `votos` opcionais, e `CargoExtraido`
  preserva a identificação textual da `pergunta`.
- O parser já está integrado ao `main.py`: evita o Gemini quando as tabelas
  dos três cargos são reconstruídas e marca a origem de cada resultado.
- Prints soltos e planilhas Excel ainda não são entradas suportadas pela CLI;
  XLSX é gerado como saída a partir do JSON na tela Dados / XLSX do app desktop.
- Sem geração de arte, sem integração com o PesquisaPRO, sem browser. Ainda
  não sabemos se o PesquisaPRO tem API — isso é o próximo desconhecido antes
  da Fase 6.

## O que falta

1. Validar o lote com PDFs reais de outros estados e layouts.
2. Generalizar a identificação de cenários, perguntas e cargos para outros
  institutos.
3. Implementar geração das artes e integração com o PesquisaPRO.

O fallback já funciona: parser confiável usa dados estruturados; parser incapaz
   de reconstruir a tabela chama o Gemini.

## Testes

```bash
uv run pytest -q
```

Resultado atual: 23 testes passando, cobrindo parser, contrato com os schemas,
fallback Gemini, validação de posições, processamento em lote e regra matemática
do Senado. Os testes de extração usam o PDF real
local; portanto, dependem da presença de `PDFs/pesquisa.pdf`.