# Relatorio de Pendencias e Evolucao

Data: 2026-09-22

## Objetivo

Extrair dados numericos de tabelas de pesquisas eleitorais com um pipeline
hibrido:

```text
PDF -> Parser deterministico -> Validator -> Calculator -> JSON
                    |
                    +-> Gemini quando a tabela nao puder ser reconstruida
```

O foco e posicao, candidato, partido, votos, percentuais, NS/NR, brancos/nulos
e identificacao da pergunta/cenario. O objetivo nao e resumir o texto do PDF.

## Implementado

### Parser

`table_extractor.py` usa PyMuPDF para:

- localizar automaticamente tabelas estimuladas por cargo;
- agrupar palavras pelas coordenadas verticais;
- reconstruir linhas e colunas;
- lidar com nomes partidos em mais de uma linha;
- extrair posicao, nome, partido, votos, percentual total, percentual valido e
  percentual acumulado;
- extrair NS/NR, brancos/nulos, estado e texto da pergunta;
- rejeitar paginas que nao tenham uma tabela reconstruivel.

No PDF real atualmente usado, o parser funciona para:

- Governador: pagina 7;
- Presidente: pagina 14.

### Pipeline hibrido

`main.py` ja tenta o parser antes do Gemini:

- com `--cargo GOVERNADOR` ou `--cargo PRESIDENTE`, uma extracao valida pode
  ser feita sem chamada a API;
- sem filtro de cargo, Governador e Presidente sao extraidos pelo parser;
- o Gemini continua sendo chamado para completar o Senado;
- se o parser falhar, o Gemini e usado como fallback;
- os dados deterministas substituem os blocos equivalentes retornados pelo
  Gemini.

### Schemas e validacao

- `CandidatoExtraido` aceita `posicao` e `votos` opcionais;
- `CargoExtraido` preserva a `pergunta`;
- `GEMINI_RESPONSE_SCHEMA` conhece posicao, votos e pergunta;
- o validator detecta posicoes duplicadas e faltantes quando elas estao
  presentes;
- o Gemini continua compativel com fixtures antigas que nao tenham esses
  campos.

### Processamento em lote

A CLI agora aceita:

```powershell
uv run main.py --pasta PDFs --cargo GOVERNADOR
```

Cada PDF e processado individualmente. A saida fica organizada por estado e,
no modo lote, usa o nome do PDF como base:

```text
saidas/PARANA/estado_a.pdf.json
saidas/SAO_PAULO/estado_b.pdf.json
```

Se o nome ja existir, o programa cria `_1.json`, `_2.json` etc. O PDF tambem
e movido para `analisados`, `bloqueados` ou `falhos` conforme o resultado.

### Testes

Existem 15 testes automatizados cobrindo:

- tabelas reais de Governador e Presidente;
- nomes quebrados;
- metadados e percentuais de ausentes;
- contrato parser -> schema;
- fallback para Gemini;
- posicoes duplicadas e faltantes;
- descoberta de PDFs e nomes de saida do lote.

Comando:

```powershell
uv run pytest -q
```

## Pendencias tecnicas

### 1. Senado

As tabelas do Senado aparecem em duas perguntas no PDF real. Ainda falta:

- extrair deterministicamente as duas tabelas;
- identificar candidatos, posicoes e percentuais de cada pergunta;
- entender a tabela de consolidacao;
- definir se a saida usa a primeira pergunta, a segunda ou a consolidacao;
- validar a soma correta para dois votos por eleitor;
- implementar o calculo de `Outros` do Senado.

Enquanto isso, Senado pode vir do Gemini, mas fica sem calculo final porque a
regra matematica ainda levanta `NotImplementedError`.

### 2. Robustez para outros layouts

O parser foi calibrado para o layout do PDF Verita usado nos testes. Ainda
precisa lidar com:

- colunas em coordenadas diferentes;
- cabecalhos ou rodapes variaveis;
- tabelas divididas entre paginas;
- PDFs escaneados sem camada de texto;
- outros institutos e formatos de pergunta;
- multiplos cenarios para o mesmo cargo.

PDFs sem texto nativo devem continuar usando OCR ou Gemini como fallback. OCR
nao deve ser adicionado sem uma validacao especifica de custo e qualidade.

### 3. Confiabilidade do fallback

Ainda pode ser melhorado:

- registrar qual cargo veio do parser e qual veio do Gemini;
- registrar o motivo exato da queda para fallback;
- impedir uma saida automatica quando posicao obrigatoria estiver ausente;
- validar que a quantidade de candidatos e as colunas numericas formam uma
  tabela completa;
- incluir a origem (`parser` ou `gemini`) no JSON final, se isso for desejado.

### 4. Saida e operacao

Ainda falta decidir:

- se o nome final deve ser sempre o nome do PDF ou `pesquisa.json`;
- como tratar dois relatorios do mesmo estado e mesmo nome-base;
- se PDFs processados devem ser movidos automaticamente em producao;
- como registrar logs de lote e um resumo de sucessos/falhas.

O comportamento atual evita sobrescrita adicionando sufixos numericos.

### 5. Artes e PesquisaPRO

Ainda nao foram implementados:

- geracao das artes;
- integracao com o PesquisaPRO;
- descoberta ou uso de API;
- automacao de navegador, caso nao exista API.

## Ordem recomendada

1. Validar o lote com varios PDFs reais de estados diferentes.
2. Implementar e testar o parser das duas perguntas do Senado.
3. Definir a regra matematica e o schema final do Senado.
4. Tornar posicao obrigatoria apenas quando a tabela exigir e houver evidencia
   suficiente para isso.
5. Ampliar testes com PDFs de layouts diferentes.
6. Definir contrato de saida para o consumidor das artes.
7. Investigar a API ou o fluxo de navegador do PesquisaPRO.
8. Implementar geracao das artes.

## Estado atual resumido

O pipeline principal ja e hibrido e o processamento em lote ja existe. O
principal bloqueio funcional para cobrir todos os cargos e o Senado: sua tabela
usa dois votos por eleitor e ainda nao possui parser consolidado nem regra de
calculo definida.
