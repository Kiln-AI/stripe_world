"""The fixture guide's landmarks, checked against the committed fixtures (architecture §3.2).

`docs/fixtures.md` is prose plus landmark blocks: a fenced SQL block whose info string is
`sql landmark=<fixture>/<name>`, followed by a fenced `json rows` block holding, one object per
line, the rows that query returns from the committed fixture. The guide cannot drift from the
data: `python -m fixtures_src.guide` checks every landmark and exits 1 on a difference, and
`--write` rewrites the `json rows` blocks from the fixtures (run it after a rebuild, then review
the diff). `tests/test_guide.py` runs the check.
"""

import json
import re
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import seahaven

__all__ = ["GUIDE", "Landmark", "check", "landmarks", "main", "render_rows", "rows_of", "write"]

GUIDE = Path(__file__).resolve().parent.parent / "docs" / "fixtures.md"

_FENCE = "```"
_BLOCK = re.compile(
    rf"{_FENCE}sql landmark=(?P<fixture>\w+)/(?P<name>\w+)\n(?P<sql>.*?){_FENCE}\n\n"
    rf"{_FENCE}json rows\n(?P<rows>.*?){_FENCE}",
    re.DOTALL,
)


@dataclass(frozen=True)
class Landmark:
    fixture: str
    name: str
    sql: str
    documented: str  # the `json rows` block's body, as written
    rows_start: int  # where that body sits in the guide's text
    rows_end: int

    @property
    def key(self) -> str:
        return f"{self.fixture}/{self.name}"


def landmarks(text: str) -> list[Landmark]:
    """Every landmark block in `text`, in order."""
    return [
        Landmark(
            fixture=match["fixture"],
            name=match["name"],
            sql=match["sql"].strip(),
            documented=match["rows"],
            rows_start=match.start("rows"),
            rows_end=match.end("rows"),
        )
        for match in _BLOCK.finditer(text)
    ]


def render_rows(rows: Sequence[dict[str, Any]]) -> str:
    """The `json rows` body for `rows`: a JSON array, one object per line."""
    if not rows:
        return "[]\n"
    lines = ",\n".join(f"  {json.dumps(row, ensure_ascii=False)}" for row in rows)
    return f"[\n{lines}\n]\n"


def rows_of(world: seahaven.World, found: Sequence[Landmark]) -> dict[str, list[dict[str, Any]]]:
    """What each landmark's query returns from its committed fixture, by landmark key."""
    out: dict[str, list[dict[str, Any]]] = {}
    for fixture in sorted({landmark.fixture for landmark in found}):
        with world.instance(fixture) as inst:
            db = inst.inspect()
            for landmark in found:
                if landmark.fixture == fixture:
                    out[landmark.key] = db.rows(landmark.sql)
    return out


def _problems(found: Sequence[Landmark], actual: dict[str, list[dict[str, Any]]]) -> Iterator[str]:
    seen: set[str] = set()
    for landmark in found:
        if landmark.key in seen:
            yield f"{landmark.key}: named twice"
        seen.add(landmark.key)
        if "order by" not in landmark.sql.lower():
            yield f"{landmark.key}: the query has no ORDER BY, so its rows have no fixed order"
        try:
            documented = json.loads(landmark.documented)
        except json.JSONDecodeError as error:
            yield f"{landmark.key}: the json rows block does not parse ({error})"
            continue
        returned = actual[landmark.key]
        if documented != returned:
            yield f"{landmark.key}: the guide shows {documented!r}, the fixture has {returned!r}"


def check(world: seahaven.World, text: str) -> list[str]:
    """Every difference between the guide's documented rows and the fixtures'."""
    found = landmarks(text)
    problems = list(_problems(found, rows_of(world, found)))
    opened = text.count(f"{_FENCE}sql landmark=")
    if opened != len(found):
        problems.append(
            f"{opened} landmark blocks open but {len(found)} parse: check each block's name"
            " and the json rows block that follows it"
        )
    return problems


def write(world: seahaven.World, text: str) -> str:
    """`text` with every `json rows` block rewritten from the fixtures."""
    found = landmarks(text)
    actual = rows_of(world, found)
    for landmark in reversed(found):
        body = render_rows(actual[landmark.key])
        text = text[: landmark.rows_start] + body + text[landmark.rows_end :]
    return text


def main(argv: list[str]) -> int:
    """`python -m fixtures_src.guide [--write]`."""
    from seahaven_stripe_world.world import world

    text = GUIDE.read_text(encoding="utf-8")
    if argv == ["--write"]:
        GUIDE.write_text(write(world, text), encoding="utf-8")
        return 0
    if argv:
        print("usage: python -m fixtures_src.guide [--write]")
        return 2
    problems = check(world, text)
    for problem in problems:
        print(problem)
    print(f"{len(landmarks(text))} landmarks, {len(problems)} differences")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
