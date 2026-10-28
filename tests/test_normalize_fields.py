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
from jobmatch.normalize.description import format_pay
from jobmatch.normalize.experience import years_experience_min
from jobmatch.normalize.fingerprint import job_fingerprint
from jobmatch.normalize.language import LanguageDetector
from jobmatch.normalize.location import LocationParser
from jobmatch.normalize.pay import PayInfo, PayParser
from jobmatch.normalize.sections import SectionKind, SectionSplitter
from jobmatch.normalize.seniority import SeniorityDetector
from jobmatch.normalize.skills import SkillExtractor
from jobmatch.normalize.text import to_plain_text
from jobmatch.normalize.title import TitleNormalizer
from jobmatch.schemas import EmploymentType, Language, PayUnit, RemoteType

RULES = load_normalization_rules(CONFIG_DIR)
LOCATIONS = LocationParser(load_canada_locations(CONFIG_DIR))
SECTIONS = SectionSplitter(RULES)
TITLES = TitleNormalizer(RULES, LOCATIONS.looks_like_location)
COMPANIES = CompanyNormalizer(load_company_aliases(CONFIG_DIR))
LANGUAGE = LanguageDetector(RULES.language_words, min_hits=4, bilingual_min_share=0.25)
CONTRACT = ContractDetector(load_contract_phrases(CONFIG_DIR), threshold=0.8)
SKILLS = SkillExtractor(load_skills_taxonomy(CONFIG_DIR))
SENIORITY = SeniorityDetector(RULES.seniority)
PAY = PayParser(RULES.pay)


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


def test_posting_with_pitch_and_ats_metadata_is_split_by_kind() -> None:
    """Real-world shape: unlisted "Why ...?" pitch, Title Case sub-headings, ATS pay fields."""
    parsed = SECTIONS.parse(posting("en_pitch_and_ats_metadata.txt"))
    assert parsed.items(SectionKind.RESPONSIBILITIES, 10) == [
        "Manage and grow our online communities across Instagram, TikTok and Facebook",
        "Track and report on community health metrics",
        "Write platform-native social copy informed by SEO best practices",
    ]
    assert parsed.items(SectionKind.REQUIREMENTS, 10) == [
        "A minimum of 5 years' experience in social media copywriting",
        "Working knowledge of SEO principles as they relate to social media copy",
    ]
    clean = parsed.clean_text()
    assert "Example Charity Canada is seeking" in clean
    for leaked in ("Agile mindset", "actively recruiting", "Hiring Min Rate", "Job Family"):
        assert leaked not in clean
    assert "70000" in parsed.text({SectionKind.PAY})


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("Key Responsibilities", SectionKind.RESPONSIBILITIES),
        ("What you'll be doing:", SectionKind.RESPONSIBILITIES),
        ("Vos responsabilités", SectionKind.RESPONSIBILITIES),
        ("Required Skills & Experience", SectionKind.REQUIREMENTS),
        ("Profil recherché", SectionKind.REQUIREMENTS),
        ("Preferred Qualifications", SectionKind.NICE_TO_HAVE),
        ("Atouts", SectionKind.NICE_TO_HAVE),
        ("Salary Range", SectionKind.PAY),
        ("Rémunération", SectionKind.PAY),
        ("Compensation & Benefits", SectionKind.BENEFITS),
        ("Why join Example Co?", SectionKind.EMPLOYER),
        ("About Example Insurance", SectionKind.EMPLOYER),
        ("Pourquoi se joindre à nous ?", SectionKind.EMPLOYER),
        ("About the role", SectionKind.OVERVIEW),
        ("About the Opportunity", SectionKind.OVERVIEW),
        ("À propos de l'opportunité", SectionKind.OVERVIEW),
        ("About the Requirements", SectionKind.REQUIREMENTS),
        ("Qualifications Required", SectionKind.REQUIREMENTS),
        ("Qualifications requises", SectionKind.REQUIREMENTS),
        ("Job Duties", SectionKind.RESPONSIBILITIES),
        ("Diversity & Inclusion", SectionKind.EQUAL_OPPORTUNITY),
    ],
)
def test_header_kinds_en_fr(text: str, kind: SectionKind) -> None:
    assert SECTIONS.classify(text) == (kind, "")


