"""
Fronteira com o Gemini. Único módulo do projeto que fala com a rede.

Responsabilidade: PDF (bytes) -> PesquisaExtraida.
Nada além disso. Sem cálculo, sem regra de negócio, sem formatação.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path

from google import genai
from google.genai import types
from google.genai.errors import ClientError, ServerError
from pydantic import ValidationError

from schemas import GEMINI_RESPONSE_SCHEMA, PesquisaExtraida

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

Extraia os dados referentes ao cargo de {cargo}, na pergunta de intenção de voto
ESTIMULADA (a que apresenta a lista de candidatos ao entrevistado).

Regras obrigatórias:
- Não faça nenhum cálculo.
- Não invente, complete nem estime valores ausentes.
- Não arredonde. Transcreva o número exatamente como está impresso.
- Não some, não converta e não normalize percentuais.
- Não misture dados de perguntas, cenários ou rodadas diferentes da pesquisa.
- Se o documento tiver vários cenários, use o primeiro cenário do cargo pedido.
- Se algum campo não existir no documento, devolva 0 apenas quando o documento
  explicitamente indicar zero; caso contrário, não preencha.

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


def extrair(caminho_pdf: Path, cargo: str = "GOVERNADOR", modelo: str | None = None) -> PesquisaExtraida:
    """Lê um PDF e devolve os dados estruturados, sem nada calculado."""
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
                PROMPT.format(cargo=cargo),
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

    try:
        return PesquisaExtraida.model_validate(bruto)
    except ValidationError as erro:
        raise ErroDeExtracao(f"JSON fora do schema esperado:\n{erro}") from erro


def extrair_de_json(caminho_json: Path) -> PesquisaExtraida:
    """Atalho para testar as fases 2–4 sem gastar chamada de API."""
    bruto = json.loads(caminho_json.read_text(encoding="utf-8"), parse_float=Decimal)
    return PesquisaExtraida.model_validate(bruto)