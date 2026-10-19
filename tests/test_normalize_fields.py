"""Unit tests for each normalization step, with English and French examples."""

import pytest

from conftest import CONFIG_DIR, posting
from jobmatch.config import (
    load_canada_locations,
    load_company_aliases,
    load_contract_phrases,
    load_normalization_rules,
    load_skills_taxonomy,
)
from jobmatch.normalize.company import CompanyNormalizer
from jobmatch.normalize.contract import ContractDetector, parse_duration_months
from jobmatch.normalize.fingerprint import job_fingerprint
from jobmatch.normalize.language import LanguageDetector
from jobmatch.normalize.location import LocationParser
from jobmatch.normalize.sections import SectionSplitter
from jobmatch.normalize.seniority import SeniorityDetector
from jobmatch.normalize.skills import SkillExtractor
from jobmatch.normalize.text import to_plain_text
from jobmatch.normalize.title import TitleNormalizer
from jobmatch.schemas import EmploymentType, Language, RemoteType

RULES = load_normalization_rules(CONFIG_DIR)
LOCATIONS = LocationParser(load_canada_locations(CONFIG_DIR))
SECTIONS = SectionSplitter(RULES)
TITLES = TitleNormalizer(RULES, LOCATIONS.looks_like_location)
COMPANIES = CompanyNormalizer(load_company_aliases(CONFIG_DIR))
LANGUAGE = LanguageDetector(RULES.language_words, min_hits=4, bilingual_min_share=0.25)
CONTRACT = ContractDetector(load_contract_phrases(CONFIG_DIR), threshold=0.8)
SKILLS = SkillExtractor(load_skills_taxonomy(CONFIG_DIR))
SENIORITY = SeniorityDetector(RULES.seniority)


# ---- sections: boilerplate and requirements ----


def test_english_html_boilerplate_is_dropped_and_duties_kept() -> None:
    clean = SECTIONS.strip_boilerplate(to_plain_text(posting("en_contract_data_engineer.html")))
    assert "Build and maintain ETL pipelines" in clean
    assert "fictional lender" not in clean  # About us
    assert "dental" not in clean  # Benefits
    assert "welcome applications" not in clean  # Equal opportunity


def test_french_boilerplate_is_dropped() -> None:
    clean = SECTIONS.strip_boilerplate(to_plain_text(posting("fr_contrat_analyste.txt")))
    assert "cartographie des processus" in clean
    assert "Organisme public fictif" not in clean  # À propos de nous
    assert "Régime de retraite" not in clean  # Avantages sociaux
    assert "souscrivons" not in clean  # Équité en matière d'emploi


def test_requirements_from_english_html_bullets() -> None:
    text = to_plain_text(posting("en_contract_data_engineer.html"))
    assert SECTIONS.requirements(text, limit=10) == [
        "5+ years of Python experience",
        "Strong SQL and data modeling skills",
        "Experience with Airflow is an asset",
    ]


def test_requirements_from_french_header_with_colon() -> None:
    text = to_plain_text(posting("fr_contrat_analyste.txt"))
    assert SECTIONS.requirements(text, limit=2) == [
        "Minimum de 8 ans d'expérience en analyse d'affaires",
        "Maîtrise de SQL, un atout",
    ]


def test_inline_headers() -> None:
    text = "Great role.\nAbout us: a fictional startup.\nRequirements: Python, SQL\nSchedule: days"
    assert SECTIONS.strip_boilerplate(text) == (
        "Great role.\nRequirements: Python, SQL\nSchedule: days"
    )
    assert SECTIONS.requirements(text, limit=5) == ["Python, SQL"]


def test_unknown_subheader_stays_in_requirements() -> None:
    text = "Qualifications\nTechnical skills:\n- Java\n- Kubernetes"
    assert SECTIONS.requirements(text, limit=5) == ["Java", "Kubernetes"]


# ---- titles ----


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Senior Python Data Engineer (Contract) - Req #A1029", "senior python data engineer"),
        ("Analyste d'affaires principal(e) – contrat de 12 mois", "analyste d'affaires principal"),
        ("Développeur·se Full Stack (H/F) - Montréal, QC", "développeur full stack"),
        ("Data Engineer - 12 Month Contract", "data engineer"),
        ("Business Analyst | Remote", "business analyst"),
        ("Senior Developer (m/f/d) Job ID: 4471", "senior developer"),
        (
            "Gestionnaire de projet TI / IT Project Manager (mandat de 6 mois / 6-month contract)",
            "gestionnaire de projet ti / it project manager",
        ),
        ("IT Project Manager - Ontario Health", "it project manager - ontario health"),
        ("Contract Manager", "contract manager"),
    ],
)
def test_title_normalization(raw: str, expected: str) -> None:
    assert TITLES.normalize(raw) == expected