def test_about_the_opportunity_is_the_job_not_the_employer() -> None:
    """Tucows-style paste with blank lines gone: "About the Opportunity" was read as the
    employer pitch and swallowed the duties and requirements after it."""
    text = (
        "Job description\n"
        "Example Domains is the world's largest wholesale domain registrar.\n"
        "About the Opportunity\n"
        "We are looking for a graphic designer to join our in-house agency.\n"
        "Job Duties\n"
        "Design event collateral (banners, booth kits, conference signage).\n"
        "Develop video content, including sizzle reels and tutorials.\n"
        "Qualifications Required\n"
        "3-5 years of experience as a graphic or visual designer.\n"
        "Experience with Adobe Creative Suite, Figma and After Effects."
    )
    parsed = SECTIONS.parse(text)
    assert parsed.items(SectionKind.RESPONSIBILITIES, 10) == [
        "Design event collateral (banners, booth kits, conference signage).",
        "Develop video content, including sizzle reels and tutorials.",
    ]
    assert parsed.items(SectionKind.REQUIREMENTS, 10) == [
        "3-5 years of experience as a graphic or visual designer.",
        "Experience with Adobe Creative Suite, Figma and After Effects.",
    ]
    assert "looking for a graphic designer" in parsed.clean_text()


def test_french_about_the_opportunity_is_kept() -> None:
    text = (
        "À propos de l'opportunité\n"
        "Nous cherchons un graphiste pour notre agence interne.\n"
        "Qualifications requises\n"
        "Maîtrise de la suite Adobe."
    )
    parsed = SECTIONS.parse(text)
    assert "Nous cherchons un graphiste" in parsed.clean_text()
    assert parsed.items(SectionKind.REQUIREMENTS, 10) == ["Maîtrise de la suite Adobe."]


@pytest.mark.parametrize(
    "line",
    [
        "- Salary negotiable",  # a bullet, not a header
        "Experience with SQL Server is required.",  # a sentence
        "Pay attention to detail in all client reports and audits",  # too long
        "5 years of experience",  # has digits
    ],
)
def test_content_lines_are_not_headers(line: str) -> None:
    assert SECTIONS.classify(line) is None


def test_role_lines_drop_metadata_and_application_steps() -> None:
    text = (
        "Position:\n\nData Analyst\n\nReports to: Director, Finance\n"
        "Please apply with your resume and portfolio.\n"
        "Note: this role requires occasional travel to client sites across Ontario.\n"
        "About the role\nYou will build the monthly reporting for our finance team.\n"
        "Lieu de travail : Montréal\nVeuillez soumettre votre candidature en ligne."
    )
    assert SECTIONS.parse(text).role_lines() == [
        "Note: this role requires occasional travel to client sites across Ontario.",
        "You will build the monthly reporting for our finance team.",
    ]


def test_pattern_header_inside_same_kind_is_content() -> None:
    text = "Requirements\n\nExperience with SQL Server\n- Python"
    assert SECTIONS.requirements(text, limit=5) == ["Experience with SQL Server", "Python"]


def test_skills_come_only_from_job_sections() -> None:
    clean = SECTIONS.strip_boilerplate(posting("en_pitch_and_ats_metadata.txt"))
    skills = SKILLS.extract(clean)
    assert {"social media", "copywriting", "seo"} <= set(skills)
    assert not {"agile", "recruitment", "compensation"} & set(skills)  # all in the pitch


# ---- pay ----


@pytest.mark.parametrize(
    ("pay_text", "body", "expected"),
    [
        ("", "Salary: $70,000 - $85,000 per year", (70000, 85000, PayUnit.YEAR, "CAD")),
        ("", "Rémunération : 70 000 $ à 85 000 $ par année", (70000, 85000, PayUnit.YEAR, "CAD")),
        ("", "Rate: $45/hr", (45, None, PayUnit.HOUR, "CAD")),  # single amount: no range
        ("", "Taux : 90 $ de l'heure", (90, None, PayUnit.HOUR, "CAD")),
        ("", "Pay range $100K-$120K", (100000, 120000, PayUnit.YEAR, "CAD")),
        ("", "Compensation: USD 60-75 per hour", (60, 75, PayUnit.HOUR, "USD")),
        ("Pay Type\nHourly\nHiring Min Rate\n70000\nHiring Max Rate\n85000", "",
         (70000, 85000, PayUnit.YEAR, None)),  # "Hourly" contradicts the amounts
        ("Salary\n$650 per day", "", (650, None, PayUnit.DAY, "CAD")),
        ("", "Salary: $75,000 to $75,000 per year", (75000, None, PayUnit.YEAR, "CAD")),
    ],
)  # fmt: skip
def test_pay_parsing(pay_text: str, body: str, expected: tuple) -> None:
    info = PAY.parse(pay_text, body)
    assert info is not None
    assert (info.pay_min, info.pay_max, info.unit, info.currency) == expected


