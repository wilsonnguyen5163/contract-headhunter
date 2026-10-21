import pytest

from conftest import posting
from jobmatch.normalize.phrases import PhraseMatcher, fold
from jobmatch.normalize.text import clean_text, html_to_text, looks_like_html, to_plain_text

# ---- phrase matching ----


def test_fold_strips_case_accents_and_curly_apostrophes() -> None:
    assert fold("Développeur’s CÔTÉ") == "developpeur's cote"


def test_whole_words_only_java_is_not_javascript() -> None:
    matcher = PhraseMatcher.from_terms(["java"])
    assert matcher.values("JavaScript developer") == []
    assert matcher.values("Java 17 and Spring") == ["java"]


def test_hyphen_and_space_are_interchangeable() -> None:
    matcher = PhraseMatcher.from_terms(["full-stack", "durée déterminée"])
    assert matcher.values("Full Stack dev, contrat à duree-determinee") == [
        "full-stack",
        "durée déterminée",
    ]


def test_longest_phrase_wins_and_plural_is_optional() -> None:
    matcher = PhraseMatcher.from_terms(["contract", "contract role"], allow_plural=True)
    assert matcher.values("Two contract roles and other contracts") == ["contract role", "contract"]


def test_symbols_inside_terms() -> None:
    matcher = PhraseMatcher({"c#": "c#", ".net core": ".net", "asp.net": ".net", "c++": "c++"})
    assert matcher.values("C#/.NET Core, ASP.NET and C++") == ["c#", ".net", "c++"]


def test_remove_blanks_out_matches() -> None:
    matcher = PhraseMatcher.from_terms(["toronto"])
    assert matcher.remove("Toronto, ON").strip() == ", on"


# ---- text cleanup ----


def test_html_blocks_become_lines_and_list_items_bullets() -> None:
    html = "<p>We need <strong>Python</strong></p><ul><li>One</li><li>Two</li></ul>"
    lines = [line for line in clean_text(html_to_text(html)).split("\n") if line]
    assert lines == ["We need Python", "• One", "• Two"]


def test_list_items_wrapping_paragraphs_keep_bullet_and_text_together() -> None:
    html = "<ul><li><p>Leads a team of producers</p></li><li><p>Tracks spending</p></li></ul>"
    lines = [line for line in to_plain_text(html).split("\n") if line]
    assert lines == ["• Leads a team of producers", "• Tracks spending"]


def test_lone_bullet_lines_from_a_copied_page_are_joined() -> None:
    assert (
        clean_text("•\n\nManage budgets\n-\nWrite reports") == "• Manage budgets\n- Write reports"
    )


def test_scripts_and_styles_are_dropped() -> None:
    text = to_plain_text(posting("en_contract_data_engineer.html"))
    assert "alert" not in text
    assert "Senior Python Data Engineer" in text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Ã‰quipe de dÃ©veloppement", "Équipe de développement"),  # UTF-8 read as cp1252
        ("a b c", "a b c"),  # non-breaking and em spaces
        ("Line 1\r\n\r\n\r\n\r\nLine 2", "Line 1\n\nLine 2"),
        ("  trailing   spaces  \n", "trailing spaces"),
    ],
)
def test_clean_text(raw: str, expected: str) -> None:
    assert clean_text(raw) == expected


def test_plain_text_with_angle_brackets_is_not_html() -> None:
    assert not looks_like_html("Salary 90 < 110 $/h and team > 5")
    assert looks_like_html("<p>Hello</p>")
