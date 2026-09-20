"""
A barreira de segurança.

Duas observações sobre o desenho deste módulo:

1. O retorno não é um booleano. Um booleano só responde "passou?"; o que você
   vai precisar na Fase 5, testando 10–20 PDFs, é "o que exatamente não fechou,
   e em qual candidato". Por isso o retorno é uma lista de Ocorrencia.

2. Existem dois níveis: ERRO bloqueia a arte, ALERTA apenas sinaliza. Se tudo
   fosse ERRO, o arredondamento normal das pesquisas (que faz somas darem 99.9
   ou 100.1) bloquearia praticamente todo PDF, e em pouco tempo alguém
   desligaria o validator inteiro. Um validador que grita sempre é um validador
   que será ignorado.

A verificação mais valiosa está em `_checar_coerencia_entre_colunas`.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from calculator import CEM, soma_total, soma_validos
from schemas import PesquisaExtraida, PesquisaFinal

# Tolerâncias em pontos percentuais.
TOL_SOMA = Decimal("1.5")        # arredondamento acumulado nas somas do PDF
TOL_DIVERGENCIA_ALERTA = Decimal("0.3")
TOL_DIVERGENCIA_ERRO = Decimal("1.0")
TOL_NEGATIVO = Decimal("0.2")


class Nivel(str, Enum):
    ERRO = "ERRO"
    ALERTA = "ALERTA"


@dataclass(frozen=True)
class Ocorrencia:
    nivel: Nivel
    mensagem: str


def _faixa(valor: Decimal, rotulo: str, maximo: Decimal = CEM) -> list[Ocorrencia]:
    if valor < 0:
        return [Ocorrencia(Nivel.ERRO, f"{rotulo} é negativo ({valor}).")]
    if valor > maximo:
        return [Ocorrencia(Nivel.ERRO, f"{rotulo} passa de {maximo} ({valor}).")]
    return []


def _checar_coerencia_entre_colunas(p: PesquisaExtraida) -> list[Ocorrencia]:
    """Cruza as duas colunas usando a relação que as define.

    Percentual válido é o percentual total recalculado sobre uma base que
    exclui NS/NR e brancos/nulos:

        valida = porcentual / (100 - ns_nr - brancos_nulos) * 100

    Conferindo com o exemplo do briefing (base = 100 - 2.3 - 1.2 = 96.5):

        50.0 / 96.5 * 100 = 51.81  -> PDF diz 51.8  ✓
        23.5 / 96.5 * 100 = 24.35  -> PDF diz 24.3  ✓
        19.1 / 96.5 * 100 = 19.79  -> PDF diz 19.8  ✓

    Por que isso é a checagem mais importante do arquivo: ela não verifica se
    os números são *plausíveis*, verifica se são *mutuamente consistentes*. Um
    LLM que troque duas colunas de lugar, pule uma linha da tabela ou misture
    dois cenários da pesquisa produz números individualmente plausíveis — todos
    entre 0 e 100, todos somando perto de 100 — e passaria por qualquer
    validação de faixa. Mas quase nunca sobrevive a esta relação.
    """
    base = CEM - p.ns_nr - p.brancos_nulos
    if base <= 0:
        return [Ocorrencia(Nivel.ERRO, f"Base de votos válidos inválida ({base}).")]

    ocorrencias: list[Ocorrencia] = []
    for c in p.candidatos:
        esperado = c.porcentual / base * CEM
        desvio = abs(esperado - c.porcentagem_valida)
        if desvio > TOL_DIVERGENCIA_ERRO:
            ocorrencias.append(
                Ocorrencia(
                    Nivel.ERRO,
                    f"{c.nome}: % válida informada ({c.porcentagem_valida}) diverge "
                    f"{desvio:.2f} pp do esperado ({esperado:.2f}) a partir de "
                    f"{c.porcentual} sobre base {base}.",
                )
            )
        elif desvio > TOL_DIVERGENCIA_ALERTA:
            ocorrencias.append(
                Ocorrencia(
                    Nivel.ALERTA,
                    f"{c.nome}: pequena divergência entre colunas ({desvio:.2f} pp).",
                )
            )
    return ocorrencias


def validar_extracao(p: PesquisaExtraida) -> list[Ocorrencia]:
    """Roda ANTES do cálculo. Lixo que entra aqui vira arte errada lá na frente."""
    ocorrencias: list[Ocorrencia] = []

    if not p.candidatos:
        ocorrencias.append(Ocorrencia(Nivel.ERRO, "Nenhum candidato extraído."))

    nomes = [c.nome for c in p.candidatos]
    duplicados = {n for n in nomes if nomes.count(n) > 1}
    if duplicados:
        ocorrencias.append(
            Ocorrencia(Nivel.ERRO, f"Candidato repetido na extração: {', '.join(sorted(duplicados))}.")
        )

    for c in p.candidatos:
        ocorrencias += _faixa(c.porcentual, f"{c.nome} (% total)")
        ocorrencias += _faixa(c.porcentagem_valida, f"{c.nome} (% válidos)")

    ocorrencias += _faixa(p.ns_nr, "NS/NR")
    ocorrencias += _faixa(p.brancos_nulos, "Brancos/Nulos")

    st = soma_total(p)
    if st > CEM + TOL_SOMA:
        ocorrencias.append(
            Ocorrencia(Nivel.ERRO, f"Soma dos percentuais totais passa de 100 ({st}).")
        )
    sv = soma_validos(p)
    if sv > CEM + TOL_SOMA:
        ocorrencias.append(
            Ocorrencia(Nivel.ERRO, f"Soma das porcentagens válidas passa de 100 ({sv}).")
        )

    ocorrencias += _checar_coerencia_entre_colunas(p)
    return ocorrencias


def validar_resultado(p: PesquisaFinal) -> list[Ocorrencia]:
    """Roda DEPOIS do cálculo, sobre os valores que iriam para a arte."""
    ocorrencias: list[Ocorrencia] = []

    for valor, rotulo in ((p.outros_valido, "Outros válidos"), (p.outros_total, "Outros total")):
        if valor < -TOL_NEGATIVO:
            ocorrencias.append(
                Ocorrencia(Nivel.ERRO, f"{rotulo} ficou negativo ({valor}) — a soma estourou 100.")
            )
        elif valor < 0:
            ocorrencias.append(
                Ocorrencia(Nivel.ALERTA, f"{rotulo} levemente negativo ({valor}); tratado como 0.0.")
            )

    # Sanidade final: "Outros" maior que o líder quase sempre indica candidato
    # perdido na extração, não uma corrida realmente pulverizada.
    if p.candidatos:
        lider = max(c.porcentagem_valida for c in p.candidatos)
        if p.outros_valido > lider:
            ocorrencias.append(
                Ocorrencia(
                    Nivel.ALERTA,
                    f"Outros válidos ({p.outros_valido}) supera o líder ({lider}). "
                    "Provável candidato não extraído do PDF.",
                )
            )
    return ocorrencias


def tem_erro(ocorrencias: list[Ocorrencia]) -> bool:
    return any(o.nivel is Nivel.ERRO for o in ocorrencias)