@pytest.mark.parametrize(
    ("pay", "shown"),
    [
        (PayInfo(pay_min=75000, pay_max=None, unit=PayUnit.YEAR, currency=None),
         "Annual salary: 75,000"),
        (PayInfo(pay_min=70000, pay_max=85000, unit=PayUnit.YEAR, currency="CAD"),
         "Annual salary: 70,000–85,000 CAD"),
        (PayInfo(pay_min=45.5, pay_max=None, unit=PayUnit.HOUR, currency="CAD"),
         "Hourly rate: 45.50 CAD"),
        (PayInfo(pay_min=650, pay_max=None, unit=None, currency=None), "Pay: 650"),
    ],
)  # fmt: skip
def test_pay_is_shown_as_single_amount_or_range(pay: PayInfo, shown: str) -> None:
    assert format_pay(pay) == shown


@pytest.mark.parametrize(
    "body",
    [
        "You will manage a $250,000 budget across 3 projects.",  # no pay wording
        "Our foundation raised $5M in 2025.",
        "We have 2 offices and 5 000 employees.",
        "",
    ],
)
def test_amounts_that_are_not_pay(body: str) -> None:
    assert PAY.parse("", body) is None


# ---- years of experience ----


@pytest.mark.parametrize(
    ("lines", "years"),
    [
        (["5+ years of Python experience"], 5.0),
        (["A minimum of 5 years' experience in social media"], 5.0),
        (["3-5 years in a similar role"], 3.0),
        (["Minimum de 8 ans d'expérience en analyse d'affaires"], 8.0),
        (["Au moins 2 années en gestion de projet"], 2.0),
        (["Strong SQL", "7 years managing vendors"], 7.0),
        (["Bilingual English/French"], None),
    ],
)
def test_years_of_experience(lines: list[str], years: float | None) -> None:
    assert years_experience_min(lines) == years


@pytest.mark.parametrize(
    ("line", "years"),
    [
        ("Job requires a minimum of seven (7) years' experience", 7.0),
        ("The position requires a minimum of seven years of experience", 7.0),
        ("(5) years of experience in a similar role", 5.0),
        ("Sept ans d'expérience en gestion de projet", 7.0),
        ("Au moins un an d'expérience", 1.0),
        ("Two or more years in broadcasting", 2.0),
        ("Twenty-four years of service", 24.0),
        ("Rate of $45 an hour", None),  # "an" is not "année" here
        ("A year of experience is an asset", None),  # "a" is not counted on purpose
    ],
)
def test_years_written_as_words(line: str, years: float | None) -> None:
    assert years_experience_min([line], RULES.number_words) == years


# ---- titles ----


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Senior Python Data Engineer (Contract) - Req #A1029", "senior python data engineer"),
        ("Analyste d'affaires principal(e) – contrat de 12 mois", "analyste d'affaires principal"),
        ("Développeur·se Full Stack (H/F) - Montréal, QC", "développeur full stack"),
        ("Data Engineer - 12 Month Contract", "data engineer"),
        ("Marketing Coordinator - 12 month FTC", "marketing coordinator"),
        ("HR Advisor (FTC, part-time)", "hr advisor"),
        (
            "Senior Producer, CBC Life (English Services) (Hybrid)",
            "senior producer, cbc life (english services)",
        ),
        ("Data Analyst (Remote - Canada)", "data analyst"),
        ("Analyste (télétravail)", "analyste"),
        ("Business Analyst | Remote", "business analyst"),
        ("Senior Developer (m/f/d) Job ID: 4471", "senior developer"),
        (
            "Gestionnaire de projet TI / IT Project Manager (mandat de 6 mois / 6-month contract)",
            "gestionnaire de projet ti / it project manager",
        ),
        ("IT Project Manager - Ontario Health", "it project manager - ontario health"),
        ("Contract Manager", "contract manager"),
        # Twine page titles: company first, " freelance job | Twine" last
        (
            "I8IS INC. - Senior Full Stack Developer freelance job | Twine",
            "senior full stack developer",
        ),
        (
            "Upstaff - Senior Full Stack Software Developer – Java / Angular freelance job | Twine",
            "senior full stack software developer - java / angular",
        ),
        ("Freelance Product Developer", "freelance product developer"),
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
        ("Marketing Coordinator - 12 month FTC", EmploymentType.CONTRACT),
        ("Full-time FTC until March 2027", EmploymentType.CONTRACT),
        ("Our FTCs are paid weekly", EmploymentType.CONTRACT),  # plural still counts
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
        ("12-month FTC", 12.0),
        ("1-year FTC", 12.0),
        ("FTC of 2 years", 24.0),
        ("8 weeks contract", 1.8),
        ("contrat de 1,5 mois", 1.5),
        ("5+ years of Python", None),
    ],
)
def test_duration_parsing(text: str, months: float | None) -> None:
    assert parse_duration_months(text) == months


@pytest.mark.parametrize(
    ("text", "months"),
    [
        ("This is a one-year contract position.", 12.0),
        ("A six-month mandate", 6.0),
        ("Contrat d'un an", 12.0),
        ("Mandat de douze mois", 12.0),
        ("Twelve month FTC", 12.0),
        ("Contract of two years", 24.0),
        ("Seven years of Python", None),  # years without a contract word are experience
    ],
)
def test_duration_written_as_words(text: str, months: float | None) -> None:
    assert parse_duration_months(text, RULES.number_words) == months


