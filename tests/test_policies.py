import pytest

from emporio.policies import PolicyIndex, consult_policies


@pytest.fixture(scope="module")
def index():
    return PolicyIndex.load()


@pytest.mark.parametrize(
    "question, section",
    [
        ("me arrependi da compra, posso devolver?", "4.1"),  # return rules
        ("o produto veio com defeito", "4.2"),  # colloquial wording, different from the manual
        ("vocês enviam para Dourados? qual o prazo?", "5.2"),  # city name absent from the manual
        ("qual o endereço da loja?", "1.2"),  # one-word query against a table-like section
        ("a promoção acabou?", "7.3"),  # expired-promotion rule
    ],
)
def test_customer_questions_find_the_right_section(index, question, section):
    top = [chunk.id for chunk, _ in index.search(question, k=2)]
    assert section in top


def test_tool_result_carries_notes_but_not_retrieval_keywords(index):
    result = consult_policies(index, "qual o telefone do whatsapp?", max_results=3)
    sections = {r["section"]: r["text"] for r in result["results"]}
    assert "Nota interna" in sections["7"] and "3321-4500" in sections["7"]  # phone conflict is flagged
    assert not any("<!--" in text for text in sections.values())


def test_unrelated_question_returns_nothing_instead_of_junk(index):
    result = consult_policies(index, "futebol campeonato brasileiro")
    assert result["results"] == []
    assert "Do not invent" in result["note"]
