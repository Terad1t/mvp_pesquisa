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
from pydantic import ValidationError

from schemas import GEMINI_RESPONSE_SCHEMA, PesquisaExtraida

# O nome do modelo muda com frequência. Fica em variável de ambiente para não
# precisar mexer no código quando a família de modelos for atualizada.
MODELO_PADRAO = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

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


def _criar_cliente() -> genai.Client:
    chave = os.getenv("GEMINI_API_KEY")
    if not chave:
        raise ErroDeExtracao(
            "GEMINI_API_KEY não encontrada. Crie um arquivo .env a partir do .env.example."
        )
    return genai.Client(api_key=chave)


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

    resposta = cliente.models.generate_content(
        model=modelo or MODELO_PADRAO,
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