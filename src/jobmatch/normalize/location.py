"""Parse free-text locations into city, province (ISO 3166-2:CA), country and remote type.

Country rules (recall first): an explicit province or "Canada" means CA, even if a foreign
country is also named ("Remote - US or Canada"); otherwise a named foreign country or a
", WA"-style US state wins; otherwise a known Canadian city means CA; otherwise unknown.
"""

import re

from pydantic import BaseModel

from jobmatch.config import CanadaLocations
from jobmatch.normalize.phrases import PhraseMatcher, fold
from jobmatch.schemas import RemoteType

# Words that may surround location terms in a title segment ("Remote or Toronto").
_FILLER_WORDS = {"or", "ou", "and", "et", "in", "en", "au", "based", "base", "basee", "only"}


class LocationInfo(BaseModel):
    city: str | None = None
    province: str | None = None
    country: str | None = None
    remote_type: RemoteType = RemoteType.UNKNOWN


class LocationParser:
    def __init__(self, locations: CanadaLocations) -> None:
        self._province_names = PhraseMatcher(
            {name: code for code, entry in locations.provinces.items() for name in entry.names}
        )
        self._abbreviations = {
            abbr: code
            for code, entry in locations.provinces.items()
            for abbr in entry.abbreviations
        }
        abbr_alternation = "|".join(
            re.escape(a) for a in sorted(self._abbreviations, key=len, reverse=True)
        )
        self._abbreviation_re = re.compile(rf"(?<![\w.])({abbr_alternation})(?!\w)")
        self._city_province = {
            city: province for province, cities in locations.cities.items() for city in cities
        }
        self._cities = PhraseMatcher.from_terms(self._city_province)
        self._canada = PhraseMatcher.from_terms(locations.canada_terms)
        foreign = {
            name: code for code, names in locations.foreign_countries.items() for name in names
        }
        foreign.update({state: "US" for state in locations.us_states})
        self._foreign = PhraseMatcher(foreign)
        codes = "|".join(re.escape(c) for c in locations.us_state_codes)
        self._us_state_code_re = re.compile(rf",\s*({codes})\b")
        self._remote = PhraseMatcher.from_terms(locations.remote_terms)
        self._hybrid = PhraseMatcher.from_terms(locations.hybrid_terms)
        self._onsite = PhraseMatcher.from_terms(locations.onsite_terms)

    def remote_type(self, text: str | None) -> RemoteType:
        if not text:
            return RemoteType.UNKNOWN
        if self._hybrid.find(text):
            return RemoteType.HYBRID
        if self._remote.find(text):
            return RemoteType.REMOTE
        if self._onsite.find(text):
            return RemoteType.ONSITE
        return RemoteType.UNKNOWN

    def _province(self, text: str) -> str | None:
        abbreviation = self._abbreviation_re.search(text)
        if abbreviation:
            return self._abbreviations[abbreviation.group(1)]
        names = self._province_names.find(text)
        return names[0].value if names else None

    def _foreign_country(self, text: str) -> str | None:
        found = self._foreign.find(text)
        if found:
            return found[0].value
        return "US" if self._us_state_code_re.search(text) else None

    def parse(self, text: str | None) -> LocationInfo:
        if not text or not text.strip():
            return LocationInfo()
        remote_type = self.remote_type(text)
        province = self._province(text)
        cities = self._cities.find(text)
        city = cities[0].value if cities else None
        if province or self._canada.find(text):
            country = "CA"
        else:
            country = self._foreign_country(text) or ("CA" if city else None)
        if country != "CA":
            return LocationInfo(country=country, remote_type=remote_type)
        if province is None and city is not None:
            province = self._city_province[city]
        return LocationInfo(city=city, province=province, country=country, remote_type=remote_type)

    def looks_like_location(self, segment: str) -> bool:
        """True if the segment is only location or work-mode words ("Toronto, ON", "Remote")."""
        without_abbr = self._abbreviation_re.sub(" ", segment)
        found = without_abbr != segment
        remainder = fold(without_abbr)
        for matcher in (
            self._province_names,
            self._cities,
            self._canada,
            self._foreign,
            self._remote,
            self._hybrid,
            self._onsite,
        ):
            reduced = matcher.remove(remainder)
            found = found or reduced != remainder
            remainder = reduced
        leftover = [w for w in re.findall(r"\w+", remainder) if w not in _FILLER_WORDS]
        return found and not leftover
