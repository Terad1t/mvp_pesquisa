"""
Orquestrador: PDF -> extração -> validação -> cálculo -> validação -> saída.

Uso:
    python main.py PDFs/pesquisa.pdf
    python main.py PDFs/pesquisa.pdf --cargo SENADOR
    python main.py --json exemplo.json        # testa fases 2-4 sem gastar API
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from calculator import CargoNaoSuportado, calcular
from extractor import ErroDeExtracao, extrair, extrair_de_json
from schemas import PesquisaExtraida, PesquisaFinal
from validator import Nivel, Ocorrencia, tem_erro, validar_extracao, validar_resultado

LARGURA = 40


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
        "PESQUISA EXTRAÍDA",
        "=" * LARGURA,
        "",
        f"Estado: {p.estado}",
        f"Cargo: {p.cargo}",
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


def processar(pesquisa: PesquisaExtraida) -> int:
    """Fases 2 a 4. Devolve o exit code."""
    ocorrencias = validar_extracao(pesquisa)
    _mostrar_ocorrencias(ocorrencias, "Validação da extração")
    if tem_erro(ocorrencias):
        print("\n⚠ ERRO DE VALIDAÇÃO")
        print("Os dados extraídos não são consistentes.")
        print("A geração da arte foi bloqueada. Revisão humana necessária.")
        return 1

    try:
        final = calcular(pesquisa)
    except (CargoNaoSuportado, NotImplementedError) as erro:
        print(f"\n⚠ {erro}")
        return 1

    pos = validar_resultado(final)
    _mostrar_ocorrencias(pos, "Validação do resultado")
    if tem_erro(pos):
        print("\n⚠ ERRO DE VALIDAÇÃO")
        print("O cálculo produziu valores inconsistentes.")
        print("A geração da arte foi bloqueada. Revisão humana necessária.")
        return 1

    print()
    print(renderizar(final))
    print()
    print("✓ VALIDAÇÃO OK" if not pos else "✓ VALIDAÇÃO OK (com alertas acima)")
    return 0


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(description="MVP de extração de pesquisas Veritá")
    parser.add_argument("pdf", nargs="?", type=Path, help="caminho do PDF")
    parser.add_argument("--cargo", default="GOVERNADOR", help="cargo a extrair")
    parser.add_argument("--json", type=Path, help="usa um JSON já extraído, sem chamar a API")
    args = parser.parse_args()

    if not args.pdf and not args.json:
        parser.error("informe um PDF ou --json")

    try:
        pesquisa = extrair_de_json(args.json) if args.json else extrair(args.pdf, cargo=args.cargo)
    except ErroDeExtracao as erro:
        print(f"⚠ Falha na extração: {erro}", file=sys.stderr)
        return 2

    return processar(pesquisa)


if __name__ == "__main__":
    raise SystemExit(main())