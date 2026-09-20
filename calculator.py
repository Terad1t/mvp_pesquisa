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


def calcular_governador(pesquisa: PesquisaExtraida) -> PesquisaFinal:
    """Regra de cargo majoritário com um voto por eleitor.

    outros_valido = 100 - soma das porcentagens válidas dos nominais
    outros_total  = 100 - (soma dos percentuais nominais + NS/NR + brancos/nulos)
    """
    return PesquisaFinal(
        estado=pesquisa.estado,
        cargo=pesquisa.cargo,
        candidatos=pesquisa.candidatos,
        ns_nr=pesquisa.ns_nr,
        brancos_nulos=pesquisa.brancos_nulos,
        outros_valido=arredondar(CEM - soma_validos(pesquisa)),
        outros_total=arredondar(CEM - soma_total(pesquisa)),
    )


def calcular_senador(pesquisa: PesquisaExtraida) -> PesquisaFinal:
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


def calcular(pesquisa: PesquisaExtraida) -> PesquisaFinal:
    cargo = pesquisa.cargo.strip().upper()
    regra = REGRAS.get(cargo)
    if regra is None:
        raise CargoNaoSuportado(
            f"Sem regra de cálculo para o cargo {cargo!r}. Disponíveis: {sorted(REGRAS)}"
        )
    return regra(pesquisa)