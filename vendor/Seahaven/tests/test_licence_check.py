"""The dependency licence gate: nothing Seahaven ships is copyleft."""

from importlib import metadata
from typing import Any

from scripts.check_licences import (
    Licence,
    audit,
    declared_extras,
    licence_of,
    runtime_licences,
)

COPYLEFT = """Metadata-Version: 2.4
Name: copyleftlib
Version: 1.0
License-Expression: GPL-3.0-only
"""


class FakeDistribution(metadata.Distribution):
    """A distribution that exists only as its metadata."""

    def __init__(self, text: str) -> None:
        self._text = text

    def read_text(self, filename: str) -> str | None:
        return self._text if filename == "METADATA" else None

    def locate_file(self, path: Any) -> Any:
        raise NotImplementedError


def test_the_shipped_closure_is_allowed() -> None:
    assert audit("seahaven") == []


def test_the_closure_covers_every_declared_extra() -> None:
    assert declared_extras("seahaven") == ["serve"]

    names = {licence.distribution.lower() for licence in runtime_licences("seahaven")}

    assert {"apsw", "pydantic", "pyyaml"} <= names
    # `serve` is a runtime extra: CI installs it, users install it, so the gate
    # reads it. The tooling is a dependency group, which is not in the metadata
    # at all and is distributed with nothing.
    assert {"openenv", "fastmcp", "numpy", "pillow"} <= names
    assert {"pytest", "ruff", "ty"} & names == set()


def test_the_base_closure_is_the_one_without_extras() -> None:
    base = {licence.distribution.lower() for licence in runtime_licences("seahaven", extras=[])}

    assert {"apsw", "pydantic", "pyyaml"} <= base
    assert "openenv" not in base


def test_every_way_of_declaring_a_licence_is_read() -> None:
    by_name = {licence.distribution.lower(): licence for licence in runtime_licences("seahaven")}

    # A PEP 639 expression, the legacy classifiers, and the legacy free-text
    # field: all three are in the closure today.
    assert by_name["pydantic"].expression == "MIT"
    assert by_name["pyyaml"].expression == "MIT"
    assert by_name["apsw"].expression == "any-OSI"
    # certifi declares MPL-2.0 only as a classifier, so the mapping has to carry
    # it or the gate reads the classifier line itself and fails on a licence it
    # allows.
    assert by_name["certifi"].expression == "MPL-2.0"


def test_the_extras_closure_carries_the_licences_this_rule_exists_for() -> None:
    by_name = {licence.distribution.lower(): licence for licence in runtime_licences("seahaven")}

    assert by_name["numpy"].expression == "BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0"
    assert by_name["orjson"].expression == "MPL-2.0 AND (Apache-2.0 OR MIT)"
    assert by_name["pillow"].expression == "MIT-CMU"
    assert all(licence.allowed() for licence in by_name.values())


def test_a_requirement_that_is_not_installed_cannot_be_cleared() -> None:
    [licence] = runtime_licences("no-such-distribution")

    assert licence == Licence("no-such-distribution", "unknown", "<not installed>")
    assert not licence.allowed()


def test_copyleft_is_rejected() -> None:
    licence = licence_of(FakeDistribution(COPYLEFT))

    assert licence == Licence("copyleftlib", "1.0", "GPL-3.0-only")
    assert not licence.allowed()


def test_every_spelling_of_the_refused_families_is_refused() -> None:
    for expression in (
        "GPL-2.0",
        "GPL-3.0-only",
        "GPL-3.0-or-later",
        "AGPL-3.0",
        "AGPL-3.0-or-later",
        "LGPL-2.1",
        "LGPL-2.1-only",
        "LGPL-3.0-or-later",
        "gpl-3.0-only",
        "GPL-2.0-only WITH Classpath-exception-2.0",
        "MIT AND LGPL-3.0-only",
    ):
        assert not Licence("x", "1", expression).allowed(), expression


def test_file_scope_copyleft_and_public_domain_are_allowed() -> None:
    # MPL-2.0 is copyleft per file and does not reach a linking or calling
    # process; CC0-1.0 grants everything and asks nothing.
    assert Licence("x", "1", "MPL-2.0").allowed()
    assert Licence("x", "1", "MPL-2.0 AND MIT").allowed()
    assert Licence("x", "1", "MPL-2.0 AND (Apache-2.0 OR MIT)").allowed()
    assert Licence("x", "1", "CC0-1.0").allowed()
    assert Licence("x", "1", "BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0").allowed()


def test_a_choice_of_licences_needs_a_person() -> None:
    # Every branch of an `OR` has to be acceptable, because the script does not
    # get to pick the good one quietly: a person makes that choice and records
    # it. Both of these are in the closure today and both branches are fine.
    assert Licence("x", "1", "Apache-2.0 OR BSD-3-Clause").allowed()
    assert Licence("x", "1", "MIT AND Apache-2.0").allowed()
    # One refused branch refuses the whole expression, even though a vendor
    # could lawfully take the other one.
    assert not Licence("x", "1", "GPL-3.0-only OR MIT").allowed()
    assert not Licence("x", "1", "MIT OR SSPL-1.0").allowed()


def test_an_undeclared_licence_is_not_allowed() -> None:
    assert not Licence("x", "1", "<none declared>").allowed()
    assert not Licence("x", "1", "").allowed()
    assert not Licence("x", "1", "<licence text>").allowed()


def test_an_unrecognised_licence_is_refused_rather_than_assumed() -> None:
    # The gate is an allowlist. A licence nobody has classified -- a new SPDX
    # identifier, a vendor's own name for one, a classifier line that fell
    # through the mapping -- fails and is read by a person.
    assert not Licence("x", "1", "SSPL-1.0").allowed()
    assert not Licence("x", "1", "Elastic-2.0").allowed()
    assert not Licence("x", "1", "License :: OSI Approved :: Artistic License").allowed()
    assert not Licence("x", "1", "MIT AND Some-New-Thing-1.0").allowed()
