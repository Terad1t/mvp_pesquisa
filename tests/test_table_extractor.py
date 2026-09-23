from decimal import Decimal
from pathlib import Path

import pytest

from table_extractor import TabelaNaoEncontrada, extrair_cargo, extrair_governador, extrair_tabela


PDF_REAL = Path(__file__).parents[1] / "PDFs" / "pesquisa.pdf"


@pytest.fixture
def tabela_governador() -> list[dict[str, object]]:
    return extrair_governador(PDF_REAL)


def test_extrai_todas_as_linhas_da_tabela_real(tabela_governador):
    assert len(tabela_governador) == 8
    assert [linha["posicao"] for linha in tabela_governador] == list(range(1, 9))
    assert [linha["nome"] for linha in tabela_governador] == [
        "SERGIO MORO",
        "REQUIÃO FILHO",
        "SANDRO ALEX",
        "LUIZ FRANÇA",
        "ADRIANO FUNILEIRO",
        "TAYNÁ MIESSA",
        "DOUTOR ALEXANDRE SALOMÃO",
        "SAMUEL DE MATTOS",
    ]


def test_extrai_partido_votos_e_percentuais(tabela_governador):
    assert tabela_governador[0] == {
        "posicao": 1,
        "nome": "SERGIO MORO",
        "partido": "PL",
        "votos": 1004,
        "porcentual": Decimal("50.0"),
        "porcentagem_valida": Decimal("51.8"),
        "porcentagem_acumulada": Decimal("51.8"),
    }
    assert tabela_governador[2]["votos"] == 385
    assert tabela_governador[2]["porcentagem_valida"] == Decimal("19.8")


def test_reconstroi_nome_e_partido_quebrados_em_linhas(tabela_governador):
    assert tabela_governador[6]["nome"] == "DOUTOR ALEXANDRE SALOMÃO"
    assert tabela_governador[6]["partido"] == "MOBILIZA"
    assert tabela_governador[6]["votos"] == 4


def test_extrai_tabela_de_presidente_por_localizacao_automatica():
    tabela = extrair_cargo(PDF_REAL, "PRESIDENTE")

    assert len(tabela) == 12
    assert tabela[0]["nome"] == "FLAVIO BOLSONARO"
    assert tabela[0]["partido"] == "PL"
    assert tabela[0]["votos"] == 1036
    assert tabela[0]["porcentual"] == Decimal("51.5")
    assert tabela[0]["porcentagem_valida"] == Decimal("54.6")


def test_presidente_reconstroi_nome_longo_quebrado():
    tabela = extrair_cargo(PDF_REAL, "PRESIDENTE")

    candidato = tabela[10]
    assert candidato["nome"] == "VETERINÁRIO WILSON GRASSI"
    assert candidato["partido"] == "DEMOCRATA"
    assert candidato["votos"] == 1


def test_extrai_metadados_e_linhas_de_ausentes():
    tabela = extrair_tabela(PDF_REAL, "GOVERNADOR")

    assert tabela.cargo == "GOVERNADOR"
    assert tabela.estado == "PARANÁ"
    assert "governador" in tabela.pergunta.lower()
    assert "paraná" in tabela.pergunta.lower()
    assert tabela.ns_nr == Decimal("2.3")
    assert tabela.brancos_nulos == Decimal("1.2")
    assert tabela.candidatos[0]["posicao"] == 1


def test_rejeita_pagina_que_nao_contem_tabela_de_governador():
    with pytest.raises(TabelaNaoEncontrada, match="não parece conter"):
        extrair_governador(PDF_REAL, pagina=4)


def test_rejeita_pagina_fora_do_pdf():
    with pytest.raises(TabelaNaoEncontrada, match="Página fora"):
        extrair_governador(PDF_REAL, pagina=999)


def test_rejeita_pdf_inexistente():
    with pytest.raises(TabelaNaoEncontrada, match="PDF não encontrado"):
        extrair_governador(Path("PDFs/nao-existe.pdf"))
