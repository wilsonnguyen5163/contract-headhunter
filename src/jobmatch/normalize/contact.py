"""The HR or hiring contact's email, taken from the posting text (TODO 1, user decisions
2026-11-10).

Only what the posting itself says: nothing is looked up anywhere (CLAUDE.md rule 4). The
app shows and copies it; it never sends email (rule 5). An address is personal data when it
names someone: it stays out of logs and out of what Gemini receives (llm/payload.py masks
addresses).

Rules: the first address in the text that is not an automated or policy mailbox (noreply@,
privacy@, …) and not a file name that looks like one (logo@2x.png). Assumption: when a
posting gives several, the first usable one is the application contact.
"""

import re

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}", re.UNICODE)
_NOT_CONTACT = re.compile(
    r"^(?:no-?reply|do-?not-?reply|ne-?pas-?repondre|privacy|confidentialite|unsubscribe|"
    r"webmaster|postmaster|abuse|mailer-daemon)$",
    re.IGNORECASE,
)
_FILE_ENDINGS = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")


def contact_email(text: str | None) -> str | None:
    """The posting's contact email, lower-cased, or None."""
    if not text:
        return None
    for found in EMAIL.finditer(text):
        address = found.group(0).strip(".")
        local = address.split("@", 1)[0]
        if _NOT_CONTACT.match(local) or address.lower().endswith(_FILE_ENDINGS):
            continue
        return address.lower()
    return None