# ---- companies ----


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Example Financial Inc.", "example financial"),
        ("Banque Exemple Ltée", "banque exemple"),
        ("ExampleCo Solutions s.e.n.c.", "exampleco solutions"),
        ("Groupe Exemple Inc., Ltd.", "groupe exemple"),
        ("Organisme Exemple du Québec", "organisme exemple du québec"),
        ("RBC", "royal bank of canada"),
        ("Banque Royale du Canada", "royal bank of canada"),
        ("Mouvement Desjardins", "desjardins"),
        (None, None),
    ],
)
def test_company_normalization(raw: str | None, expected: str | None) -> None:
    assert COMPANIES.normalize(raw) == expected


# ---- locations ----


@pytest.mark.parametrize(
    ("raw", "city", "province", "country", "remote"),
    [
        ("Toronto, ON (Hybrid)", "Toronto", "CA-ON", "CA", RemoteType.HYBRID),
        ("Montréal (QC)", "Montréal", "CA-QC", "CA", RemoteType.UNKNOWN),
        ("Montreal, Quebec", "Montréal", "CA-QC", "CA", RemoteType.UNKNOWN),
        ("Gatineau, Québec, Canada", "Gatineau", "CA-QC", "CA", RemoteType.UNKNOWN),
        ("Calgary, Alberta", "Calgary", "CA-AB", "CA", RemoteType.UNKNOWN),
        ("Vancouver, BC, CA", "Vancouver", "CA-BC", "CA", RemoteType.UNKNOWN),
        ("St. John's, NL", "St. John's", "CA-NL", "CA", RemoteType.UNKNOWN),
        ("Québec City, QC (sur place)", "Québec City", "CA-QC", "CA", RemoteType.ONSITE),
        ("London, ON", "London", "CA-ON", "CA", RemoteType.UNKNOWN),
        ("Remote - Canada", None, None, "CA", RemoteType.REMOTE),
        ("Télétravail – partout au Canada", None, None, "CA", RemoteType.REMOTE),
        ("Remote - US or Canada", None, None, "CA", RemoteType.REMOTE),
        ("Seattle, WA", None, None, "US", RemoteType.UNKNOWN),
        ("New York, NY, United States", None, None, "US", RemoteType.UNKNOWN),
        ("London, United Kingdom", None, None, "GB", RemoteType.UNKNOWN),
        ("Paris, France (hybride)", None, None, "FR", RemoteType.HYBRID),
        ("Remote", None, None, None, RemoteType.REMOTE),
        (None, None, None, None, RemoteType.UNKNOWN),
    ],
)
def test_location_parsing(
    raw: str | None, city: str | None, province: str | None, country: str | None, remote: RemoteType
) -> None:
    info = LOCATIONS.parse(raw)
    assert (info.city, info.province, info.country, info.remote_type) == (
        city,
        province,
        country,
        remote,
    )


@pytest.mark.parametrize(
    ("segment", "expected"),
    [
        ("Toronto, ON", True),
        ("Remote", True),
        ("Télétravail ou Montréal", True),
        ("Ontario Health", False),
        ("Data Engineer", False),
        ("ON", True),
    ],
)
def test_looks_like_location(segment: str, expected: bool) -> None:
    assert LOCATIONS.looks_like_location(segment) is expected


# ---- language ----


def test_language_english_french_bilingual() -> None:
    english = to_plain_text(posting("en_contract_data_engineer.html"))
    assert LANGUAGE.detect(english) is Language.EN
    assert LANGUAGE.detect(posting("fr_contrat_analyste.txt")) is Language.FR
    assert LANGUAGE.detect(posting("bilingual_project_manager.txt")) is Language.BILINGUAL


def test_language_unknown_for_short_or_other_languages() -> None:
    assert LANGUAGE.detect("Project Manager") is None
    assert LANGUAGE.detect(posting("es_desarrollador.txt")) is None
    assert LANGUAGE.word_count(posting("es_desarrollador.txt")) >= 40


# ---- contract detection ----


def test_english_contract_with_duration() -> None:
    result = CONTRACT.assess(to_plain_text(posting("en_contract_data_engineer.html")))
    assert result.employment_type is EmploymentType.CONTRACT
    assert result.signals[:2] == ["contract", "possible extension"]
    assert result.duration_months == 6.0


def test_french_fixed_term_contract() -> None:
    result = CONTRACT.assess(posting("fr_contrat_analyste.txt"))
    assert result.employment_type is EmploymentType.CONTRACT
    assert result.signals == ["contrat", "durée déterminée"]
    assert result.duration_months == 12.0