# ---- where a list ends ----


def test_bulleted_list_ends_at_the_first_plain_sentence() -> None:
    text = (
        "What you bring:\n"
        "• Strong SQL\n"
        "• Python and\n"
        "dbt modelling\n"
        "The position requires a minimum of seven years of experience in data work.\n"
        "Interactive agency experience is preferred\n"
        "• A bullet after the list has ended\n"
    )
    parsed = SECTIONS.parse(text)
    assert parsed.items(SectionKind.REQUIREMENTS, None) == [
        "Strong SQL",
        "Python and dbt modelling",
    ]
    # the trailing sentence still counts for years of experience
    reader_lines = parsed.lines({SectionKind.REQUIREMENTS})
    assert years_experience_min(reader_lines, RULES.number_words) == 7.0


def test_list_items_wrapped_in_paragraphs_are_all_kept() -> None:
    """Real-world shape (Built In / CBC): <li><p>text</p></li>, with a plain lead-in line."""
    html = (
        "<h3>Key Tasks</h3><p>Under the direction of the Managing Editor</p><ul>"
        + "".join(f"<li><p>Duty number {i} for the platform</p></li>" for i in range(13))
        + "</ul><h3>Qualifications</h3><ul><li><p>Seven (7) years of experience</p></li></ul>"
    )
    parsed = SECTIONS.parse(to_plain_text(html))
    duties = parsed.items(SectionKind.RESPONSIBILITIES, None)
    assert duties[0] == "Under the direction of the Managing Editor"  # lead-in kept
    assert len(duties) == 14
    assert parsed.items(SectionKind.REQUIREMENTS, None) == ["Seven (7) years of experience"]


def test_sub_headings_between_bullets_do_not_end_the_list() -> None:
    text = "Requirements\n• SQL\nTechnical\n• Python\nNice extras\n• dbt"
    assert SECTIONS.requirements(text, limit=10) == ["SQL", "Python", "dbt"]


def test_plain_lists_have_no_end_signal_so_the_cap_bounds_them() -> None:
    sentence = "Requirement number {} written as a full plain sentence here."
    lines = "\n".join(sentence.format(i) for i in range(30))
    assert len(SECTIONS.requirements(f"Requirements\n{lines}", limit=20)) == 20


def test_asides_are_never_bullets() -> None:
    text = (
        "Requirements\n"
        "• Ability to apply Agile principles\n"
        "• Web application security experience\n"
        "• Experience managing $1M budgets\n"
        "• The anticipated salary range is $70,000 – $80,000 CAD.\n"
        "• Applicants are invited to submit a portfolio of work.\n"
        "• As part of our recruitment process, candidates complete a short test.\n"
        "• Veuillez soumettre votre candidature en ligne\n"
    )
    assert SECTIONS.requirements(text, limit=10) == [
        "Ability to apply Agile principles",
        "Web application security experience",
        "Experience managing $1M budgets",
    ]


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
        # not_when phrases hide a term that does not mean the skill
        ("You excel at building relationships; advanced Excel", ["excel"]),
        ("You excel in fast-paced teams", []),
        ("As part of our recruitment process, candidates complete a test", []),
        ("Processus de recrutement accessible", []),
        ("Full-cycle recruitment for sales roles", ["recruitment"]),
        ("Animation d'ateliers et animation 3D", ["animation"]),
        ("Senior Animator: character animation in Maya", ["animation"]),
        # generic words no longer tag the wrong skill
        ("Fluid UI, event mapping; website/UI copy", []),
        ("UX designers, UI design and wireframing", ["ui/ux design"]),
        ("Daily tools: monday.com, Zoom, Figma", ["figma"]),
        ("Experience with prototyping and manufacturing", []),
        ("Passion for continuous improvement", ["process improvement"]),
        ("AI data trainer for coding models", []),
        ("Reporting impacts of mergers and acquisitions", ["mergers and acquisitions"]),
        ("Helps readers navigate purchasing decisions", []),
        ("Purchasing agent, approvisionnement", ["procurement"]),
        # titles written as people, in both languages
        ("Graphic Designer (InDesign, Adobe Illustrator)", ["graphic design"]),
        ("Graphiste junior", ["graphic design"]),
        ("Corporate Conference and Event Coordinator", ["event planning"]),
        ("Coordination d'événements corporatifs", ["event planning"]),
        ("PeopleSoft FSCM and Workday Financials", ["erp implementation"]),
        ("Senior Webflow Digital Producer", ["content management systems", "digital production"]),
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
