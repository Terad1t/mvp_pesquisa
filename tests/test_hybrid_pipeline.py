from pathlib import Path

from main import _arquivos_pdf, _extrair_hibrido, _nome_saida_disponivel
from main import _extrair_hibrido
from extractor import ResultadoExtracao
from schemas import CargoExtraido
from validator import Nivel, validar_extracao


PDF_REAL = Path(__file__).parents[1] / "PDFs" / "pesquisa.pdf"


def test_parser_entra_no_contrato_principal_para_governador():
    resultado = _extrair_hibrido(PDF_REAL, "GOVERNADOR")
    bloco = resultado.cargos[0]

    assert resultado.estado == "PARANÁ"
    assert isinstance(bloco, CargoExtraido)
    assert bloco.candidatos[0].posicao == 1
    assert bloco.candidatos[0].votos == 1004
    assert "governador" in bloco.pergunta.lower()
    assert validar_extracao(bloco) == []


def test_parser_entra_no_contrato_principal_para_presidente():
    resultado = _extrair_hibrido(PDF_REAL, "PRESIDENTE")
    bloco = resultado.cargos[0]

    assert bloco.cargo == "PRESIDENTE"
    assert len(bloco.candidatos) == 12
    assert bloco.candidatos[0].posicao == 1
    assert bloco.candidatos[0].votos == 1036


def test_cargo_sem_parser_delega_para_gemini(monkeypatch):
    esperado = object()

    def fake_extrair(caminho):
        assert caminho == PDF_REAL
        return esperado

    monkeypatch.setattr("main.extrair", fake_extrair)

    assert _extrair_hibrido(PDF_REAL, "SENADOR") is esperado


    def test_modo_completo_prefere_parser_nos_cargos_deterministicos(monkeypatch):
        gemini = CargoExtraido.model_validate(
            {
                "cargo": "GOVERNADOR",
                "candidatos": [
                    {"nome": "IA", "partido": "XX", "porcentual": 10, "porcentagem_valida": 10}
                ],
                "ns_nr": 1,
                "brancos_nulos": 1,
            }
        )

        monkeypatch.setattr(
            "main.extrair",
            lambda caminho: ResultadoExtracao(estado="PARANÁ", cargos=[gemini]),
        )

        resultado = _extrair_hibrido(PDF_REAL, None)
        governador = next(bloco for bloco in resultado.cargos if bloco.cargo == "GOVERNADOR")

        assert governador.candidatos[0].nome == "SERGIO MORO"
        assert governador.candidatos[0].posicao == 1


def test_posicao_duplicada_e_faltante_bloqueiam():
    bloco = CargoExtraido.model_validate(
        {
            "cargo": "GOVERNADOR",
            "candidatos": [
                {"posicao": 1, "nome": "A", "partido": "AA", "porcentual": 10, "porcentagem_valida": 10},
                {"posicao": 2, "nome": "B", "partido": "BB", "porcentual": 10, "porcentagem_valida": 10},
                {"posicao": 2, "nome": "C", "partido": "CC", "porcentual": 10, "porcentagem_valida": 10},
            ],
            "ns_nr": 1,
            "brancos_nulos": 1,
        }
    )

    ocorrencias = validar_extracao(bloco)
    mensagens = [ocorrencia.mensagem for ocorrencia in ocorrencias if ocorrencia.nivel is Nivel.ERRO]

    assert any("Posição repetida" in mensagem for mensagem in mensagens)
    assert any("Posição faltante" in mensagem for mensagem in mensagens)


def test_lista_somente_pdfs(tmp_path):
    (tmp_path / "b.pdf").write_bytes(b"")
    (tmp_path / "a.pdf").write_bytes(b"")
    (tmp_path / "notas.txt").write_text("ignorar")

    assert _arquivos_pdf(tmp_path) == [tmp_path / "a.pdf", tmp_path / "b.pdf"]


def test_nome_de_saida_nao_sobrescreve_arquivo_existente(tmp_path):
    (tmp_path / "parana.json").write_text("{}")
    (tmp_path / "parana_1.json").write_text("{}")

    assert _nome_saida_disponivel(tmp_path, "parana.json") == "parana_2.json"
