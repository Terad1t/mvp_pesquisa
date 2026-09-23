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

POR QUE "estado" ENTRA COMO PARÂMETRO SEPARADO
------------------------------------------------
CargoExtraido (o que este módulo recebe) não carrega "estado" — isso pertence
ao PDF inteiro, não a um cargo específico (ver schemas.py). Por isso toda
função aqui recebe o bloco do cargo e o estado como argumentos distintos.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from schemas import CargoExtraido, PesquisaFinal

CEM = Decimal("100")
DOIS_VOTOS = Decimal("200")
UMA_CASA = Decimal("0.1")


def arredondar(valor: Decimal) -> Decimal:
    """Uma casa decimal, ROUND_HALF_UP (4.05 -> 4.1).

    Note que o padrão do Python é ROUND_HALF_EVEN ("banker's rounding"), que
    arredondaria 4.05 para 4.0. As pesquisas usam a convenção comercial, então
    a escolha é explícita.
    """
    return valor.quantize(UMA_CASA, rounding=ROUND_HALF_UP)


def soma_validos(bloco: CargoExtraido) -> Decimal:
    return sum((c.porcentagem_valida for c in bloco.candidatos), Decimal(0))


def soma_total(bloco: CargoExtraido) -> Decimal:
    """Soma dos percentuais sobre o total, incluindo NS/NR e brancos/nulos.

    Agnóstico de base — não assume 100 nem 200. Quem chama compara contra o
    alvo que fizer sentido para o cargo (ver validator.py).
    """
    nominais = sum((c.porcentual for c in bloco.candidatos), Decimal(0))
    return nominais + bloco.ns_nr + bloco.brancos_nulos


def _calcular_majoritario_um_voto(bloco: CargoExtraido, estado: str, top_n: int) -> PesquisaFinal:
    """Regra compartilhada para cargos de um voto por eleitor, base 100%.

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

    Usada hoje por Governador e Presidente. Não é chamada diretamente de fora
    deste módulo — cada cargo tem sua própria função pública (ver abaixo),
    mesmo que hoje só repassem para esta. Isso é proposital: se amanhã
    Presidente ganhar uma regra própria (ex.: segundo turno), a mudança fica
    isolada na função do Presidente, sem arriscar quebrar Governador junto.
    """
    ordenados = sorted(bloco.candidatos, key=lambda c: c.porcentagem_valida, reverse=True)
    principais = ordenados[:top_n]

    soma_validos_principais = sum((c.porcentagem_valida for c in principais), Decimal(0))
    soma_totais_principais = sum((c.porcentual for c in principais), Decimal(0))

    return PesquisaFinal(
        estado=estado,
        cargo=bloco.cargo,
        candidatos=principais,
        ns_nr=bloco.ns_nr,
        brancos_nulos=bloco.brancos_nulos,
        outros_valido=arredondar(CEM - soma_validos_principais),
        outros_total=arredondar(CEM - soma_totais_principais - bloco.ns_nr - bloco.brancos_nulos),
    )


def calcular_governador(bloco: CargoExtraido, estado: str, top_n: int = 3) -> PesquisaFinal:
    return _calcular_majoritario_um_voto(bloco, estado, top_n)


def calcular_presidente(bloco: CargoExtraido, estado: str, top_n: int = 3) -> PesquisaFinal:
    return _calcular_majoritario_um_voto(bloco, estado, top_n)


def calcular_senador(bloco: CargoExtraido, estado: str, top_n: int = 3) -> PesquisaFinal:
    """Base 200% (dois votos por eleitor).

    O PDF da Veritá não traz Senado numa tabela só, como Governador e
    Presidente. Traz DUAS perguntas (primeira e segunda intenção de voto) e
    uma tabela de CONSOLIDAÇÃO que soma as duas. O extractor.py já instrui o
    Gemini a usar só a consolidação e extrair a coluna "Porcentagem de
    casos" — que é uma % sobre o total de entrevistados (base 2010, dois
    votos possíveis por pessoa, por isso soma ~200% no total). Essa coluna
    entra no campo `porcentual` de cada candidato (duplicada também em
    `porcentagem_valida`, só como placeholder — ver PROMPT).

    O problema: essa tabela de consolidação NÃO tem uma segunda coluna "só
    votos válidos" como existe para Governador/Presidente. Por isso ela é
    DERIVADA aqui, reescalando para excluir NS/NR e brancos/nulos do
    denominador — o mesmo princípio de "válidos" que já existe para
    Governador, só que partindo de uma base de 200% em vez de 100%:

        base_valida = 200 - ns_nr - brancos_nulos
        valida_candidato = porcentual_candidato / base_valida * 200

    Verificado contra o PDF real do Paraná (pesquisa Veritá, 13-18/09/2026):
    consolidação dá Deltan Dallagnol com 45,3% de casos (NS/NR=27,7%,
    brancos=7,0%) -> base_valida=165,3 -> válida recalculada = 54,8%. O
    Outros válido do top 3 (Deltan+Filipe Barros+Alexandre Curi) fecha em
    71,7%, batendo com a soma direta dos candidatos que sobraram fora do
    top 3 (71,9%, diferença de arredondamento normal). outros_total (escala
    bruta, sem reescalar) fecha em 59,3%, também batendo com a soma direta
    dos que sobraram (59,4%).
    """
    base_valida = DOIS_VOTOS - bloco.ns_nr - bloco.brancos_nulos
    if base_valida <= 0:
        raise ValueError(f"Base de votos válidos do Senado inválida ({base_valida}).")

    recalculados = [
        c.model_copy(
            update={"porcentagem_valida": arredondar(c.porcentual / base_valida * DOIS_VOTOS)}
        )
        for c in bloco.candidatos
    ]

    ordenados = sorted(recalculados, key=lambda c: c.porcentagem_valida, reverse=True)
    principais = ordenados[:top_n]

    soma_validos_principais = sum((c.porcentagem_valida for c in principais), Decimal(0))
    soma_totais_principais = sum((c.porcentual for c in principais), Decimal(0))

    return PesquisaFinal(
        estado=estado,
        cargo=bloco.cargo,
        candidatos=principais,
        ns_nr=bloco.ns_nr,
        brancos_nulos=bloco.brancos_nulos,
        outros_valido=arredondar(DOIS_VOTOS - soma_validos_principais),
        outros_total=arredondar(DOIS_VOTOS - soma_totais_principais - bloco.ns_nr - bloco.brancos_nulos),
    )


# Registro de regras por cargo. É o embrião do RulesEngine: adicionar
# PREFEITO/VEREADOR depois vira uma linha aqui, não um if espalhado pelo main.
REGRAS = {
    "GOVERNADOR": calcular_governador,
    "PRESIDENTE": calcular_presidente,
    "SENADOR": calcular_senador,
}


class CargoNaoSuportado(ValueError):
    pass


def calcular(bloco: CargoExtraido, estado: str, top_n: int = 3) -> PesquisaFinal:
    cargo = bloco.cargo.strip().upper()
    regra = REGRAS.get(cargo)
    if regra is None:
        raise CargoNaoSuportado(
            f"Sem regra de cálculo para o cargo {cargo!r}. Disponíveis: {sorted(REGRAS)}"
        )
    return regra(bloco, estado, top_n=top_n)