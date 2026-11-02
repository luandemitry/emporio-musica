"""Convert the store policy manual (PDF) into a reviewable Markdown file.

    pip install -e ".[ingest]"
    python scripts/ingest_policies.py

The agent reads ``data/politicas_da_loja.md`` at runtime, so it never parses PDFs.
Keeping the Markdown in the repo lets anyone review exactly what the agent sees.

Layout cues from the PDF are used to rebuild the structure: numbered headings become
Markdown headings, wrapped bullet lines are joined, and paragraph breaks are detected
from the vertical gap between lines. Known problems in the manual are handled in the
``ERRATA`` / ``SECTION_NOTES`` tables below, and every change is printed when it runs.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pdfplumber

ROOT = Path(__file__).resolve().parents[1]
PDF_PATH = ROOT / "data" / "politicas_da_loja.pdf"
MD_PATH = ROOT / "data" / "politicas_da_loja.md"

# --- layout thresholds (PDF points), measured on this manual ----------------------- #
LEFT_MARGIN = 72.0
BULLET_INDENT = 100.0  # wrapped bullet lines start to the right of this
CELL_WRAP_GAP = 13.0  # a closer line is a wrapped table cell, not a new row
LINE_GAP = 17.5  # lines closer than this are the same paragraph

HEADING_H2 = re.compile(r"^(\d{1,2})\.\s+\S")  # "4. Política de Trocas e Devoluções"
HEADING_H3 = re.compile(r"^(\d{1,2}\.\d{1,2})\s+\S")  # "4.1 Direito de Arrependimento"
PAGE_NOISE = re.compile(r"^(Empório da Música Manual de Políticas e Procedimentos|Página \d+)$")
COVER_FOOTER = re.compile(r"^Empório da Música — Sua música começa aqui\.$")
VERSION_LINE = re.compile(r"^Última atualização:.*Versão.*$")

# --- fixes for text that is wrong or inconsistent in the manual -------------------- #
ERRATA = [
    # The domain can't contain an accent; the cover page has it without one.
    ("contato@empóriodamusica.com.br", "contato@emporiodamusica.com.br"),
]

# Internal notes for the model (never to be read out to customers). Each one is placed at the
# end of the content that belongs to that heading, so it travels with the chunk it concerns.
SECTION_NOTES = {
    "1": (
        "O catálogo real é o do sistema, não o texto acima: o manual cita mais de 300 itens, "
        "incluindo sopro e cordas orquestrais, mas hoje há bem menos produtos e algumas "
        "categorias estão vazias. Para saber o que a loja vende e a que preço, use sempre "
        "as ferramentas de catálogo."
    ),
    "1.2": (
        "O manual cita outro número de WhatsApp na seção 7, (67) 3321-4500. Informe "
        "(67) 3341-4444, que consta no cadastro da empresa; se o cliente precisar "
        "especificamente do número do WhatsApp, diga que vai confirmar com a equipe e "
        "encaminhe a um humano."
    ),
    "3": (
        "A tabela resume o cartão só pela faixa mais alta (parcela mínima de R$ 100,00); "
        "valem as faixas detalhadas de 3.1. Para calcular parcelas, use a ferramenta de "
        "simulação de pagamento em vez de fazer a conta de cabeça."
    ),
    "7": (
        "Inconsistência no manual: a seção 1.2 (e a capa) indicam (67) 3341-4444 como "
        "telefone/WhatsApp, mas este texto cita (67) 3321-4500. Informe (67) 3341-4444; se o "
        "cliente precisar especificamente do número do WhatsApp, diga que vai confirmar com "
        "a equipe e encaminhe a um humano."
    ),
}


# Retrieval keywords per heading: synonyms and the way customers actually phrase things.
# Written to the Markdown as an HTML comment (used to index the chunk, never shown to
# customers). Helps short questions about table-like sections ("qual o telefone?").
RETRIEVAL_KEYWORDS = {
    "1": "loja instrumentos musicais acessórios não vendemos cordas palhetas cabos cases pedais amplificadores o que vende escopo",
    "1.1": "missão valores sobre a empresa",
    "1.2": "endereço onde fica localização rua telefone whatsapp contato e-mail email cnpj razão social",
    "2": "horário funcionamento abre fecha aberto fechado sábado domingo feriado expediente atendimento",
    "3": "formas de pagamento pix boleto cartão crédito débito à vista aceita",
    "3.1": "parcelamento parcelar parcelas sem juros 12x parcela mínima combinar pagamentos",
    "4": "trocas devoluções política código de defesa do consumidor",
    "4.1": "arrependimento devolver devolução desistir cancelar compra 7 dias reembolso estorno dinheiro de volta",
    "4.2": "defeito de fabricação troca produto com defeito quebrado estragou problema 30 dias",
    "4.3": "troca por preferência trocar cor modelo tamanho",
    "4.4": "não pode trocar itens não elegíveis venda final personalização boquilha",
    "5.1": "frete entrega campo grande taxa grátis motoboy prazo região metropolitana",
    "5.2": "entrega outras cidades interior outra cidade fora de campo grande dourados três lagoas corumbá ponta porã correios pac sedex jadlog envio enviar frete cep prazo transportadora seguro avaria",
    "5.3": "rastreamento rastrear código de rastreio onde está meu pedido correios",
    "6.1": "promoções campanhas black friday aniversário da loja semana do músico queima de estoque volta às aulas",
    "6.2": "regras promoção desconto cumulativo pix promocional preço original rain check",
    "7": "whatsapp canal atendimento assistente virtual telefone",
    "7.1": "tom de voz tempo de resposta informações precisas escopo acessórios lojas parceiras",
    "7.2": "fluxo de atendimento passos saudação",
    "7.3": "situações especiais fora de estoque descontinuado promoção expirada vencida acabou reclamação retorno 24 horas",
    "8.1": "garantia legal 90 dias defeitos de fabricação",
    "8.2": "garantia do fabricante certificado prazo de garantia",
    "8.3": "o que não cobre a garantia mau uso queda desgaste",
    "9": "privacidade lgpd dados pessoais excluir meus dados compartilhamento",
    "10": "disposições finais manual atualizações gerência dúvidas",
}


def read_blocks(pdf_path: Path) -> tuple[list[tuple[str, str]], str]:
    """Return ``[(kind, text)]`` blocks (kind: h2, h3, bullet, text) and the version line."""
    blocks: list[tuple[str, str]] = []
    version = ""
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages[1:]:  # page 1 is the cover (its data repeats in section 1.2)
            prev_top: float | None = None
            for line in page.extract_text_lines():
                text = line["text"].strip()
                if VERSION_LINE.match(text):
                    version = text
                    continue
                if not text or PAGE_NOISE.match(text) or COVER_FOOTER.match(text):
                    continue
                x0, top = line["x0"], line["top"]
                gap = None if prev_top is None else top - prev_top
                prev_top = top

                at_margin = x0 == LEFT_MARGIN  # real headings sit at the margin; list items are indented
                if at_margin and HEADING_H3.match(text):
                    blocks.append(("h3", text))
                elif at_margin and HEADING_H2.match(text):
                    blocks.append(("h2", text))
                elif text.startswith("•"):
                    blocks.append(("bullet", text.lstrip("• ").strip()))
                elif not at_margin and re.match(r"^\d+\.\s", text):
                    blocks.append(("item", text))  # numbered list (e.g. the 7.2 service flow)
                elif blocks and blocks[-1][0] in ("bullet", "item") and x0 > BULLET_INDENT:
                    blocks[-1] = (blocks[-1][0], blocks[-1][1] + " " + text)  # wrapped list item
                elif blocks and gap is not None and gap < CELL_WRAP_GAP and blocks[-1][0] == "text":
                    blocks[-1] = ("text", blocks[-1][1] + " " + text)  # wrapped table cell
                elif (
                    blocks and gap is not None and gap <= LINE_GAP
                    and blocks[-1][0] == "text" and x0 == LEFT_MARGIN
                ):
                    blocks[-1] = ("text", blocks[-1][1] + " " + text)  # wrapped prose
                else:
                    blocks.append(("text", text))
    return blocks, version


def render(blocks: list[tuple[str, str]], version: str) -> str:
    out = [
        "# Manual de Políticas e Procedimentos — Empório da Música",
        "",
        f"> {version or 'Versão não identificada'}. Gerado a partir de `politicas_da_loja.pdf` "
        "por `scripts/ingest_policies.py`; não edite à mão.",
        "",
    ]
    current_id = ""

    def flush_note() -> None:
        note = SECTION_NOTES.get(current_id)
        if note:
            out.extend([f"> [Nota interna — não repetir ao cliente] {note}", ""])

    prev_kind = ""
    for kind, text in blocks:
        in_list = kind in ("bullet", "item")
        if prev_kind in ("bullet", "item") and not in_list:
            out.append("")  # blank line after a list
        if kind in ("h2", "h3"):
            flush_note()
            current_id = text.split()[0].rstrip(".")
            out.append(("## " if kind == "h2" else "### ") + text)
            if current_id in RETRIEVAL_KEYWORDS:
                out.append(f"<!-- keywords: {RETRIEVAL_KEYWORDS[current_id]} -->")
            out.append("")
        elif kind == "bullet":
            out.append(f"- {text}")
        elif kind == "item":
            out.append(text)
        else:
            out.extend([text, ""])
        prev_kind = kind
    if prev_kind in ("bullet", "item"):
        out.append("")
    flush_note()
    md = "\n".join(out).rstrip() + "\n"

    for wrong, right in ERRATA:
        if wrong in md:
            print(f"errata: {wrong!r} -> {right!r}", file=sys.stderr)
            md = md.replace(wrong, right)
    return md


def main() -> None:
    blocks, version = read_blocks(PDF_PATH)
    MD_PATH.write_text(render(blocks, version), encoding="utf-8")
    sections = sum(1 for kind, _ in blocks if kind in ("h2", "h3"))
    print(f"wrote {MD_PATH.relative_to(ROOT)} ({sections} headings)", file=sys.stderr)


if __name__ == "__main__":
    main()
