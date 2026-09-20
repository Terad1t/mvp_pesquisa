# MVP — Automação de Pesquisas Veritá

Princípio: **IA interpreta, código decide.** O Gemini transcreve o PDF; o Python
calcula, valida e bloqueia.

## Instalação (uv)

```bash
uv sync                   # cria .venv e resolve tudo a partir do pyproject.toml
cp .env.example .env      # preencha GEMINI_API_KEY
```

Não é preciso ativar a venv: `uv run` já executa dentro dela.

## Uso

```bash
# Fases 2–4 sem gastar chamada de API (fixture do briefing)
uv run main.py --json exemplo_parana.json

# Fase 1 completa, com PDF real
uv run main.py PDFs/pesquisa.pdf --cargo GOVERNADOR
```

Para adicionar dependências, use `uv add <pacote>` — ele atualiza o
`pyproject.toml` e o `uv.lock` juntos. Editar o `pyproject.toml` à mão e
esquecer o lock é a forma mais comum de os dois saírem de sincronia.

Se algum dia precisar de um `requirements.txt` (deploy antigo, CI que não tem
uv), gere a partir do lock em vez de manter os dois à mão:

```bash
uv export --no-dev --format requirements-txt > requirements.txt
```

Exit codes: `0` validado, `1` bloqueado por validação, `2` falha de extração.
Isso importa quando o robô virar job agendado — o orquestrador precisa
distinguir "dados ruins" de "API fora do ar".

## Arquivos

| Arquivo | Responsabilidade |
|---|---|
| `extractor.py` | Único módulo que fala com a rede. PDF → `PesquisaExtraida` |
| `schemas.py` | Contratos. Separa o que o Gemini pode dizer do que o Python produz |
| `calculator.py` | Só matemática, funções puras. Registro de regras por cargo |
| `validator.py` | Barreira de segurança. Devolve ocorrências, não booleano |
| `main.py` | Orquestração e saída no terminal |
| `pyproject.toml` | Dependências declaradas (o que você pediu) |
| `uv.lock` | Versões exatas resolvidas (o que você recebeu) — **commitar** |

## Estado atual

- Governador implementado e conferido contra o exemplo do briefing (4.1 / 3.9).
- Senado levanta `NotImplementedError` de propósito: base ~200%, dois votos por
  eleitor. Reaproveitar a fórmula de Governador daria "Outros" negativo.
- Sem geração de arte, sem PesquisaPRO, sem browser. Conforme combinado.

## Próximo passo

Rodar com um PDF real da Veritá e comparar o JSON devolvido com o que está
impresso no documento, campo a campo.