"""
Orquestrador: PDF -> extração (Governador+Senador+Presidente de uma vez) ->
validação -> cálculo -> validação -> saída + JSON combinado por estado.

Uso:
    python main.py PDFs/pesquisa.pdf
    python main.py PDFs/pesquisa.pdf --cargo GOVERNADOR   # só processa um cargo
    python main.py --json exemplo.json                    # testa fases 2-4 sem gastar API

Depois de rodar com um PDF (não com --json), o arquivo é movido para uma
subpasta de PDFs/ de acordo com o resultado — ver _mover_pdf_processado.

O bloqueio de validação é POR CARGO, não por PDF inteiro: se Governador não
fechar mas Presidente fechar, Presidente continua liberado — só Governador
fica marcado como bloqueado. O PDF só vai inteiro para PDFs/bloqueados/
porque a revisão humana normalmente olha o documento todo de uma vez, não
porque os outros cargos também estejam com problema.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv

from calculator import CargoNaoSuportado, calcular
from extractor import ErroDeExtracao, ErroTransitorio, ResultadoExtracao, extrair, extrair_de_json
from schemas import CARGOS_ESPERADOS, CargoExtraido, PesquisaFinal
from table_extractor import TabelaNaoEncontrada, extrair_intencoes_senado, extrair_tabela
from validator import Nivel, Ocorrencia, tem_erro, validar_extracao, validar_resultado

LARGURA = 40
PASTA_SAIDAS = Path("saidas")
_CARGOS_PARSER = {"GOVERNADOR", "PRESIDENTE", "SENADOR"}


def _resultado_do_parser(caminho_pdf: Path, cargo: str) -> ResultadoExtracao:
    tabela = extrair_tabela(caminho_pdf, cargo)
    candidatos = [
        {
            "posicao": candidato["posicao"],
            "nome": candidato["nome"],
            "partido": candidato["partido"],
            "votos": candidato["votos"],
            "porcentual": candidato["porcentual"],
            "porcentagem_valida": candidato["porcentagem_valida"],
        }
        for candidato in tabela.candidatos
    ]
    bloco = CargoExtraido.model_validate(
        {
            "cargo": tabela.cargo,
            "pergunta": tabela.pergunta,
            "origem": "parser",
            "candidatos": candidatos,
            "ns_nr": tabela.ns_nr,
            "brancos_nulos": tabela.brancos_nulos,
        }
    )
    intencoes = []
    if cargo.strip().upper() == "SENADOR":
        intencoes = [_bloco_da_tabela(item) for item in extrair_intencoes_senado(caminho_pdf)]
    return ResultadoExtracao(estado=tabela.estado, cargos=[bloco], senado_intencoes=intencoes)


def _bloco_da_tabela(tabela) -> CargoExtraido:
    return CargoExtraido.model_validate(
        {
            "cargo": tabela.cargo,
            "pergunta": tabela.pergunta,
            "origem": "parser",
            "candidatos": [
                {
                    "posicao": candidato["posicao"],
                    "nome": candidato["nome"],
                    "partido": candidato["partido"],
                    "votos": candidato["votos"],
                    "porcentual": candidato["porcentual"],
                    "porcentagem_valida": candidato["porcentagem_valida"],
                }
                for candidato in tabela.candidatos
            ],
            "ns_nr": tabela.ns_nr,
            "brancos_nulos": tabela.brancos_nulos,
        }
    )


def _extrair_hibrido(caminho_pdf: Path, cargo_filtro: str | None) -> ResultadoExtracao:
    """Tenta o parser para cargos suportados; Gemini continua sendo fallback."""
    alvo = cargo_filtro.strip().upper() if cargo_filtro else None
    if alvo in _CARGOS_PARSER:
        try:
            print(f"→ Parser determinístico: tentando {alvo}...")
            return _resultado_do_parser(caminho_pdf, alvo)
        except (TabelaNaoEncontrada, ValueError) as erro:
            print(f" [!] Parser não conseguiu {alvo}: {erro}")
            print("→ Fallback: enviando o PDF ao Gemini.")
            resultado = extrair(caminho_pdf)
            resultado.avisos.insert(0, f"{alvo}: fallback para Gemini ({erro})")
            return resultado
    elif alvo is None:
        # Se todas as tabelas suportadas forem reconstruídas, não há motivo
        # para chamar a API. Se alguma falhar, Gemini completa o documento.
        parser_blocos: dict[str, CargoExtraido] = {}
        senado_intencoes: list[CargoExtraido] = []
        estado_parser = ""
        avisos_parser: list[str] = []
        for cargo in sorted(_CARGOS_PARSER):
            try:
                parcial = _resultado_do_parser(caminho_pdf, cargo)
                parser_blocos[cargo] = parcial.cargos[0]
                if cargo == "SENADOR":
                    senado_intencoes = parcial.senado_intencoes
                estado_parser = estado_parser or parcial.estado
                print(f"✓ Parser determinístico: {cargo} extraído.")
            except (TabelaNaoEncontrada, ValueError) as erro:
                print(f" [!] Parser não conseguiu {cargo}: {erro}")
                avisos_parser.append(f"{cargo}: fallback para Gemini ({erro})")
        if parser_blocos.keys() == set(CARGOS_ESPERADOS):
            return ResultadoExtracao(
                estado=estado_parser,
                cargos=list(parser_blocos.values()),
                senado_intencoes=senado_intencoes,
            )
        print("→ Fallback: usando Gemini para o documento inteiro e cargos restantes.")
        resultado_gemini = extrair(caminho_pdf)
        cargos = [parser_blocos.get(bloco.cargo, bloco) for bloco in resultado_gemini.cargos]
        presentes = {bloco.cargo for bloco in cargos}
        cargos.extend(bloco for cargo, bloco in parser_blocos.items() if cargo not in presentes)
        return ResultadoExtracao(
            estado=resultado_gemini.estado or estado_parser,
            cargos=cargos,
            avisos=avisos_parser + resultado_gemini.avisos,
            senado_intencoes=senado_intencoes or resultado_gemini.senado_intencoes,
        )
    resultado = extrair(caminho_pdf)
    if alvo:
        resultado.avisos.insert(
            0,
            f"{alvo}: parser determinístico não cobre este cargo; fallback para Gemini",
        )
    return resultado


def _mostrar_ocorrencias(ocorrencias: list[Ocorrencia], etapa: str) -> None:
    if not ocorrencias:
        return
    print(f"\n--- {etapa} ---")
    for o in ocorrencias:
        marca = "x" if o.nivel is Nivel.ERRO else "!"
        print(f" [{marca}] {o.nivel.value}: {o.mensagem}")


def renderizar(p: PesquisaFinal) -> str:
    linhas = [
        "=" * LARGURA,
        f"{p.cargo} — {p.estado}",
        "=" * LARGURA,
        "",
    ]
    for i, c in enumerate(p.candidatos, start=1):
        linhas += [
            f"{i}. {c.nome} — {c.partido}",
            f"   Total:   {c.porcentual}%",
            f"   Válidos: {c.porcentagem_valida}%",
            "",
        ]
    linhas += [
        f"NS/NR: {p.ns_nr}%",
        f"Brancos/Nulos: {p.brancos_nulos}%",
        "",
        f"Outros válidos: {p.outros_valido}%",
        f"Outros total:   {p.outros_total}%",
    ]
    return "\n".join(linhas)


def _mover_pdf_processado(caminho_pdf: Path, exit_code: int) -> None:
    """Organiza o PDF de acordo com o que aconteceu, para o próximo run não
    reprocessar o que já foi resolvido nem perder de vista o que precisa de
    atenção humana.

    Três destinos possíveis, dois motivos para NÃO mover:

        exit 0            -> PDFs/analisados/   (todos os cargos processados
                                                   com sucesso)
        exit 1 (validação) -> PDFs/bloqueados/   (pelo menos um cargo não
                                                   fechou — revisão humana
                                                   decide se o PDF tem
                                                   problema real ou se é a
                                                   tolerância que está curta)
        exit 2 (extração)  -> PDFs/falhos/       (nem chegou a extrair —
                                                   arquivo corrompido, formato
                                                   inesperado etc.)
        exit 3 (transitório) -> fica onde está   (Gemini sobrecarregado; o
                                                   próximo run deve tentar de
                                                   novo sozinho, sem
                                                   intervenção)
    """
    destinos = {0: "analisados", 1: "bloqueados", 2: "falhos"}
    nome_pasta = destinos.get(exit_code)
    if nome_pasta is None:
        return  # exit 3 (transitório): permanece no lugar para retry

    pasta_destino = caminho_pdf.parent / nome_pasta
    pasta_destino.mkdir(parents=True, exist_ok=True)

    destino = pasta_destino / caminho_pdf.name
    if destino.exists():
        contador = 1
        while destino.exists():
            destino = pasta_destino / f"{caminho_pdf.stem}_{contador}{caminho_pdf.suffix}"
            contador += 1

    shutil.move(str(caminho_pdf), str(destino))
    print(f"\n→ PDF movido para {destino.relative_to(caminho_pdf.parent.parent)}")


def _processar_cargo(
    bloco: CargoExtraido, estado: str, top_n: int
) -> tuple[str, PesquisaFinal | None, list[Ocorrencia]]:
    """Roda validação + cálculo + validação para UM bloco de cargo.

    Devolve (status, resultado, ocorrências), onde status é um destes:
        "ok"        -> resultado é um PesquisaFinal pronto para a arte
        "bloqueado" -> não fechou a validação; resultado é None
        "pulado"    -> cargo sem regra de cálculo implementada; resultado é
                        None, mas os dados brutos continuam em `bloco` para
                        quem chamou decidir o que fazer com eles
    """
    ocorrencias = validar_extracao(bloco)
    if tem_erro(ocorrencias):
        return "bloqueado", None, ocorrencias

    try:
        final = calcular(bloco, estado=estado, top_n=top_n)
    except (CargoNaoSuportado, NotImplementedError):
        return "pulado", None, ocorrencias

    pos = validar_resultado(final)
    if tem_erro(pos):
        return "bloqueado", None, ocorrencias + pos

    return "ok", final, ocorrencias + pos


def processar(
    resultado: ResultadoExtracao,
    top_n: int = 3,
    cargo_filtro: str | None = None,
    nome_saida: str = "pesquisa.json",
    on_cargo: Callable[[str, str, list[Ocorrencia]], None] | None = None,
) -> int:
    """Fases 2 a 4, para todos os cargos extraídos do PDF. Devolve o exit code."""
    for aviso in resultado.avisos:
        print(f" [!] Cargo não extraído corretamente: {aviso}")

    blocos = resultado.cargos
    if cargo_filtro:
        alvo = cargo_filtro.strip().upper()
        blocos = [b for b in blocos if b.cargo == alvo]
        if not blocos:
            print(f"\n⚠ Cargo {alvo!r} não foi encontrado na extração deste PDF.")
            return 1

    houve_bloqueio = False
    houve_sucesso = False
    finais: dict[str, PesquisaFinal] = {}
    pendentes: set[str] = set()  # extraído, mas sem regra de cálculo ainda
    faltando: set[str] = set()   # nem chegou a ser extraído — problema, não "esperado"

    if cargo_filtro is None:
        # Você confirmou que os três cargos sempre vêm juntos no mesmo PDF.
        # Se um sumiu, é mais provável que o Gemini tenha falhado em
        # reconhecê-lo do que a pesquisa genuinamente não tê-lo pesquisado —
        # e "assumir que não tinha mesmo" é exatamente o tipo de suposição
        # silenciosa que pode colocar um estado sem dado de Senado no ar sem
        # ninguém perceber. Por isso isso bloqueia o PDF, mesmo que os cargos
        # presentes estejam perfeitos.
        encontrados = {b.cargo for b in resultado.cargos}
        faltando = set(CARGOS_ESPERADOS) - encontrados
        if faltando:
            houve_bloqueio = True
            print(
                f"\n⚠ ERRO DE VALIDAÇÃO: cargo(s) ausente(s) na extração: {', '.join(sorted(faltando))}."
            )
            print(
                "   Governador, Senador e Presidente deveriam vir juntos neste PDF — "
                "revisão humana necessária antes de confiar nos cargos que vieram."
            )

    for bloco in blocos:
        if on_cargo:
            on_cargo(bloco.cargo, "processando", [])
        status, final, ocorrencias = _processar_cargo(bloco, resultado.estado, top_n)
        if on_cargo:
            on_cargo(bloco.cargo, status, ocorrencias)
        _mostrar_ocorrencias(ocorrencias, f"Validação — {bloco.cargo}")

        if status == "bloqueado":
            houve_bloqueio = True
            print(f"\n⚠ {bloco.cargo}: ERRO DE VALIDAÇÃO — geração da arte bloqueada para este cargo.")
            continue

        if status == "pulado":
            pendentes.add(bloco.cargo)
            print(f"\nℹ {bloco.cargo}: extraído, mas sem regra de cálculo implementada ainda.")
            print(f"   Candidatos brutos: {', '.join(c.nome for c in bloco.candidatos)}")
            continue

        houve_sucesso = True
        finais[bloco.cargo] = final
        print()
        print(renderizar(final))
        print("\n✓ VALIDAÇÃO OK" if not ocorrencias else "\n✓ VALIDAÇÃO OK (com alertas acima)")

    if finais:
        _salvar_json_combinado(resultado, finais, faltando=faltando, pendentes=pendentes, nome_arquivo=nome_saida)

    if houve_bloqueio:
        return 1
    if not houve_sucesso:
        print("\n⚠ Nenhum cargo pôde ser calculado (todos pulados ou sem dado).")
        return 1
    return 0


def _salvar_json_combinado(
    resultado: ResultadoExtracao,
    finais: dict[str, PesquisaFinal],
    faltando: set[str] | None = None,
    pendentes: set[str] | None = None,
    nome_arquivo: str = "pesquisa.json",
) -> Path:
    """Escreve saidas/{ESTADO}/{arquivo}.json com todos os cargos calculados
    com sucesso deste PDF — pronto para o próximo passo (integração com o
    PesquisaPRO), que organiza a geração de arte também por estado.

    Cargos bloqueados por validação ficam de fora de propósito: a regra do
    projeto é nunca deixar dado que não fechou virar insumo de arte
    automaticamente.

    Um cargo pode estar ausente de `cargos` por dois motivos bem diferentes,
    e o JSON precisa distinguir os dois para quem for consumi-lo depois:

        cargos_faltando  -> nem foi extraído do PDF. Sinal de problema —
                             "completo": false, revisão humana.
        cargos_pendentes -> foi extraído e validado estruturalmente, só não
                             tem fórmula de cálculo ainda. É esperado, não é
                             um erro — por isso NÃO derruba "completo" para
                             false.

    Sem essa distinção, um consumidor futuro (o robô que alimenta o site,
    por exemplo) não teria como saber se "Senado não está aqui" é normal ou
    é um problema que precisa de atenção.
    """
    pasta = PASTA_SAIDAS / resultado.estado.replace(" ", "_")
    pasta.mkdir(parents=True, exist_ok=True)

    # No modo --pasta (lote), nome_arquivo já vem específico do PDF
    # ("<stem>.json"), e é AQUI — antes de escrever, não depois — que
    # evitamos colidir com outro PDF do mesmo estado já processado neste
    # lote. Fazer essa checagem depois da escrita (como uma renomeação
    # posterior) é frágil: um PDF que termina bloqueado (código != 0) ainda
    # assim pode ter cargos em `finais` e escrever aqui, e se esse passo de
    # renomear só rodasse para código 0, o arquivo ficaria com o nome fixo
    # "pesquisa.json" e seria sobrescrito silenciosamente pelo próximo PDF
    # do mesmo estado no lote — perda de dado sem aviso nenhum.
    #
    # No modo de um PDF só, nome_arquivo continua sendo "pesquisa.json" de
    # propósito, e reprocessar o MESMO PDF deve sobrescrever — não crescer
    # pesquisa_1.json, pesquisa_2.json a cada nova tentativa.
    if nome_arquivo != "pesquisa.json":
        nome_arquivo = _nome_saida_disponivel(pasta, nome_arquivo)

    saida = {
        "estado": resultado.estado,
        "avisos": resultado.avisos,
        "completo": not bool(faltando),
        "cargos": {cargo: json.loads(final.model_dump_json()) for cargo, final in finais.items()},
    }
    if resultado.senado_intencoes and "SENADOR" in saida["cargos"]:
        saida["cargos"]["SENADOR"]["intencoes"] = [
            json.loads(bloco.model_dump_json()) for bloco in resultado.senado_intencoes
        ]
    if faltando:
        saida["cargos_faltando"] = sorted(faltando)
    if pendentes:
        saida["cargos_pendentes"] = sorted(pendentes)

    destino = pasta / nome_arquivo
    destino.write_text(json.dumps(saida, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n→ JSON combinado salvo em {destino}")
    return destino


def _processar_pdf(
    caminho_pdf: Path,
    top_n: int,
    cargo_filtro: str | None,
    nome_saida: str = "pesquisa.json",
) -> int:
    """Processa um PDF e preserva o código de saída das fases existentes."""
    try:
        resultado = _extrair_hibrido(caminho_pdf, cargo_filtro)
    except ErroTransitorio as erro:
        print(f"⏳ {erro}", file=sys.stderr)
        return 3
    except ErroDeExtracao as erro:
        print(f"⚠ Falha na extração: {erro}", file=sys.stderr)
        _mover_pdf_processado(caminho_pdf, exit_code=2)
        return 2

    codigo = processar(resultado, top_n=top_n, cargo_filtro=cargo_filtro, nome_saida=nome_saida)
    _mover_pdf_processado(caminho_pdf, exit_code=codigo)
    return codigo


def _arquivos_pdf(pasta: Path) -> list[Path]:
    if not pasta.is_dir():
        raise ValueError(f"Pasta de PDFs não encontrada: {pasta}")
    return sorted(caminho for caminho in pasta.glob("*.pdf") if caminho.is_file())


def _nome_saida_disponivel(pasta: Path, nome_arquivo: str) -> str:
    destino = pasta / nome_arquivo
    if not destino.exists():
        return nome_arquivo

    contador = 1
    while True:
        candidato = pasta / f"{destino.stem}_{contador}{destino.suffix}"
        if not candidato.exists():
            return candidato.name
        contador += 1


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(description="MVP de extração de pesquisas Veritá")
    parser.add_argument("pdf", nargs="?", type=Path, help="caminho do PDF")
    parser.add_argument("--pasta", type=Path, help="processa todos os PDFs diretamente nesta pasta")
    parser.add_argument(
        "--cargo",
        default=None,
        help="processa só este cargo (GOVERNADOR, SENADOR ou PRESIDENTE); padrão: todos os extraídos",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=3,
        help="quantos candidatos mais votados exibir individualmente; o resto vira 'Outros' (padrão: 3)",
    )
    parser.add_argument("--json", type=Path, help="usa um JSON já extraído, sem chamar a API")
    args = parser.parse_args()

    escolhas = sum(bool(valor) for valor in (args.pdf, args.pasta, args.json))
    if escolhas != 1:
        parser.error("informe exatamente um PDF, --pasta ou --json")

    if args.json:
        try:
            resultado = extrair_de_json(args.json)
        except ErroDeExtracao as erro:
            print(f"⚠ Falha na extração: {erro}", file=sys.stderr)
            return 2
        return processar(resultado, top_n=args.top_n, cargo_filtro=args.cargo)

    if args.pasta:
        try:
            arquivos = _arquivos_pdf(args.pasta)
        except ValueError as erro:
            parser.error(str(erro))
        if not arquivos:
            print(f"Nenhum PDF encontrado em {args.pasta}.", file=sys.stderr)
            return 2

        codigos = []
        for caminho_pdf in arquivos:
            print(f"\n{'#' * LARGURA}\nPDF: {caminho_pdf}\n{'#' * LARGURA}")
            nome_saida = f"{caminho_pdf.stem}.json"
            codigos.append(_processar_pdf(caminho_pdf, args.top_n, args.cargo, nome_saida))
        return max(codigos)

    return _processar_pdf(args.pdf, args.top_n, args.cargo)


if __name__ == "__main__":
    raise SystemExit(main())