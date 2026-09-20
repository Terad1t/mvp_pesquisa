"""
Contratos de dados do MVP.

Existem DOIS modelos propositalmente separados:

    PesquisaExtraida  -> exatamente o que o Gemini tem permissão de dizer.
    PesquisaFinal     -> o que o Python produz depois de calcular.

Essa separação não é estética. Se "outros_valido" existisse no modelo de
extração, mais cedo ou mais tarde alguém (ou o próprio modelo) preencheria
esse campo, e a regra "a IA não calcula" viraria uma promessa em vez de uma
garantia. Aqui ela é estrutural: não existe campo onde o Gemini possa
escrever um valor calculado.

Todos os percentuais são Decimal, nunca float. Ver comentário em calculator.py.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# --------------------------------------------------------------------------
# Modelos de EXTRAÇÃO (fronteira com o Gemini)
# --------------------------------------------------------------------------


class CandidatoExtraido(BaseModel):
    """Um candidato exatamente como aparece no PDF."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    nome: str = Field(min_length=1)
    partido: str = Field(min_length=1)
    porcentual: Decimal = Field(description="% sobre o total de entrevistados")
    porcentagem_valida: Decimal = Field(description="% sobre os votos válidos")

    @field_validator("nome", "partido")
    @classmethod
    def _nao_vazio(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("campo textual vazio")
        return v.upper()


class PesquisaExtraida(BaseModel):
    """O resultado bruto da leitura do documento. Nenhum campo derivado."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    estado: str = Field(min_length=1)
    cargo: str = Field(min_length=1)
    candidatos: list[CandidatoExtraido] = Field(min_length=1)
    ns_nr: Decimal
    brancos_nulos: Decimal


# --------------------------------------------------------------------------
# Modelo FINAL (saída do Python)
# --------------------------------------------------------------------------


class PesquisaFinal(BaseModel):
    """Extração + campos calculados deterministicamente em Python."""

    model_config = ConfigDict(extra="forbid")

    estado: str
    cargo: str
    candidatos: list[CandidatoExtraido]
    ns_nr: Decimal
    brancos_nulos: Decimal
    outros_valido: Decimal
    outros_total: Decimal


# --------------------------------------------------------------------------
# Schema enviado ao Gemini (response_schema)
# --------------------------------------------------------------------------
#
# Por que escrever o schema à mão em vez de passar a classe Pydantic direto?
#
# 1. O Gemini não conhece Decimal — o tipo do wire é NUMBER. Passar o modelo
#    Pydantic com Decimal pode falhar ou ser convertido de forma implícita.
# 2. As "description" abaixo entram no prompt efetivo do modelo. Elas são o
#    lugar certo para desambiguar "porcentual" vs "porcentagem válida" —
#    muito mais eficaz do que repetir isso no texto do prompt.
# 3. "required" força o modelo a devolver o campo em vez de omiti-lo.

GEMINI_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "estado": {
            "type": "STRING",
            "description": "Unidade federativa da pesquisa, por extenso e em maiúsculas. Ex.: PARANÁ",
        },
        "cargo": {
            "type": "STRING",
            "description": "Cargo em disputa nesta pergunta específica. Ex.: GOVERNADOR, SENADOR, PREFEITO",
        },
        "candidatos": {
            "type": "ARRAY",
            "description": (
                "Candidatos nominalmente citados nesta pergunta. NÃO inclua linhas agregadas "
                "como 'Outros', 'Nenhum', 'Branco/Nulo' ou 'NS/NR' nesta lista."
            ),
            "items": {
                "type": "OBJECT",
                "properties": {
                    "nome": {"type": "STRING", "description": "Nome do candidato como aparece no documento"},
                    "partido": {"type": "STRING", "description": "Sigla do partido. Ex.: PL, PDT, PSD"},
                    "porcentual": {
                        "type": "NUMBER",
                        "description": (
                            "Percentual sobre o TOTAL de entrevistados (inclui brancos, nulos e NS/NR "
                            "no denominador). Normalmente é a coluna com o menor valor das duas."
                        ),
                    },
                    "porcentagem_valida": {
                        "type": "NUMBER",
                        "description": (
                            "Percentual sobre os votos VÁLIDOS (exclui brancos, nulos e NS/NR do "
                            "denominador). Normalmente é a coluna com o maior valor das duas."
                        ),
                    },
                },
                "required": ["nome", "partido", "porcentual", "porcentagem_valida"],
            },
        },
        "ns_nr": {
            "type": "NUMBER",
            "description": "Percentual de Não sabe / Não respondeu, sobre o total de entrevistados",
        },
        "brancos_nulos": {
            "type": "NUMBER",
            "description": "Percentual de brancos e nulos somados, sobre o total de entrevistados",
        },
    },
    "required": ["estado", "cargo", "candidatos", "ns_nr", "brancos_nulos"],
}