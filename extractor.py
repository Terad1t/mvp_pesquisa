"""
Fronteira com o Gemini. Único módulo do projeto que fala com a rede.

Responsabilidade: PDF (bytes) -> ResultadoExtracao (um estado, vários blocos
de cargo). Nada além disso. Sem cálculo, sem regra de negócio, sem formatação.

UMA CHAMADA POR PDF, NÃO UMA POR CARGO
---------------------------------------
Governador, Senador e Presidente estão sempre no mesmo PDF da Veritá. Extrair
cada um com uma chamada separada triplicaria custo e chance de esbarrar em
limite de taxa/sobrecarga para o mesmo documento. Por isso o prompt pede os
três de uma vez, e a resposta vem como {"estado": ..., "cargos": [...]}.

O preço dessa economia é risco de acoplamento: numa resposta única, um erro
de schema em UM cargo poderia, por padrão do Pydantic, invalidar a resposta
inteira — inclusive os cargos que vieram perfeitos. Por isso a validação
abaixo NÃO usa PesquisaPDF.model_validate() no documento inteiro; ela valida
cada item de "cargos" individualmente (ver _validar_blocos), preservando os
que passaram mesmo quando outro falha.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from google import genai
from google.genai import types
from google.genai.errors import ClientError, ServerError
from pydantic import ValidationError

from schemas import GEMINI_RESPONSE_SCHEMA, CargoExtraido

# O nome do modelo muda com frequência. Fica em variável de ambiente para não
# precisar mexer no código quando a família de modelos for atualizada.
#
# IMPORTANTE: isto é lido dentro de _criar_cliente()/extrair(), nunca aqui no
# nível do módulo. Se fosse `MODELO_PADRAO = os.getenv(...)` fixado no import,
# o valor seria capturado ANTES do main.py chamar load_dotenv() — o import de
# extractor acontece no topo do main.py, antes da chamada a load_dotenv() lá
# dentro da função main(). O resultado seria sempre o valor padrão do código,
# ignorando silenciosamente o que está no .env.
_MODELO_FALLBACK = "gemini-3.6-flash"


def _modelo_padrao() -> str:
    return os.getenv("GEMINI_MODEL", _MODELO_FALLBACK)


# Limite prático do envio inline (bytes no corpo da requisição). Acima disso,
# usa-se a Files API. PDFs de pesquisa costumam ficar bem abaixo.
LIMITE_INLINE_BYTES = 15 * 1024 * 1024

PROMPT = """\
Você é um extrator de dados de pesquisas eleitorais. Sua única tarefa é LER o
documento e transcrever os números encontrados.

Este documento normalmente contém pesquisas de intenção de voto para TRÊS
cargos: GOVERNADOR, SENADOR e PRESIDENTE. Extraia os três, cada um como um
item separado dentro de "cargos". Os três são do mesmo estado — preencha
"estado" uma única vez, no nível do documento.

Para GOVERNADOR e PRESIDENTE, use a pergunta de intenção de voto ESTIMULADA
(a que apresenta a lista de candidatos ao entrevistado). Essas perguntas têm
DUAS colunas por candidato — "Porcentual" (sobre o total de entrevistados) e
"Porcentagem válida" (sobre os votos válidos) — extraia as duas exatamente
como aparecem.

