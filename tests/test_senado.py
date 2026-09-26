from decimal import Decimal

from calculator import calcular_senador
from schemas import CargoExtraido
from validator import Nivel, validar_extracao


def _bloco_senado(percentuais: tuple[int, ...], ns_nr: int = 15, brancos: int = 5):
    return CargoExtraido.model_validate(
        {
            "cargo": "SENADOR",
            "candidatos": [
                {
                    "nome": f"CANDIDATO {indice}",
                    "partido": "PARTIDO",
                    "porcentual": percentual,
                    "porcentagem_valida": percentual,
                }
                for indice, percentual in enumerate(percentuais, start=1)
            ],
            "ns_nr": ns_nr,
            "brancos_nulos": brancos,
        }
    )


def test_validator_aceita_base_de_200_porcento():
    ocorrencias = validar_extracao(_bloco_senado((80, 60, 40)))

    assert ocorrencias == []


def test_validator_rejeita_coluna_de_base_100_no_senado():
    ocorrencias = validar_extracao(_bloco_senado((40, 30, 20), ns_nr=5, brancos=5))

    erros = [o for o in ocorrencias if o.nivel is Nivel.ERRO]
    assert any("foge de 200" in o.mensagem for o in erros)


def test_calculo_do_senado_recalcula_validos_e_outros():
    resultado = calcular_senador(_bloco_senado((50, 40, 35, 30, 25)), "PARANÁ", top_n=3)

    assert [c.nome for c in resultado.candidatos] == [
        "CANDIDATO 1", "CANDIDATO 2", "CANDIDATO 3", "CANDIDATO 4", "CANDIDATO 5"
    ]
    assert resultado.candidatos[0].porcentagem_valida == Decimal("55.6")
    assert resultado.candidatos[4].porcentagem_valida == Decimal("27.8")
    assert resultado.outros_valido == Decimal("0.0")
    assert resultado.outros_total == Decimal("0.0")