"""The layering rule, enforced rather than documented.

Views get querysets from repositories and call services for anything that
changes state; nothing outside `pharmacy/repositories/` names a manager. A
grep is a blunt instrument, but it is the one that still holds after the
next person adds a "small" query to a view.
"""

import io
import re
import tokenize
from pathlib import Path

import pytest

PHARMACY = Path(__file__).resolve().parent.parent
LAYERS_ABOVE_THE_REPOSITORY = ["views", "serializers", "services", "admin"]
FORBIDDEN = re.compile(r"\.objects\b|\.all_objects\b|select_for_update|serializer\.save\(")


def modules_in(package):
    return sorted((PHARMACY / package).glob("*.py"))


def code_lines(module):
    """(line number, text) for every token that is code: comments and
    docstrings may describe the rule without tripping it."""
    lines = {}
    for token in tokenize.generate_tokens(io.StringIO(module.read_text()).readline):
        if token.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        lines.setdefault(token.start[0], token.line.rstrip())
    return sorted(lines.items())


@pytest.mark.parametrize(
    "module",
    [m for package in LAYERS_ABOVE_THE_REPOSITORY for m in modules_in(package)],
    ids=lambda m: f"{m.parent.name}/{m.name}",
)
def test_nothing_above_the_repository_queries(module):
    offending = [
        f"{module.parent.name}/{module.name}:{number}: {line.strip()}"
        for number, line in code_lines(module)
        if FORBIDDEN.search(line)
    ]
    assert not offending, "\n".join(offending)


# Models that are never a resource of their own: each is written by exactly
# one service that is not named after it.
WRITTEN_THROUGH_ANOTHER_SERVICE = {
    "field_change": "services.history",
}


def test_every_model_has_a_repository_and_a_service():
    """A new resource means a matching module in both packages, the same way
    it already means one in models/, serializers/ and views/."""
    models = {m.stem for m in modules_in("models")} - {"__init__", "base"}
    repositories = {m.stem for m in modules_in("repositories")} - {"__init__", "base"}
    services = {m.stem for m in modules_in("services")} - {"__init__", "base", "context"}
    assert models <= repositories, models - repositories
    assert models - set(WRITTEN_THROUGH_ANOTHER_SERVICE) <= services, models - services