Para SENADOR, o documento tem uma estrutura DIFERENTE, porque cada eleitor
tem DOIS votos: existem DUAS perguntas separadas (geralmente "primeira
intenção de voto" e "segunda intenção de voto, excluindo o nome anterior"),
E DEPOIS uma tabela de CONSOLIDAÇÃO que soma as duas (título parecido com
"Consolidação das Perguntas" ou "Consolidação da primeira e segunda
intenção"). Para Senador:
- IGNORE as duas perguntas individuais — não extraia delas.
- Use SOMENTE a tabela de CONSOLIDAÇÃO.
- Essa tabela tem duas colunas de percentual: uma chamada "Porcentagem"
  (soma 100% no total) e outra chamada "Porcentagem de casos" (soma
  aproximadamente 200%, porque cada eleitor pode aparecer em até duas
  linhas). USE A COLUNA "Porcentagem de casos" — é ela que representa a
  fração dos entrevistados que citou aquele candidato, que é a base 200%
  mencionada no restante deste documento.
- Essa mesma tabela também tem uma linha "NS/NR" e uma linha "Branco/nulo"
  — extraia os valores de "Porcentagem de casos" delas para os campos
  ns_nr e brancos_nulos do bloco de SENADOR.
- Coloque o valor de "Porcentagem de casos" de cada candidato TANTO no campo
  "porcentual" QUANTO no campo "porcentagem_valida" (repita o mesmo número
  nos dois campos). O motivo: essa tabela consolidada não tem uma segunda
  coluna "só votos válidos" como Governador e Presidente têm — o Python
  recalcula esse segundo número depois, a partir do que você extraiu aqui.
  Você não precisa (e não deve) calcular nada — apenas repetir o valor.

Regras obrigatórias, para todos os cargos:
- Não faça nenhum cálculo.
- Não invente, complete nem estime valores ausentes.
- Não arredonde. Transcreva o número exatamente como está impresso.
- Não some, não converta e não normalize percentuais (exceto a duplicação
  explicitamente pedida acima para Senador).
- Não misture dados de perguntas, cenários, rodadas ou CARGOS diferentes
  dentro do mesmo item de "cargos" — cada item deve conter candidatos de um
  único cargo.
- Se o documento tiver vários cenários para o mesmo cargo, use o primeiro
  cenário desse cargo.
- Se um dos três cargos genuinamente não aparecer no documento, não inclua um
  item para ele — não invente dados para preencher a lacuna.
- Se algum campo não existir no documento, devolva 0 apenas quando o
  documento explicitamente indicar zero; caso contrário, não preencha.

Devolva somente os dados no formato solicitado.
"""


class ErroDeExtracao(RuntimeError):
    """Falha ao obter uma extração utilizável do Gemini."""


class ErroTransitorio(ErroDeExtracao):
    """A API respondeu, mas com um erro passageiro (sobrecarga, limite de taxa).

    Separado de ErroDeExtracao para que quem chama (main.py, e depois um
    processador em lote na Fase 5) possa decidir tratar isso diferente de um
    erro definitivo: reenfileirar o PDF para tentar de novo mais tarde, em vez
    de descartá-lo junto com PDFs que realmente têm problema de conteúdo.
    """


@dataclass
class ResultadoExtracao:
    """O que sobrevive da resposta do Gemini para um PDF inteiro.

    `cargos` só contém blocos que passaram na validação de schema. `avisos`
    guarda, para cada bloco que falhou, o nome do cargo (quando dava para
    identificar) e o motivo — para aparecer no relatório em vez de
    simplesmente desaparecer em silêncio.
    """

    estado: str
    cargos: list[CargoExtraido]
    avisos: list[str] = field(default_factory=list)


# O SDK já reenvia sozinho em 429/5xx com backoff exponencial (ver
# google.genai.types.HttpRetryOptions), mas o padrão dele é modesto: 5
# tentativas, no máximo 60s de espera entre elas. Picos de demanda no Gemini
# às vezes duram mais que isso. Como este robô vai rodar rotineiramente e sem
# supervisão, vale a pena esperar mais antes de desistir — o custo de esperar
# alguns minutos a mais é irrelevante perto do custo de falhar um PDF que
# seria extraído com sucesso dali a pouco.
_RETRY = types.HttpRetryOptions(
    attempts=8,
    initial_delay=2.0,
    max_delay=90.0,
    exp_base=2,
    http_status_codes=[408, 429, 500, 502, 503, 504],
)


def _criar_cliente() -> genai.Client:
    chave = os.getenv("GEMINI_API_KEY")
    if not chave:
        raise ErroDeExtracao(
            "GEMINI_API_KEY não encontrada. Crie um arquivo .env a partir do .env.example."
        )
    return genai.Client(api_key=chave, http_options=types.HttpOptions(retry_options=_RETRY))


def _validar_blocos(bruto: dict) -> ResultadoExtracao:
    """Valida cada item de 'cargos' individualmente.

    Não usa PesquisaPDF.model_validate(bruto) direto porque isso falharia o
    documento inteiro se um único cargo vier malformado. Aqui, um cargo ruim
    vira um aviso; os outros dois continuam disponíveis.
    """
    estado_bruto = bruto.get("estado")
    if not isinstance(estado_bruto, str) or not estado_bruto.strip():
        raise ErroDeExtracao("Resposta sem campo 'estado' utilizável.")
    estado = estado_bruto.strip().upper()

    itens = bruto.get("cargos")
    if not isinstance(itens, list) or not itens:
        raise ErroDeExtracao("Resposta sem nenhum item em 'cargos'.")

    cargos: list[CargoExtraido] = []
    avisos: list[str] = []
    for item in itens:
        nome_cargo = item.get("cargo") if isinstance(item, dict) else None
        try:
            cargo = CargoExtraido.model_validate(item)
            cargos.append(cargo.model_copy(update={"origem": "gemini"}))
        except ValidationError as erro:
            identificacao = nome_cargo or "cargo não identificado"
            avisos.append(f"{identificacao}: item fora do schema esperado ({erro.error_count()} erro(s))")

    if not cargos:
        raise ErroDeExtracao(
            f"Nenhum cargo passou na validação de schema. Detalhes: {'; '.join(avisos)}"
        )

    return ResultadoExtracao(estado=estado, cargos=cargos, avisos=avisos)


def extrair(caminho_pdf: Path, modelo: str | None = None) -> ResultadoExtracao:
    """Lê um PDF e devolve, para cada cargo reconhecido, os dados brutos —
    sem nada calculado.
    """
    if not caminho_pdf.is_file():
        raise ErroDeExtracao(f"PDF não encontrado: {caminho_pdf}")

    dados = caminho_pdf.read_bytes()
    if len(dados) > LIMITE_INLINE_BYTES:
        raise ErroDeExtracao(
            f"PDF de {len(dados) / 1_048_576:.1f} MB excede o envio inline. "
            "Use client.files.upload() para arquivos grandes."
        )

    cliente = _criar_cliente()

    try:
        resposta = cliente.models.generate_content(
            model=modelo or _modelo_padrao(),
            contents=[
                types.Part.from_bytes(data=dados, mime_type="application/pdf"),
                PROMPT,
            ],
            config=types.GenerateContentConfig(
                # temperature=0 não torna o modelo determinístico, mas reduz
                # bastante a variação entre execuções — o que importa quando o
                # mesmo PDF precisa render o mesmo JSON toda vez.
                temperature=0,
                response_mime_type="application/json",
                response_schema=GEMINI_RESPONSE_SCHEMA,
            ),
        )
    except ServerError as erro:
        # Chegou aqui depois de esgotar as 8 tentativas do _RETRY acima — ou
        # seja, o problema persistiu por vários minutos, não foi um soluço de
        # um segundo. Ainda assim é transitório do ponto de vista do
        # conteúdo: nada nesse PDF está errado, o serviço que estava fora.
        raise ErroTransitorio(
            f"Gemini indisponível após múltiplas tentativas ({erro.code}): {erro.message}. "
            "Tente este PDF novamente mais tarde."
        ) from erro
    except ClientError as erro:
        if erro.code == 429:
            raise ErroTransitorio(
                f"Limite de taxa da API atingido ({erro.message}). Tente novamente em instantes."
            ) from erro
        # 4xx que não seja 429 é erro nosso (chave inválida, payload malformado,
        # modelo inexistente) — não adianta reenfileirar, precisa de correção.
        raise ErroDeExtracao(f"Erro da API ({erro.code}): {erro.message}") from erro

    texto = (resposta.text or "").strip()
    if not texto:
        raise ErroDeExtracao("O Gemini devolveu uma resposta vazia.")

    try:
        # parse_float=Decimal é o detalhe que evita perder precisão logo na
        # porta de entrada: o JSON traz 51.8 como texto, e converter direto
        # para Decimal preserva esse valor. Se passasse por float primeiro,
        # 51.8 já entraria como 51.79999999999999715782905696.
        bruto = json.loads(texto, parse_float=Decimal)
    except json.JSONDecodeError as erro:
        raise ErroDeExtracao(f"Resposta não é JSON válido: {erro}") from erro

    if not isinstance(bruto, dict):
        raise ErroDeExtracao("Resposta não é um objeto JSON no formato esperado.")

    return _validar_blocos(bruto)


def extrair_de_json(caminho_json: Path) -> ResultadoExtracao:
    """Atalho para testar as fases 2–4 sem gastar chamada de API.

    Aceita o mesmo formato {"estado": ..., "cargos": [...]} que o Gemini
    devolveria.
    """
    bruto = json.loads(caminho_json.read_text(encoding="utf-8"), parse_float=Decimal)
    if not isinstance(bruto, dict):
        raise ErroDeExtracao("JSON de teste não é um objeto no formato esperado.")
    return _validar_blocos(bruto)