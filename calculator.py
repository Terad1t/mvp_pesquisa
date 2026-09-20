"""
Regras matemáticas. Nenhuma chamada de rede, nenhum I/O: funções puras.

POR QUE Decimal E NÃO float
---------------------------
float é binário. 51.8 não tem representação exata em binário, assim como 1/3
não tem representação exata em decimal. O erro é minúsculo, mas se acumula:

    >>> 51.8 + 24.3 + 19.8
    95.89999999999999
    >>> 100 - (51.8 + 24.3 + 19.8)
    4.100000000000009

Com Decimal:

    >>> Decimal("100") - (Decimal("51.8") + Decimal("24.3") + Decimal("19.8"))
    Decimal("4.1")

Isso importa por dois motivos concretos aqui. Primeiro, o número vai virar uma
arte publicada — "4.100000000000009" ou um 4.0 vindo de arredondamento torto é
um erro visível. Segundo, e mais sutil: o validator compara somas com uma
tolerância. Com float, parte dessa tolerância é gasta absorvendo ruído do
próprio tipo, e aí você não sabe mais se o desvio veio do PDF ou do Python.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from schemas import PesquisaExtraida, PesquisaFinal

CEM = Decimal("100")
UMA_CASA = Decimal("0.1")


def arredondar(valor: Decimal) -> Decimal:
    """Uma casa decimal, ROUND_HALF_UP (4.05 -> 4.1).

    Note que o padrão do Python é ROUND_HALF_EVEN ("banker's rounding"), que
    arredondaria 4.05 para 4.0. As pesquisas usam a convenção comercial, então
    a escolha é explícita.
    """
    return valor.quantize(UMA_CASA, rounding=ROUND_HALF_UP)


def soma_validos(pesquisa: PesquisaExtraida) -> Decimal:
    return sum((c.porcentagem_valida for c in pesquisa.candidatos), Decimal(0))


def soma_total(pesquisa: PesquisaExtraida) -> Decimal:
    """Soma dos percentuais sobre o total, incluindo NS/NR e brancos/nulos."""
    nominais = sum((c.porcentual for c in pesquisa.candidatos), Decimal(0))
    return nominais + pesquisa.ns_nr + pesquisa.brancos_nulos


def calcular_governador(pesquisa: PesquisaExtraida, top_n: int = 3) -> PesquisaFinal:
    """Regra de cargo majoritário com um voto por eleitor.

    Mantém apenas os `top_n` candidatos mais votados (por porcentagem válida)
    e agrega todo o resto em "Outros".

    outros_valido = 100 - soma das porcentagens válidas dos top_n
    outros_total  = 100 - (soma dos percentuais totais dos top_n + NS/NR + brancos/nulos)

    Por que "100 menos os top_n" e não "soma literal dos candidatos que sobraram":
    são equivalentes quando a extração pegou todo mundo (o caso comum), mas a
    subtração é mais robusta. Se o Gemini não extrair um candidato minúsculo,
    ou se o próprio PDF já trouxer uma linha agregada sem nomear ninguém, o
    resultado de "Outros" continua correto — porque ele nunca depende de somar
    os itens que sobraram, só de saber quem são os top_n. O cálculo só quebra
    se o próprio top_n estiver errado, que é exatamente o que a validação
    (checagem de coerência entre colunas) já protege.
    """
    ordenados = sorted(pesquisa.candidatos, key=lambda c: c.porcentagem_valida, reverse=True)
    principais = ordenados[:top_n]

    soma_validos_principais = sum((c.porcentagem_valida for c in principais), Decimal(0))
    soma_totais_principais = sum((c.porcentual for c in principais), Decimal(0))

    return PesquisaFinal(
        estado=pesquisa.estado,
        cargo=pesquisa.cargo,
        candidatos=principais,
        ns_nr=pesquisa.ns_nr,
        brancos_nulos=pesquisa.brancos_nulos,
        outros_valido=arredondar(CEM - soma_validos_principais),
        outros_total=arredondar(CEM - soma_totais_principais - pesquisa.ns_nr - pesquisa.brancos_nulos),
    )


def calcular_senador(pesquisa: PesquisaExtraida, top_n: int = 3) -> PesquisaFinal:
    """Ainda não implementado — e essa ausência é intencional.

    No Senado o eleitor vota em dois nomes, então a base não é 100 e sim ~200,
    e ainda existe a consolidação dos dois votos. Reaproveitar a fórmula de
    Governador aqui produziria "Outros" negativo sem quebrar nada, que é
    exatamente o tipo de erro silencioso que este projeto quer evitar.
    """
    raise NotImplementedError(
        "Regra de Senado ainda não definida (base ~200%, dois votos por eleitor)."
    )


# Registro de regras por cargo. É o embrião do RulesEngine: adicionar
# PREFEITO/VEREADOR depois vira uma linha aqui, não um if espalhado pelo main.
REGRAS = {
    "GOVERNADOR": calcular_governador,
    "SENADOR": calcular_senador,
}


class CargoNaoSuportado(ValueError):
    pass


def calcular(pesquisa: PesquisaExtraida, top_n: int = 3) -> PesquisaFinal:
    cargo = pesquisa.cargo.strip().upper()
    regra = REGRAS.get(cargo)
    if regra is None:
        raise CargoNaoSuportado(
            f"Sem regra de cálculo para o cargo {cargo!r}. Disponíveis: {sorted(REGRAS)}"
        )
    return regra(pesquisa, top_n=top_n)