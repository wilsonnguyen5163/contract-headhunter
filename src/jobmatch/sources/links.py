"""Read posting links from text files: one URL per line, "#" starts a comment.

# data/inbox/links/2026-10.txt
https://jobera.com/job/dept-senior-campaign-producer-6-month-ftc-c190dd5a/
https://www.linkedin.com/jobs/view/4012345678/   # from a job alert
"""

from pathlib import Path

from pydantic import BaseModel

LINKS_SUFFIX = ".txt"


class LinkEntry(BaseModel):
    file: str
    line: int
    url: str

    @property
    def label(self) -> str:
        return f"{Path(self.file).name}:{self.line}"


def collect_link_files(paths: list[Path]) -> tuple[list[Path], list[Path]]:
    """(files to read, missing paths). Folders give their *.txt files, except "_" names."""
    files: list[Path] = []
    missing: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(
                p for p in sorted(path.glob(f"*{LINKS_SUFFIX}")) if not p.name.startswith("_")
            )
        elif path.is_file():
            files.append(path)
        else:
            missing.append(path)
    return files, missing


def read_links(path: Path) -> list[LinkEntry]:
    """A line starting with "#" is a comment; after the URL, anything past a space is ignored
    (so "https://x.ca/job/1   # from an alert" works, and URLs may contain "#")."""
    entries: list[LinkEntry] = []
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            entries.append(LinkEntry(file=str(path), line=number, url=stripped.split()[0]))
    return entries
