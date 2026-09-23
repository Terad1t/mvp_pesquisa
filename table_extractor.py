"""Protótipo determinístico para tabelas eleitorais textuais do PDF."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import pymupdf


class TabelaNaoEncontrada(ValueError):
    """O PDF não contém a tabela solicitada em formato textual reconhecível."""


@dataclass(frozen=True)
class TabelaExtraida:
    """Tabela reconstruída deterministicamente antes de qualquer IA."""

    cargo: str
    pergunta: str
    candidatos: list[dict[str, object]]
    ns_nr: Decimal
    brancos_nulos: Decimal
    estado: str


_PERCENTUAL = re.compile(r"^\d+(?:,\d+)?$")
_INTEIRO = re.compile(r"^\d+$")


def _decimal(texto: str) -> Decimal:
    return Decimal(texto.replace(",", "."))


def _normalizar_texto(texto: str) -> str:
    return " ".join(texto.replace("\xa0", " ").split())


def _percentual_ausente(page: Any, rotulo: str) -> Decimal:
    """Lê o percentual da linha NS/NR ou Branco/nulo."""
    words = page.get_text("words")
    alvo = rotulo.upper()
    marcador = next((word for word in words if word[4].strip().upper() == alvo), None)
    if marcador is None:
        raise TabelaNaoEncontrada(f"Linha {rotulo!r} não encontrada.")

    mesma_linha = [
        word[4].strip()
        for word in words
        if word[0] >= 280 and abs(word[1] - marcador[1]) <= 1.5
    ]
    percentuais = [word for word in mesma_linha if _PERCENTUAL.fullmatch(word)]
    if not percentuais:
        raise TabelaNaoEncontrada(f"Percentual de {rotulo} não encontrado.")
    return _decimal(percentuais[-1])


def _estado_da_pagina(doc: Any) -> str:
    texto = _normalizar_texto(doc[1].get_text("text")) if doc.page_count > 1 else ""
    correspondencia = re.search(r"Abrangência:\s*([A-Za-zÀ-ÿ -]+?)(?: Período|$)", texto)
    if correspondencia:
        return correspondencia.group(1).strip().upper()
    return ""


def _palavras_da_tabela(page: Any) -> list[tuple[float, float, str]]:
    """Retorna palavras da área de candidatos da pergunta encontrada."""
    words = page.get_text("words")
    # O cabeçalho termina perto de y=142. O limite inferior fica amplo porque
    # a quantidade de candidatos varia entre cargos.
    return [
        (word[0], word[1], word[4].strip())
        for word in words
        if 145 < word[1] < page.rect.height - 250
        and word[0] >= 90
    ]


def _localizar_pagina_cargo(doc: Any, cargo: str) -> int:
    """Retorna a página da primeira pergunta estimulada para o cargo."""
    cargo_normalizado = cargo.strip().upper()
    for indice, page in enumerate(doc):
        texto = page.get_text("text").upper()
        if cargo_normalizado in texto and "PERGUNTA" in texto and "ESTIMULADA" in texto:
            return indice
    raise TabelaNaoEncontrada(
        f"Nenhuma tabela estimulada de {cargo_normalizado} foi encontrada."
    )


def _localizar_consolidacao_senado(doc: Any) -> int:
    for indice, page in enumerate(doc):
        texto = page.get_text("text").upper()
        if "CONSOLIDAÇÃO" in texto and "PERGUNTAS" in texto and "PORCENTAGEM DE CASOS" in texto:
            return indice
    raise TabelaNaoEncontrada("Nenhuma tabela de consolidação do Senado foi encontrada.")


def extrair_cargo(
    caminho_pdf: Path, cargo: str, pagina: int | None = None
) -> list[dict[str, object]]:
    """Extrai a primeira tabela estimulada do cargo informado.

    Quando ``pagina`` é informado, usa essa página (numeração humana), o que
    facilita depuração de layouts conhecidos. Sem ele, procura a página pela
    pergunta, evitando depender da posição fixa no documento.

    Este é deliberadamente um protótipo específico para o layout observado em
    ``PDFs/pesquisa.pdf``. Ele falha explicitamente quando não consegue formar
    quatro campos numéricos por candidato, permitindo fallback posterior para
    o Gemini.
    """
    if not caminho_pdf.is_file():
        raise TabelaNaoEncontrada(f"PDF não encontrado: {caminho_pdf}")

    with pymupdf.open(caminho_pdf) as doc:
        if pagina is None:
            indice_pagina = _localizar_pagina_cargo(doc, cargo)
        else:
            if pagina < 1 or pagina > doc.page_count:
                raise TabelaNaoEncontrada(f"Página fora do PDF: {pagina}")
            indice_pagina = pagina - 1

        page = doc[indice_pagina]
        texto = page.get_text("text").upper()
        cargo_normalizado = cargo.strip().upper()
        if cargo_normalizado not in texto or "PERGUNTA" not in texto or "ESTIMULADA" not in texto:
            raise TabelaNaoEncontrada(
                f"A página não parece conter a tabela estimulada de {cargo_normalizado}."
            )

        words = _palavras_da_tabela(page)

    numeric_rows: dict[float, list[tuple[float, str]]] = {}
    for x, y, word in words:
        if _INTEIRO.fullmatch(word) or _PERCENTUAL.fullmatch(word):
            numeric_rows.setdefault(round(y, 1), []).append((x, word))

    rows: list[dict[str, object]] = []
    for y, numeric_words in sorted(numeric_rows.items()):
        numeric_words.sort()
        if len(numeric_words) != 4:
            continue

        frequencia, total, valido, acumulado = (word for _, word in numeric_words)
        if not _INTEIRO.fullmatch(frequencia) or not all(
            _PERCENTUAL.fullmatch(value) for value in (total, valido, acumulado)
        ):
            continue

        name_words = [
            word
            for x, word_y, word in words
            if 90 <= x < 280 and y - 12 <= word_y <= y + 18
        ]
        if not name_words:
            continue
        candidato = " ".join(name_words)
        if " - " not in candidato:
            continue
        nome, partido = candidato.rsplit(" - ", 1)
        if nome.upper() in {"TOTAL", "NS/NR", "BRANCO/NULO"}:
            continue

        rows.append(
            {
                "posicao": len(rows) + 1,
                "nome": nome.upper(),
                "partido": partido.upper(),
                "votos": int(frequencia),
                "porcentual": _decimal(total),
                "porcentagem_valida": _decimal(valido),
                "porcentagem_acumulada": _decimal(acumulado),
            }
        )

    if not rows:
        raise TabelaNaoEncontrada("Nenhuma linha de candidato foi reconstruída.")
    return rows


def extrair_tabela(caminho_pdf: Path, cargo: str, pagina: int | None = None) -> TabelaExtraida:
    """Extrai uma tabela completa, incluindo pergunta e percentuais ausentes."""
    if not caminho_pdf.is_file():
        raise TabelaNaoEncontrada(f"PDF não encontrado: {caminho_pdf}")

    with pymupdf.open(caminho_pdf) as doc:
        if pagina is None:
            indice_pagina = (
                _localizar_consolidacao_senado(doc)
                if cargo.strip().upper() == "SENADOR"
                else _localizar_pagina_cargo(doc, cargo)
            )
        else:
            if pagina < 1 or pagina > doc.page_count:
                raise TabelaNaoEncontrada(f"Página fora do PDF: {pagina}")
            indice_pagina = pagina - 1
        page = doc[indice_pagina]
        if cargo.strip().upper() == "SENADOR":
            return _extrair_tabela_senado(page, doc)

        linhas = page.get_text("text").splitlines()
        pergunta = _normalizar_texto(" ".join(linhas[:3]))
        candidatos = extrair_cargo(caminho_pdf, cargo, pagina=indice_pagina + 1)
        ns_nr = _percentual_ausente(page, "NS/NR")
        brancos_nulos = _percentual_ausente(page, "Branco/nulo")
        estado = _estado_da_pagina(doc)

    return TabelaExtraida(
        cargo=cargo.strip().upper(),
        pergunta=pergunta,
        candidatos=candidatos,
        ns_nr=ns_nr,
        brancos_nulos=brancos_nulos,
        estado=estado,
    )


def _extrair_tabela_senado(page: Any, doc: Any) -> TabelaExtraida:
    words = page.get_text("words")
    pergunta = _normalizar_texto(" ".join(page.get_text("text").splitlines()[:4]))
    linhas: dict[float, list[tuple[float, str]]] = {}
    for word in words:
        x, y, texto = word[0], round(word[1], 1), word[4].strip()
        if 150 < y < 405 and x >= 40:
            linhas.setdefault(y, []).append((x, texto))

    candidatos: list[dict[str, object]] = []
    ns_nr: Decimal | None = None
    brancos_nulos: Decimal | None = None
    for y, itens in sorted(linhas.items()):
        itens.sort()
        numeros = [
            (x, texto.rstrip("%"))
            for x, texto in itens
            if _INTEIRO.fullmatch(texto.rstrip("%"))
            or _PERCENTUAL.fullmatch(texto.rstrip("%"))
        ]
        if len(numeros) != 3:
            continue
        frequencia, _, casos = (texto for _, texto in numeros)
        nome_partido = " ".join(texto for x, texto in itens if 40 <= x < 300)
        nome_upper = nome_partido.upper()
        if nome_upper == "NS/NR":
            ns_nr = _decimal(casos)
            continue
        if nome_upper == "BRANCO/NULO":
            brancos_nulos = _decimal(casos)
            continue
        if nome_upper == "TOTAL" or " - " not in nome_partido:
            continue
        nome, partido = nome_partido.rsplit(" - ", 1)
        candidatos.append(
            {
                "posicao": len(candidatos) + 1,
                "nome": nome.upper(),
                "partido": partido.upper(),
                "votos": int(frequencia),
                "porcentual": _decimal(casos),
                "porcentagem_valida": _decimal(casos),
                "porcentagem_acumulada": _decimal(casos),
            }
        )

    if not candidatos or ns_nr is None or brancos_nulos is None:
        raise TabelaNaoEncontrada("Consolidação do Senado incompleta.")
    return TabelaExtraida(
        cargo="SENADOR",
        pergunta=pergunta,
        candidatos=candidatos,
        ns_nr=ns_nr,
        brancos_nulos=brancos_nulos,
        estado=_estado_da_pagina(doc),
    )


def extrair_governador(caminho_pdf: Path, pagina: int | None = None) -> list[dict[str, object]]:
    """Compatibilidade: extrai a tabela estimulada de Governador."""
    return extrair_cargo(caminho_pdf, "GOVERNADOR", pagina=pagina)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Inspeciona a tabela eleitoral extraída do PDF")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--cargo", default="GOVERNADOR")
    parser.add_argument(
        "--pagina",
        type=int,
        default=None,
        help="página humana para depuração; por padrão, localiza a tabela automaticamente",
    )
    args = parser.parse_args()

    for linha in extrair_cargo(args.pdf, args.cargo, pagina=args.pagina):
        print(linha)