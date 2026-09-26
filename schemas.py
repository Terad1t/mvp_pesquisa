"""
Contratos de dados do MVP.

Existem DOIS conjuntos de modelos propositalmente separados:

    CargoExtraido / PesquisaPDF  -> exatamente o que o Gemini tem permissão
                                     de dizer.
    PesquisaFinal                -> o que o Python produz depois de calcular.

Essa separação não é estética. Se "outros_valido" existisse no modelo de
extração, mais cedo ou mais tarde alguém (ou o próprio modelo) preencheria
esse campo, e a regra "a IA não calcula" viraria uma promessa em vez de uma
garantia. Aqui ela é estrutural: não existe campo onde o Gemini possa
escrever um valor calculado.

Um PDF real da Veritá traz GOVERNADOR, SENADOR e PRESIDENTE juntos, do mesmo
estado. Por isso a extração não é "um cargo isolado" — é um documento (com um
"estado") que contém uma LISTA de blocos de cargo. Ver extractor.py para como
cada bloco é validado de forma independente, para que um erro de schema em um
cargo não derrube os outros dois.

Todos os percentuais são Decimal, nunca float. Ver comentário em calculator.py.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Cargos que todo PDF da Veritá traz, sempre juntos, do mesmo estado. Usado
# pelo main.py para detectar quando o Gemini devolveu menos cargos do que
# deveria — sinal de falha de extração, não de que o cargo genuinamente não
# existia na pesquisa (ver PROMPT em extractor.py, que também permite o
# Gemini omitir um cargo que de fato não esteja no documento — a checagem
# aqui existe para o caso comum, onde os três sempre aparecem).
CARGOS_ESPERADOS = ("GOVERNADOR", "SENADOR", "PRESIDENTE")

# --------------------------------------------------------------------------
# Modelos de EXTRAÇÃO (fronteira com o Gemini)
# --------------------------------------------------------------------------


class CandidatoExtraido(BaseModel):
    """Um candidato exatamente como aparece no PDF."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    posicao: int | None = Field(default=None, ge=1)
    nome: str = Field(min_length=1)
    partido: str = Field(min_length=1)
    votos: int | None = Field(default=None, ge=0)
    porcentual: Decimal = Field(description="% sobre o total de entrevistados")
    porcentagem_valida: Decimal = Field(description="% sobre os votos válidos")

    @field_validator("nome", "partido")
    @classmethod
    def _nao_vazio(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("campo textual vazio")
        return v.upper()


class CargoExtraido(BaseModel):
    """O resultado bruto de UM cargo dentro do PDF. Nenhum campo derivado.

    Não carrega "estado" — isso pertence ao documento como um todo
    (PesquisaPDF), não a um cargo específico, porque os três cargos de um
    mesmo PDF são sempre do mesmo estado.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    cargo: str = Field(min_length=1)
    pergunta: str | None = Field(default=None, min_length=1)
    origem: str | None = Field(default=None, min_length=1)
    candidatos: list[CandidatoExtraido] = Field(min_length=1)
    ns_nr: Decimal
    brancos_nulos: Decimal

    @field_validator("cargo")
    @classmethod
    def _cargo_maiusculo(cls, v: str) -> str:
        return v.strip().upper()


class PesquisaPDF(BaseModel):
    """Container do PDF inteiro: um estado, vários cargos.

    Usado apenas quando a extração INTEIRA valida de primeira. O caminho
    comum (ver extractor.py) valida cada CargoExtraido separadamente e monta
    o equivalente deste objeto manualmente, para isolar falhas por cargo —
    mas o tipo continua existindo aqui como o contrato "ideal" do documento.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    estado: str = Field(min_length=1)
    cargos: list[CargoExtraido] = Field(min_length=1)


# --------------------------------------------------------------------------
# Modelo FINAL (saída do Python, por cargo)
# --------------------------------------------------------------------------


class PesquisaFinal(BaseModel):
    """Extração de UM cargo + campos calculados deterministicamente em Python."""

    model_config = ConfigDict(extra="forbid")

    estado: str
    cargo: str
    origem: str = "desconhecida"
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

_CANDIDATO_ITEM_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "posicao": {"type": "INTEGER", "description": "Posição da linha na tabela, quando disponível"},
        "nome": {"type": "STRING", "description": "Nome do candidato como aparece no documento"},
        "partido": {"type": "STRING", "description": "Sigla do partido. Ex.: PL, PDT, PSD"},
        "votos": {"type": "INTEGER", "description": "Frequência/quantidade de entrevistados, quando disponível"},
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
}

GEMINI_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "estado": {
            "type": "STRING",
            "description": (
                "Unidade federativa da pesquisa, por extenso e em maiúsculas. Ex.: PARANÁ. "
                "É o mesmo estado para todos os cargos do documento."
            ),
        },
        "cargos": {
            "type": "ARRAY",
            "description": (
                "Um item para cada cargo majoritário encontrado no documento (GOVERNADOR, "
                "SENADOR, PRESIDENTE). Cada item é auto-contido: não misture candidatos de "
                "cargos diferentes no mesmo item."
            ),
            "items": {
                "type": "OBJECT",
                "properties": {
                    "cargo": {
                        "type": "STRING",
                        "description": "GOVERNADOR, SENADOR ou PRESIDENTE",
                    },
                    "pergunta": {
                        "type": "STRING",
                        "description": "Texto ou identificação da pergunta/cenário, quando disponível",
                    },
                    "candidatos": {
                        "type": "ARRAY",
                        "description": (
                            "Candidatos nominalmente citados para ESTE cargo. NÃO inclua linhas "
                            "agregadas como 'Outros', 'Nenhum', 'Branco/Nulo' ou 'NS/NR' nesta lista."
                        ),
                        "items": _CANDIDATO_ITEM_SCHEMA,
                    },
                    "ns_nr": {
                        "type": "NUMBER",
                        "description": "Percentual de Não sabe / Não respondeu para ESTE cargo, sobre o total de entrevistados",
                    },
                    "brancos_nulos": {
                        "type": "NUMBER",
                        "description": "Percentual de brancos e nulos somados para ESTE cargo, sobre o total de entrevistados",
                    },
                },
                "required": ["cargo", "candidatos", "ns_nr", "brancos_nulos"],
            },
        },
    },
    "required": ["estado", "cargos"],
}