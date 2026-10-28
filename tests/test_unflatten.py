"""Flattened posting text (normalize/unflatten.py). Synthetic text in the style of
Workday's one-line descriptions, in English and French."""

from datetime import UTC, datetime

from conftest import CONFIG_DIR
from jobmatch.config import load_normalization_rules
from jobmatch.normalize.pipeline import Normalizer
from jobmatch.normalize.unflatten import Unflattener
from jobmatch.schemas import RawJob

UNFLATTEN = Unflattener(load_normalization_rules(CONFIG_DIR))

EN = (
    "Example Co is a leader in legal software for firms of every size across Canada and beyond. "
    "Summary: We are seeking a Senior Customer Marketing Manager to join our team in Toronto. "
    "This is a 12 month fixed term contract. Who you are: You are a builder who thinks in "
    "systems, not just campaigns, and you know lifecycle marketing well. What you'll work on: "
    "Architecting journey frameworks from onboarding through expansion with clear triggers. "
    "Planning and running webinars and community posts to drive adoption; partner with Product "
    "Marketing on launches. Building AI into the operating model for engagement Defining and "
    "tracking adoption metrics across Marketing, Sales and Customer Success Monitoring program "
    "results every month. What You Bring Experience in customer marketing at a SaaS company "
    "Strong project management skills and HubSpot. What you will find here: Competitive salary "
    "with health benefits. $2000 annual counseling benefit and RRSP matching. The expected "
    "salary range for this role is $110,300 to $149,300 CAD. Initial placement depends on "
    "experience."
)

FR = (
    "Exemple inc. est une entreprise québécoise qui aide les familles depuis plus de quarante ans. "
    "Nous offrons des services d'assurance, d'assistance routière et de réparation automobile "
    "partout dans la province, avec une équipe engagée envers ses membres et sa communauté. "
    "Ce poste est un contrat à durée déterminée de six mois, en mode hybride, à Vancouver. "
    "Ce que vous ferez : Créer du contenu pour les réseaux sociaux chaque semaine Gérer le "
    "calendrier éditorial avec l'équipe Coordonner les événements de la marque. Ce que vous "
    "apportez : Expérience de deux ans en création de contenu. Excellente maîtrise du français "
    "et de l'anglais. Le taux horaire est de 35 $ à 44 $ l'heure selon l'expérience."
)


def test_headers_go_on_their_own_line_and_lists_split_into_items() -> None:
    out = UNFLATTEN.unflatten(EN)
    lines = [ln for ln in out.split("\n") if ln.strip()]
    assert "What you'll work on" in lines and "What You Bring" in lines
    assert "- Building AI into the operating model for engagement" in lines
    assert (
        "- Defining and tracking adoption metrics across Marketing, Sales and Customer Success"
        in lines
    )
    assert "- Monitoring program results every month." in lines
    assert "- Experience in customer marketing at a SaaS company" in lines
    assert "- Strong project management skills and HubSpot." in lines
    # "Marketing" (an -ing noun) and "Sales" did not start new items
    assert not any(ln.startswith(("- Marketing", "- Sales")) for ln in lines)


def test_pay_sentence_moves_out_of_benefits() -> None:
    out = UNFLATTEN.unflatten(EN)
    pay = out.split("\nPay\n", 1)[1]
    assert pay.strip() == "The expected salary range for this role is $110,300 to $149,300 CAD."
    assert "$2000 annual counseling benefit" in out.split("\nPay\n", 1)[0]


def test_french_flattened_posting() -> None:
    out = UNFLATTEN.unflatten(FR)
    lines = [ln for ln in out.split("\n") if ln.strip()]
    assert "- Créer du contenu pour les réseaux sociaux chaque semaine" in lines
    assert "- Gérer le calendrier éditorial avec l'équipe" in lines
    assert "- Expérience de deux ans en création de contenu." in lines
    assert lines[-1] == "Le taux horaire est de 35 $ à 44 $ l'heure selon l'expérience."


def test_normally_formatted_text_is_untouched() -> None:
    text = "Responsibilities\n- Build dashboards\n- Write SQL\n\nRequirements\n- 3+ years\n" * 20
    assert UNFLATTEN.unflatten(text) == text
    assert UNFLATTEN.unflatten("Short one-line posting.") == "Short one-line posting."


def test_flattened_posting_through_the_normalizer(normalizer: Normalizer) -> None:
    raw = RawJob(
        source="manual",
        source_id="url-3",
        url="https://jobs.example.org/3",
        title="Senior Customer Marketing Manager",
        description=EN,
        fetched_at=datetime(2026, 10, 26, tzinfo=UTC),
    )
    job = normalizer.normalize(raw, now=datetime(2026, 10, 26, tzinfo=UTC))
    assert len(job.responsibilities) >= 4
    assert len(job.requirements) == 3  # "Who you are:" is a requirements header too
    assert (job.pay_min, job.pay_max) == (110300, 149300)  # not the $2000 benefit
    assert "webinar marketing" in job.skills and "project management" in job.skills


def test_run_on_benefits_keep_their_amounts_out_of_pay() -> None:
    text = EN.replace(
        "Competitive salary with health benefits. $2000 annual counseling benefit and RRSP "
        "matching. The expected",
        "Competitive salary with health benefits $2000 annual counseling benefit RRSP matching "
        "Recognition at 3, 5, 7, and 10 years The expected",
    )
    out = UNFLATTEN.unflatten(text)
    before, pay = out.split("\nPay\n", 1)
    assert pay.strip() == "The expected salary range for this role is $110,300 to $149,300 CAD."
    assert "$2000 annual counseling benefit" in before


def test_paragraphs_of_run_on_text_count_as_flattened() -> None:
    three_paragraphs = "\n\n".join([EN[:700], EN[700:1400], EN[1400:]])
    assert UNFLATTEN.is_flattened(three_paragraphs)
    assert not UNFLATTEN.is_flattened("Responsibilities\n- Build dashboards\n" * 60)