def test_permanent_role_about_contracts_is_permanent() -> None:
    text = "Contract Manager, Procurement\n" + posting("en_permanent_contract_manager.txt")
    result = CONTRACT.assess(text)
    assert result.employment_type is EmploymentType.PERMANENT
    assert (
        result.signals == []
    )  # "contract management", "permanent resident"... are false positives
    assert result.duration_months is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Poste permanent à temps plein", EmploymentType.PERMANENT),
        ("Emploi en CDI, temps plein", EmploymentType.PERMANENT),
        ("Mandat de 6 mois, possibilité de prolongation", EmploymentType.CONTRACT),
        ("Remplacement de congé de maternité (13 mois)", EmploymentType.CONTRACT),
        (
            "Must be a Canadian citizen or permanent resident. 6-month contract.",
            EmploymentType.CONTRACT,
        ),
        ("Temporary role", EmploymentType.UNKNOWN),  # weak signal alone: kept for review
        ("Responsable de la gestion des contrats. Poste permanent.", EmploymentType.PERMANENT),
        ("Votre mandat : diriger l'équipe TI.", EmploymentType.UNKNOWN),
        ("Great team, great culture.", EmploymentType.UNKNOWN),
    ],
)
def test_contract_phrases(text: str, expected: EmploymentType) -> None:
    assert CONTRACT.assess(text).employment_type is expected


@pytest.mark.parametrize(
    ("text", "structured", "expected"),
    [
        ("6-month contract", "Permanent", EmploymentType.PERMANENT),
        ("Great team", "fixed_term", EmploymentType.CONTRACT),
        ("Great team", "Temporaire", EmploymentType.TEMPORARY),
        ("Contrat de 6 mois", "full_time", EmploymentType.CONTRACT),  # unknown value: phrases
    ],
)
def test_structured_field_wins_over_phrases(
    text: str, structured: str, expected: EmploymentType
) -> None:
    assert CONTRACT.assess(text, structured).employment_type is expected


@pytest.mark.parametrize(
    ("text", "months"),
    [
        ("6-month contract", 6.0),
        ("contrat de 12 mois", 12.0),
        ("12 Month Contract", 12.0),
        ("contrat de 2 ans", 24.0),
        ("3-year contract", 36.0),
        ("8 weeks contract", 1.8),
        ("contrat de 1,5 mois", 1.5),
        ("5+ years of Python", None),
    ],
)
def test_duration_parsing(text: str, months: float | None) -> None:
    assert parse_duration_months(text) == months


# ---- skills ----


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("JavaScript and TypeScript developer", ["javascript", "typescript"]),
        ("Java 17, Spring Boot", ["java", "spring boot"]),
        (
            "Expérience en gestion de projet et analyse d'affaires",
            ["project management", "business analysis"],
        ),
        ("C#/.NET Core, ASP.NET", ["c#", ".net"]),
        ("Node.js and React.js", ["node.js", "react"]),
        ("Tableau de bord mensuel", []),  # French "tableau" is not the BI tool
        ("Power BI dashboards, Excel avancé", ["power bi", "excel"]),
        (
            "Comptabilité, paie et conciliation bancaire",
            ["accounting", "payroll", "reconciliation"],
        ),
        ("Génie civil, AutoCAD Civil 3D", ["civil engineering", "autocad"]),
        ("Cote de fiabilité requise; reliability status", ["security clearance"]),
        ("Soins infirmiers en santé mentale", ["patient care"]),
        ("", []),
    ],
)
def test_skill_extraction(text: str, expected: list[str]) -> None:
    assert SKILLS.extract(text) == expected


# ---- seniority ----


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Senior Python Developer", "senior"),
        ("Analyste d'affaires principal(e)", "senior"),
        ("Jr. Accountant", "junior"),
        ("Directeur, technologies de l'information", "executive"),
        ("Tech Lead / Senior Engineer", "lead"),
        ("Data Analyst II", "intermediate"),
        ("Project Manager", None),
    ],
)
def test_seniority(title: str, expected: str | None) -> None:
    assert SENIORITY.detect(title) == expected


# ---- fingerprint ----


def test_fingerprint_is_stable_and_accent_insensitive() -> None:
    a = job_fingerprint("organisme exemple", "analyste d'affaires", "Montréal")
    assert a == job_fingerprint("Organisme  Exemple", "analyste d'affaires", "Montreal")
    assert a != job_fingerprint("organisme exemple", "analyste financier", "Montréal")
    assert len(a) == 16
