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
- inferir a faixa das linhas e das colunas a partir do conteudo da propria
  pagina, reduzindo dependencia de coordenadas fixas.

No PDF real atualmente usado, o parser funciona para:

- Governador: pagina 7;
- Presidente: pagina 14;
- Senado, usando a consolidacao das perguntas 05 e 06: pagina 13.

### Pipeline hibrido

`main.py` ja tenta o parser antes do Gemini:

- com `--cargo GOVERNADOR`, `--cargo PRESIDENTE` ou `--cargo SENADOR`, uma extracao valida pode
  ser feita sem chamada a API;
- sem filtro de cargo, o parser tenta os tres cargos;
- o Gemini so e chamado para cargos cuja tabela nao puder ser reconstruida;
- se o parser falhar, o Gemini e usado como fallback;
- o resultado preserva avisos com o cargo e o motivo do fallback;
- cada `PesquisaFinal` informa `origem` como `parser`, `gemini` ou `desconhecida`;
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

Existem 23 testes automatizados cobrindo:

- tabelas reais de Governador e Presidente;
- nomes quebrados;
- metadados e percentuais de ausentes;
- contrato parser -> schema;
- fallback para Gemini;
- posicoes duplicadas e faltantes;
- descoberta de PDFs e nomes de saida do lote;
- posicoes parcialmente ausentes;
- validacao da base de 200% do Senado;
- recalculo de percentuais validos e `Outros` para o Senado.

Comando:

```powershell
uv run pytest -q
```

## Pendencias tecnicas

### 1. Senado e novos layouts

As tabelas do Senado aparecem em duas perguntas e uma consolidacao no PDF real.
A consolidacao ja e extraida deterministicamente e a regra de base 200% ja foi
implementada. Ainda falta validar esse comportamento em mais institutos e
layouts e ampliar os testes com outros formatos de consolidacao.

Se a consolidacao nao puder ser reconstruida, o Gemini continua sendo fallback.

### 2. Formatos de entrada

- PDFs textuais sao suportados pelo parser.
- PDFs escaneados ou prints dentro de PDF nao sao lidos deterministicamente;
  podem seguir para Gemini, mas dependem da capacidade multimodal da API.
- PNG/JPG soltos nao sao aceitos pela CLI atual.
- XLSX nao e aceito; exigiria um importador separado para o contrato dos
  schemas.

### 3. Robustez para outros layouts

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

### 4. Confiabilidade do fallback

Ainda pode ser melhorado:

- impedir uma saida automatica quando posicao obrigatoria estiver ausente;
- validar que a quantidade de candidatos e as colunas numericas formam uma
  tabela completa;
- definir se o frontend precisa de niveis de confianca alem de `origem` e
  `avisos`.

### 5. Saida e operacao

Ainda falta decidir:

- se o nome final deve ser sempre o nome do PDF ou `pesquisa.json`;
- como tratar dois relatorios do mesmo estado e mesmo nome-base;
- se PDFs processados devem ser movidos automaticamente em producao;
- como registrar logs de lote e um resumo de sucessos/falhas.

O comportamento atual evita sobrescrita adicionando sufixos numericos.

### 6. Artes e PesquisaPRO

Ainda nao foram implementados:

- geracao das artes;
- integracao com o PesquisaPRO;
- descoberta ou uso de API;
- automacao de navegador, caso nao exista API.

### 7. Frontend desktop

O app CustomTkinter ja cobre selecao de PDF/pasta, acompanhamento por cargo,
consulta dos dados estruturados do JSON, exportacao JSON -> XLSX, fila de
bloqueados, historico por estado e configuracoes basicas. Ainda falta
empacotar com PyInstaller e testar a experiencia em uma maquina limpa.

## Ordem recomendada

1. Validar o lote com varios PDFs reais de estados diferentes.
2. Validar o parser do Senado em outros formatos de consolidacao.
3. Tornar posicao obrigatoria apenas quando a tabela exigir e houver evidencia
   suficiente para isso.
4. Ampliar testes com PDFs de layouts diferentes.
5. Definir contrato de saida para o consumidor das artes.
6. Investigar a API ou o fluxo de navegador do PesquisaPRO.
7. Implementar geracao das artes.

## Estado atual resumido

O pipeline principal ja e hibrido, cobre os tres cargos do PDF real e o
processamento em lote ja existe. Os principais riscos restantes sao variacoes
de layout, validacao em mais PDFs e a integracao futura com a geracao das artes.
