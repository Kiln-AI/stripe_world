r"""How every fixture in `fixtures/` is made. Committed, because it is the recipe.

A fixture is a binary artifact, and a binary artifact with no source is one
nobody can change. This module is that source: one function per fixture, each
taking a live instance and filling it, which is exactly the shape the CLI calls:

    uv run seahaven fixture freeze small_startup \
        --now 2026-06-01T09:00:00.000Z \
        --run fixtures_src.generate:small_startup \
        --description "..."

**`--now` is not optional here, whatever the CLI's default says.** `freeze`
starts from a blank instance, and a blank instance with no `--now` takes the wall
clock -- so the command without the flag mints a fixture dated today and quietly
breaks the invariant two paragraphs below. It has to be `NOW`, spelled out,
because the CLI imports one function from this module and cannot see the
constant. `fork` needs no `--now`: a fork inherits its parent's clock, which is
the point of forking.

Running this module does the same thing itself, from the repository root, passes
`NOW` for you and carries the descriptions with it, which is why it is the way to
rebuild a fixture here:

    uv run python worlds/projecttracker/fixtures_src/generate.py small_startup

`build` and `main` both take `world=`, defaulting to this package's. A fixture is
frozen into the world it is handed, so that is how `tests/test_fixtures.py`
rebuilds all three into a temporary directory to compare them with the committed
bytes -- rather than moving the imported world's fixtures directory, which every
other caller in the process would see.

Every fixture is frozen from a *blank* instance at `NOW`, so the tracker's clock
is the same instant in all of them and a scenario written against one reads the
same as a scenario written against another. Nothing here reads the wall clock,
and nothing here is random outside `ctx.ids`: the whole of the variation below
comes from `ctx.ids.random`, seeded from the world's name, so two runs of this
file produce the same tracker down to the identifiers.

**Both write paths are used, deliberately.** The bulk of every fixture goes in
through `inst.bulk()`, which is one transaction and no argument validation --
tens of thousands of rows through `instance.call` would spend their time on the
call path. The last few writes of each fixture go through the tools instead, so
the fixture exercises the path an agent will use, and so the team's issue-key
counter ends where the tools left it rather than where a raw `INSERT` did.
"""

import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import seahaven

# The instant every fixture's clock is frozen at. Chosen once and never moved:
# it is baked into every sidecar, and moving it would invalidate every scenario
# written against a fixture's dates.
NOW = "2026-06-01T09:00:00.000Z"

# How far ahead of `NOW` a due date may fall. Everything else a fixture holds is
# in the past, which is what `_assert_within_span` checks.
DUE_HORIZON_DAYS = 45

# How many of each fixture's issues and comments are written through the tools
# rather than through `bulk()`. Small on purpose: the point is that the path is
# exercised and that the key counter is the tools', not that the fixture is built
# a call at a time.
THROUGH_THE_TOOLS = 3

# How long before the end of a fixture's span its teams and then its projects
# came into being, in days. Everything else is dated after the parent it belongs
# to, which is what `_assert_within_span` checks row by row: a tracker where a
# third of the issues were filed by people who had not joined yet is a tracker a
# reporting eval will grade strangely.
TEAM_AGE = 0
PROJECT_AGE = 2


@dataclass(frozen=True)
class Workspace:
    """What one populated fixture holds, as numbers.

    The generator below reads these and nothing else, so the two populated
    fixtures are the same tracker at two sizes rather than two scripts that drift
    apart.
    """

    teams: tuple[tuple[str, str], ...]
    users: int
    admins: int
    viewers: int
    projects: int
    done_projects: int
    issues: int
    comments: int
    labels: int
    span_days: int
    # The share of issues carrying an extra assignee change and an extra status
    # change in the trail, beyond the one each its current state implies.
    reassigned: float
    transitioned: float


WORKSPACES = {
    "small_startup": Workspace(
        teams=(("ENG", "Engineering"),),
        users=3,
        admins=1,
        viewers=0,
        projects=2,
        done_projects=0,
        issues=40,
        comments=60,
        labels=6,
        span_days=60,
        reassigned=0.0,
        transitioned=0.0,
    ),
    "agency": Workspace(
        teams=(("ENG", "Engineering"), ("DES", "Design"), ("OPS", "Operations")),
        users=12,
        admins=2,
        viewers=2,
        projects=9,
        done_projects=1,
        issues=600,
        comments=1500,
        labels=30,
        span_days=180,
        reassigned=0.20,
        transitioned=0.25,
    ),
}

# What an eval author reads when choosing a fixture. Prose about the data, not
# about the schema: what is in it, and what it is good for.
DESCRIPTIONS = {
    "empty": (
        "The tracker's schema with no rows. Start here to write a history, or to test setup flows."
    ),
    "small_startup": (
        "A three-person startup's tracker: one engineering team, two active projects, forty "
        "issues spread over the last two months, a handful of labels. Good for single-team "
        "triage, assignment and status-change scenarios."
    ),
    "agency": (
        "A twelve-person agency: three teams, nine projects including a finished one, six "
        "hundred issues with a six-month history, comments, labels and an audit trail. Good for "
        "cross-team queries, reporting, bulk operations and search."
    ),
}

# The vocabulary every title, description and comment is composed from. Fixed
# tables rather than a text generator, because a fixture's prose has two jobs: it
# has to read like a tracker's, and it has to be the same prose on every run.
# Nothing here names a real product, a real company or a real person.
_SUBJECTS = (
    "the invite flow",
    "the billing page",
    "the audit log",
    "the search index",
    "the webhook retry",
    "the CSV export",
    "the session cookie",
    "the avatar upload",
    "the timezone picker",
    "the keyboard shortcuts",
    "the notification digest",
    "the archive view",
    "the sandbox banner",
    "the onboarding checklist",
    "the API rate limiter",
    "the permissions matrix",
)
_PROBLEMS = (
    "drops the last character",
    "times out after a minute",
    "shows yesterday's numbers",
    "double-counts archived rows",
    "loses state on reload",
    "renders behind the header",
    "ignores the filter",
    "returns a 500 under load",
    "is unreachable by keyboard",
    "leaks a stack trace",
)
_ACTIONS = (
    "Rewrite",
    "Investigate",
    "Add a test for",
    "Document",
    "Roll back",
    "Instrument",
    "Simplify",
    "Cache",
)
_COMMENTS = (
    "Reproduced on a clean workspace.",
    "This is the same root cause as the export bug.",
    "Pushed a fix; please re-check on your side.",
    "Moving this out of the milestone for now.",
    "I cannot reproduce it -- what browser?",
    "Added a regression test with the fix.",
    "Needs a decision from design before we start.",
    "Closing: the underlying API changed and this went away.",
    "Bumping the priority, three people hit this today.",
    "Split the rest into a follow-up.",
)
_LABEL_NAMES = (
    "bug",
    "feature",
    "chore",
    "docs",
    "regression",
    "customer",
    "performance",
    "security",
    "design",
    "infra",
)
_LABEL_COLOURS = (
    "#e11d48",
    "#2563eb",
    "#16a34a",
    "#a855f7",
    "#f59e0b",
    "#0891b2",
    "#be123c",
    "#4338ca",
    "#15803d",
    "#c2410c",
)
_PROJECT_NAMES = (
    "Platform",
    "Mobile",
    "Growth",
    "Billing",
    "Infrastructure",
    "Onboarding",
    "Reporting",
    "Partner API",
    "Design System",
)
_FIRST_NAMES = (
    "Ada",
    "Bo",
    "Cai",
    "Dara",
    "Eli",
    "Fen",
    "Gus",
    "Hana",
    "Ines",
    "Jo",
    "Kai",
    "Lena",
)
_SURNAMES = (
    "Okafor",
    "Lindqvist",
    "Moreau",
    "Tanaka",
    "Silva",
    "Novak",
    "Haddad",
    "Berg",
    "Costa",
    "Ivanov",
    "Rahman",
    "Weber",
)

# The statuses a bulk-written issue can be in, and how often. Weighted like a real
# backlog: most of it is not being worked on, and most of what is finished is
# done rather than cancelled.
_STATUS_WEIGHTS = (
    ("backlog", 34),
    ("todo", 20),
    ("in_progress", 12),
    ("done", 28),
    ("canceled", 6),
)
_CLOSED = frozenset({"done", "canceled"})

# Every timestamp column of this world, and how far from `NOW` it may be. The
# span is the fixture's; the ceiling is `NOW` for everything except a due date,
# which is the one thing in a tracker that is allowed to be in the future.
_TIMESTAMPS = (
    ("users", "created_at", 0),
    ("teams", "created_at", 0),
    ("team_members", "joined_at", 0),
    ("projects", "created_at", 0),
    ("issues", "created_at", 0),
    ("issues", "updated_at", 0),
    ("issues", "archived_at", 0),
    ("issues", "due_at", DUE_HORIZON_DAYS),
    ("comments", "created_at", 0),
    ("issue_events", "created_at", 0),
)


def empty(inst: seahaven.Instance) -> None:
    """`empty`: the schema and nothing else.

    Deliberately does no work. It exists so that `empty` is made the same way
    every other fixture is -- freeze a blank instance through this module -- and
    so the fixture that later ones are forked from has a source like the rest.
    """


def small_startup(inst: seahaven.Instance) -> None:
    """`small_startup`: one team, two projects, forty issues over two months."""
    _populate(inst, WORKSPACES["small_startup"])


def agency(inst: seahaven.Instance) -> None:
    """`agency`: three teams, nine projects, six hundred issues over six months."""
    _populate(inst, WORKSPACES["agency"])


# Every fixture this module can build, by id. `seahaven fixture freeze` names one
# of these functions directly; `main` below looks it up here.
BUILDERS = {"empty": empty, "small_startup": small_startup, "agency": agency}


def build(fixture_id: str, *, world: seahaven.World | None = None) -> seahaven.Fixture:
    """Freeze `fixture_id` from a blank instance at `NOW`, and return it.

    `world` is which world to freeze into, defaulting to this package's. A
    fixture lands in `world.fixtures_dir`, so this is the seam a test builds
    through: `tests/test_fixtures.py` rebuilds all three into a temporary
    directory to compare them with the committed bytes, and hands in a world
    pointed there rather than moving the imported one's -- which is a
    process-wide change every other test in the run would see.

    The instance is destroyed whether the build succeeds or not, and a failure
    leaves no fixture directory behind: `freeze` publishes by rename.
    """
    into = world if world is not None else _package_world()
    builder = BUILDERS[fixture_id]
    with into.instance(None, now=NOW) as inst:
        builder(inst)
        return inst.freeze(fixture_id, DESCRIPTIONS[fixture_id])


def _package_world() -> seahaven.World:
    """This world, imported when a build asks for it and not before.

    `seahaven fixture freeze --run` imports this module for one function, and a
    builder should not cost a world import until it is called. Spelled through
    `world.py` rather than through the package attribute, which is a `World`
    shadowing the module of that name.
    """
    from projecttracker.world import world

    return world


def main(argv: list[str], *, world: seahaven.World | None = None) -> int:
    """`python generate.py <fixture-id>...`, or with no arguments, every fixture.

    `world` is `build`'s, passed straight through, so the whole set can be
    rebuilt somewhere other than this checkout's `fixtures/`.

    Refuses to overwrite: `freeze` fails if the directory exists, because a
    fixture is immutable once it is published. Rebuilding one means deleting it
    first, deliberately, and committing the new bytes.
    """
    ids = argv or sorted(BUILDERS)
    unknown = [fixture_id for fixture_id in ids if fixture_id not in BUILDERS]
    if unknown:
        print(f"no such fixture: {', '.join(unknown)}", file=sys.stderr)
        print(f"known fixtures: {', '.join(sorted(BUILDERS))}", file=sys.stderr)
        return 1
    for fixture_id in ids:
        fixture = build(fixture_id, world=world)
        print(f"froze {fixture.id} at {fixture.now} -> {fixture.dir}")
    return 0


def _populate(inst: seahaven.Instance, spec: Workspace) -> None:
    """Fill an instance with a whole workspace: rows in bulk, the last few by tool."""
    with inst.bulk() as ctx:
        people, born = _write_users(ctx, spec)
        teams = _write_teams(ctx, spec)
        membership = _write_memberships(ctx, people, born, teams)
        projects = _write_projects(ctx, spec, teams)
        labels = _write_labels(ctx, spec, teams)
        issues, created_days = _write_issues(
            ctx, spec, _Workforce(people, born, membership), teams, projects, labels
        )
        _write_comments(
            ctx, spec, _Workforce(people, born, membership), projects, issues, created_days
        )
        _hand_the_counters_to_the_tools(ctx, teams, projects, issues)
    _write_the_last_few_through_the_tools(inst, people, projects)
    _assert_within_span(inst, spec)


@dataclass(frozen=True)
class _Workforce:
    """The workspace's people, when each joined, and which teams each is on.

    Three lists that are always used together -- every row that names a person
    has to pick one who had joined by then *and* is on the team the row belongs
    to -- so they travel as one value rather than as three parameters that could
    be passed in the wrong order.
    """

    people: Sequence[dict[str, Any]]
    born: Sequence[float]
    membership: dict[str, frozenset[str]]

    def on(self, team_id: str, at: float) -> list[dict[str, Any]]:
        """Who was on `team_id` `at` days before `now`.

        `born` is in days before `now`, so "had joined" is `born >= at`. The
        first person is an admin, is on every team, and joins with the teams
        themselves, so the answer is never empty for a row dated inside the span.
        """
        return [
            person
            for person, joined in zip(self.people, self.born, strict=True)
            if joined >= at and team_id in self.membership[str(person["id"])]
        ]


def _write_users(ctx: seahaven.Ctx, spec: Workspace) -> tuple[list[dict[str, Any]], list[float]]:
    """The workspace's people, oldest first, admins first.

    Admins are created first so that `startup.py`'s viewer fallback -- the
    earliest-created admin -- resolves to the person the fixture's description
    calls the viewer.

    The second half of the answer is when each of them joined, in days before
    `now`. Every later row that names a person is drawn from the people who
    already existed at its own instant, and this list is what "already existed"
    is read from.
    """
    people: list[dict[str, Any]] = []
    born: list[float] = []
    for index in range(spec.users):
        if index < spec.admins:
            role = "admin"
        elif index < spec.admins + spec.viewers:
            role = "viewer"
        else:
            role = "member"
        name = f"{_FIRST_NAMES[index % len(_FIRST_NAMES)]} {_SURNAMES[index % len(_SURNAMES)]}"
        # Spread over the first tenth of the span: a workspace's people join
        # before most of its work happens, and the first of them joins with the
        # teams themselves.
        joined = float(spec.span_days - index * (spec.span_days // 20 or 1))
        born.append(joined)
        people.append(
            {
                "id": ctx.ids.uuid(),
                "email": f"{name.split(' ')[0].lower()}{index}@seahaven.invalid",
                "name": name,
                "role": role,
                "created_at": _stamp(ctx, joined),
            }
        )
    _insert(ctx, "users", people)
    return people, born


def _write_teams(ctx: seahaven.Ctx, spec: Workspace) -> list[dict[str, Any]]:
    teams = [
        {
            "id": ctx.ids.uuid(),
            "key": key,
            "name": name,
            "issue_counter": 0,
            "created_at": _stamp(ctx, spec.span_days - TEAM_AGE),
        }
        for key, name in spec.teams
    ]
    _insert(ctx, "teams", teams)
    return teams


def _write_memberships(
    ctx: seahaven.Ctx,
    people: Sequence[dict[str, Any]],
    born: Sequence[float],
    teams: Sequence[dict[str, Any]],
) -> dict[str, frozenset[str]]:
    """Admins are on every team; everyone else is on one, round robin.

    A membership is dated when the person joined the workspace, so it is never
    older than either end of it -- the teams are all as old as the span, and
    nobody is on a team before they exist.

    The answer is who is on what, which every later row needs: a tracker where
    half the issues in `ENG` were filed by people who are not in `ENG` makes "how
    much did ENG file" about half noise, and `agency` exists to be asked that.
    """
    rows = []
    membership: dict[str, frozenset[str]] = {}
    for index, person in enumerate(people):
        joined = teams if person["role"] == "admin" else [teams[index % len(teams)]]
        membership[str(person["id"])] = frozenset(str(team["id"]) for team in joined)
        rows += [(team["id"], person["id"], _stamp(ctx, born[index])) for team in joined]
    ctx.db.executemany(
        "INSERT INTO team_members (team_id, user_id, joined_at) VALUES (?, ?, ?)", rows
    )
    return membership


def _write_projects(
    ctx: seahaven.Ctx, spec: Workspace, teams: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Projects spread over the teams, the last `done_projects` of them finished."""
    projects = []
    for index in range(spec.projects):
        projects.append(
            {
                "id": ctx.ids.uuid(),
                "team_id": teams[index % len(teams)]["id"],
                "name": _PROJECT_NAMES[index % len(_PROJECT_NAMES)],
                "state": "done" if index >= spec.projects - spec.done_projects else "active",
                "created_at": _stamp(ctx, spec.span_days - PROJECT_AGE),
            }
        )
    _insert(ctx, "projects", projects)
    return projects


def _write_labels(
    ctx: seahaven.Ctx, spec: Workspace, teams: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    """A team's share of the labels, named from the vocabulary and never repeated in a team."""
    per_team = spec.labels // len(teams)
    labels = []
    for team in teams:
        for index in range(per_team):
            labels.append(
                {
                    "id": ctx.ids.uuid(),
                    "team_id": team["id"],
                    "name": _label_name(index),
                    "color": _LABEL_COLOURS[index % len(_LABEL_COLOURS)],
                }
            )
    _insert(ctx, "labels", labels)
    return labels


def _write_issues(
    ctx: seahaven.Ctx,
    spec: Workspace,
    workforce: _Workforce,
    teams: Sequence[dict[str, Any]],
    projects: Sequence[dict[str, Any]],
    labels: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    """The issues, their labels and their trail, all in one pass.

    One pass because the three are one story: an issue's `updated_at` is when the
    last thing in its trail happened, and its labels are drawn from its own team's
    set. Building them separately would mean reading the issues back to find out
    which team each belonged to.

    Every issue is filed after its project existed, by someone who was on the
    owning team by then, and every event on it is acted by someone on that team
    too. The team boundary is the same one the labels keep: an eval that joins
    through `team_members` to ask what a team filed should get that team's work
    and not the workspace's.

    The second half of the answer is when each issue was created, in days before
    `now`, which is what `_write_comments` needs to place a comment after the
    issue it is on. It is carried rather than parsed back out of the stamp: the
    number is what the stamp was made from.
    """
    labels_of_team: dict[str, list[dict[str, Any]]] = {}
    for label in labels:
        labels_of_team.setdefault(str(label["team_id"]), []).append(label)
    key_of_team = {str(team["id"]): str(team["key"]) for team in teams}
    counters = dict.fromkeys(key_of_team, 0)

    issues: list[dict[str, Any]] = []
    created_days: dict[str, float] = {}
    issue_labels: list[tuple[str, str]] = []
    events: list[tuple[Any, ...]] = []
    for index in range(spec.issues - THROUGH_THE_TOOLS):
        project = projects[index % len(projects)]
        team_id = str(project["team_id"])
        counters[team_id] += 1
        # Never older than the project it is filed in, which is itself never
        # older than the team that owns it.
        created = ctx.ids.random.uniform(1.0, float(spec.span_days - PROJECT_AGE))
        colleagues = workforce.on(team_id, created)
        status = _weighted(ctx, _STATUS_WEIGHTS)
        priority = ctx.ids.random.randrange(5)
        creator = _pick(ctx, colleagues)
        assignee = None if status in _CLOSED else _maybe(ctx, colleagues, 0.6)
        issue_id = ctx.ids.uuid()

        trail = _trail(
            ctx,
            spec,
            colleagues,
            creator=str(creator["id"]),
            status=status,
            priority=priority,
            assignee=assignee,
        )
        moments = _moments(ctx, created, len(trail))

        created_days[issue_id] = created
        issues.append(
            {
                "id": issue_id,
                "project_id": project["id"],
                "key": f"{key_of_team[team_id]}-{counters[team_id]}",
                "title": _title(ctx),
                "description": _description(ctx),
                "status": status,
                "priority": priority,
                "assignee_id": assignee,
                "creator_id": creator["id"],
                "created_at": _stamp(ctx, created),
                "updated_at": _stamp(ctx, moments[-1]),
                "due_at": _due_at(ctx, created),
                "archived_at": None,
            }
        )
        issue_labels += [
            (issue_id, str(label["id"]))
            for label in _sample(ctx, labels_of_team.get(team_id, []), ctx.ids.random.randrange(4))
        ]
        events += [
            (
                ctx.ids.uuid(),
                issue_id,
                actor,
                kind,
                json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
                _stamp(ctx, moment),
            )
            for (kind, payload, actor), moment in zip(trail, moments, strict=True)
        ]

    _insert(ctx, "issues", issues)
    ctx.db.executemany(
        "INSERT INTO issue_labels (issue_id, label_id) VALUES (?, ?)", sorted(set(issue_labels))
    )
    ctx.db.executemany(
        "INSERT INTO issue_events (id, issue_id, actor_id, kind, payload, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        events,
    )
    return issues, created_days


def _trail(
    ctx: seahaven.Ctx,
    spec: Workspace,
    colleagues: Sequence[dict[str, Any]],
    *,
    creator: str,
    status: str,
    priority: int,
    assignee: str | None,
) -> list[tuple[str, dict[str, Any], str]]:
    """How an issue got to the state it is in: the events, oldest first.

    Built forward from `backlog` and unassigned, so every event's `from` is the
    previous event's `to` and the last one leaves the issue in exactly the state
    its row records. `spec.transitioned` and `spec.reassigned` add a step in the
    middle -- an issue that moved and then moved again, or changed hands -- which
    is what makes a fixture's trail worth querying rather than a restatement of
    the row.

    The creation is acted by whoever filed it and everything after it by whoever
    was around at the time, which need not be the same person: "who moved the
    most issues to done" is a question a tracker is asked, and a trail whose actor
    is always the filer answers it wrongly and convincingly.

    The payload shapes are the ones `tools/_events.py` writes, and they have to
    stay the same shapes: an eval reads the trail without caring which of the two
    write paths put a row in it.
    """
    at_status = "backlog"
    at_assignee: str | None = None
    opened = {"status": at_status, "priority": priority, "assignee_id": at_assignee}
    trail: list[tuple[str, dict[str, Any], str]] = [("created", opened, creator)]

    def move_to(next_status: str) -> None:
        nonlocal at_status
        if next_status != at_status:
            actor = str(_pick(ctx, colleagues)["id"])
            trail.append(("status", {"from": at_status, "to": next_status}, actor))
            at_status = next_status

    def hand_to(next_assignee: str | None) -> None:
        nonlocal at_assignee
        if next_assignee != at_assignee:
            actor = str(_pick(ctx, colleagues)["id"])
            trail.append(("assignee", {"from": at_assignee, "to": next_assignee}, actor))
            at_assignee = next_assignee

    if ctx.ids.random.random() < spec.transitioned:
        move_to(_pick(ctx, ("todo", "in_progress")))
    if ctx.ids.random.random() < spec.reassigned:
        hand_to(str(_pick(ctx, colleagues)["id"]))
    hand_to(assignee)
    move_to(status)
    if status in _CLOSED:
        # The product's rule, and the trail has to show it: closing an issue is
        # also what took it off whoever was working on it.
        hand_to(None)
    return trail


def _write_comments(
    ctx: seahaven.Ctx,
    spec: Workspace,
    workforce: _Workforce,
    projects: Sequence[dict[str, Any]],
    issues: Sequence[dict[str, Any]],
    created_days: dict[str, float],
) -> None:
    """Comments on the issues, each with the `comment` event that recorded it.

    A comment is dated after the issue it is on and is written by someone who was
    on that issue's team by then, for the same reason every other row is.
    """
    team_of_project = {str(project["id"]): str(project["team_id"]) for project in projects}
    comments: list[tuple[Any, ...]] = []
    events: list[tuple[Any, ...]] = []
    for index in range(spec.comments - THROUGH_THE_TOOLS):
        issue = issues[index % len(issues)]
        moment = ctx.ids.random.uniform(0.0, created_days[str(issue["id"])])
        author = _pick(ctx, workforce.on(team_of_project[str(issue["project_id"])], moment))
        comment_id = ctx.ids.uuid()
        stamp = _stamp(ctx, moment)
        comments.append((comment_id, issue["id"], author["id"], _pick(ctx, _COMMENTS), stamp))
        events.append(
            (
                ctx.ids.uuid(),
                issue["id"],
                author["id"],
                "comment",
                json.dumps({"comment_id": comment_id}, separators=(",", ":"), ensure_ascii=False),
                stamp,
            )
        )
    ctx.db.executemany(
        "INSERT INTO comments (id, issue_id, author_id, body, created_at) VALUES (?, ?, ?, ?, ?)",
        comments,
    )
    ctx.db.executemany(
        "INSERT INTO issue_events (id, issue_id, actor_id, kind, payload, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        events,
    )


def _hand_the_counters_to_the_tools(
    ctx: seahaven.Ctx,
    teams: Sequence[dict[str, Any]],
    projects: Sequence[dict[str, Any]],
    issues: Sequence[dict[str, Any]],
) -> None:
    """Set each team's `issue_counter` to the keys the bulk load consumed.

    `create_issue` mints from this counter, so a fixture that bulk-wrote forty
    issues and left it at zero would have the next tool call mint `ENG-1` again --
    and `issues.key UNIQUE` would refuse it. This is the seam between the two
    write paths, and it is one statement wide.
    """
    team_of = {project["id"]: project["team_id"] for project in projects}
    minted: dict[Any, int] = {team["id"]: 0 for team in teams}
    for issue in issues:
        minted[team_of[issue["project_id"]]] += 1
    ctx.db.executemany(
        "UPDATE teams SET issue_counter = ? WHERE id = ?",
        [(count, team_id) for team_id, count in minted.items()],
    )


def _write_the_last_few_through_the_tools(
    inst: seahaven.Instance,
    people: Sequence[dict[str, Any]],
    projects: Sequence[dict[str, Any]],
) -> None:
    """The tail of the fixture, written the way an agent writes.

    Outside `bulk()`, so each of these is a real call: arguments validated, the
    middleware chain run, the key minted from the counter the bulk load left, the
    trail written by `record_event`. If the tools and the bulk load ever disagree
    about the shape of a row, the fixture is where it shows.
    """
    admin = people[0]["id"]
    project = projects[0]["id"]
    for index in range(THROUGH_THE_TOOLS):
        issue = inst.call(
            "create_issue",
            project_id=project,
            title=f"{_ACTIONS[index % len(_ACTIONS)]} {_SUBJECTS[index % len(_SUBJECTS)]}",
            description="Filed during the fixture's final pass, through the tools.",
            status="todo",
            priority=2,
            actor_id=admin,
        )
        inst.call(
            "add_comment",
            issue_id=issue["id"],
            body=_COMMENTS[index % len(_COMMENTS)],
            actor_id=admin,
        )
        if index == 0:
            inst.call("assign_issue", issue_id=issue["id"], assignee_id=admin, actor_id=admin)
        if index == 1:
            inst.call(
                "transition_issue", issue_id=issue["id"], status="in_progress", actor_id=admin
            )


def _assert_within_span(inst: seahaven.Instance, spec: Workspace) -> None:
    """Every timestamp in the fixture is inside the window the fixture claims.

    The window is `[now - span, now]`, and `due_at` is the one exception: a due
    date is the only thing in a tracker that points forward, and it may be up to
    `DUE_HORIZON_DAYS` ahead. A fixture that broke this would be one where "the
    last two months" in its description was not true of its rows, which is the
    kind of thing an eval discovers by grading wrongly rather than by failing.
    """
    db = inst.inspect()
    floor = _shift(inst.clock, spec.span_days)
    now = inst.clock.iso()
    for table, column, horizon in _TIMESTAMPS:
        found = db.one(
            f"SELECT min({column}) AS low, max({column}) AS high FROM {table}"
            f" WHERE {column} IS NOT NULL"
        )
        assert found is not None
        if found["low"] is None:
            continue
        ceiling = now if horizon == 0 else _shift(inst.clock, -horizon)
        assert floor <= found["low"], f"{table}.{column} starts at {found['low']}, before {floor}"
        assert found["high"] <= ceiling, f"{table}.{column} reaches {found['high']}, past {ceiling}"
    _assert_nothing_predates_its_parent(inst)


# Rows that must not be older than something else: a description, and a query
# counting the ones that are. A window check alone passes a tracker where a third
# of the issues were filed by people who had not joined yet, which is exactly the
# kind of thing a reporting eval grades strangely and nobody notices.
_PARENTS = (
    (
        "a membership older than the person",
        "SELECT count(*) AS n FROM team_members JOIN users ON users.id = team_members.user_id"
        " WHERE team_members.joined_at < users.created_at",
    ),
    (
        "a membership older than the team",
        "SELECT count(*) AS n FROM team_members JOIN teams ON teams.id = team_members.team_id"
        " WHERE team_members.joined_at < teams.created_at",
    ),
    (
        "a project older than its team",
        "SELECT count(*) AS n FROM projects JOIN teams ON teams.id = projects.team_id"
        " WHERE projects.created_at < teams.created_at",
    ),
    (
        "an issue older than its project",
        "SELECT count(*) AS n FROM issues JOIN projects ON projects.id = issues.project_id"
        " WHERE issues.created_at < projects.created_at",
    ),
    (
        "an issue older than the person who filed it",
        "SELECT count(*) AS n FROM issues JOIN users ON users.id = issues.creator_id"
        " WHERE issues.created_at < users.created_at",
    ),
    (
        "an issue assigned to someone who had not joined",
        "SELECT count(*) AS n FROM issues JOIN users ON users.id = issues.assignee_id"
        " WHERE issues.created_at < users.created_at",
    ),
    (
        "an issue updated before it was filed",
        "SELECT count(*) AS n FROM issues WHERE updated_at < created_at",
    ),
    (
        "a due date older than the issue",
        "SELECT count(*) AS n FROM issues WHERE due_at IS NOT NULL AND due_at < created_at",
    ),
    (
        "a comment older than its issue",
        "SELECT count(*) AS n FROM comments JOIN issues ON issues.id = comments.issue_id"
        " WHERE comments.created_at < issues.created_at",
    ),
    (
        "a comment by someone who had not joined",
        "SELECT count(*) AS n FROM comments JOIN users ON users.id = comments.author_id"
        " WHERE comments.created_at < users.created_at",
    ),
    (
        "an event older than its issue",
        "SELECT count(*) AS n FROM issue_events JOIN issues ON issues.id = issue_events.issue_id"
        " WHERE issue_events.created_at < issues.created_at",
    ),
    (
        "an event acted by someone who had not joined",
        "SELECT count(*) AS n FROM issue_events JOIN users ON users.id = issue_events.actor_id"
        " WHERE issue_events.created_at < users.created_at",
    ),
    (
        "an issue filed by someone who is not on its team",
        "SELECT count(*) AS n FROM issues"
        " JOIN projects ON projects.id = issues.project_id"
        " WHERE NOT EXISTS ("
        "  SELECT 1 FROM team_members"
        "  WHERE team_members.team_id = projects.team_id"
        "    AND team_members.user_id = issues.creator_id)",
    ),
    (
        "an issue assigned to someone who is not on its team",
        "SELECT count(*) AS n FROM issues"
        " JOIN projects ON projects.id = issues.project_id"
        " WHERE issues.assignee_id IS NOT NULL AND NOT EXISTS ("
        "  SELECT 1 FROM team_members"
        "  WHERE team_members.team_id = projects.team_id"
        "    AND team_members.user_id = issues.assignee_id)",
    ),
    (
        "a comment by someone who is not on the issue's team",
        "SELECT count(*) AS n FROM comments"
        " JOIN issues ON issues.id = comments.issue_id"
        " JOIN projects ON projects.id = issues.project_id"
        " WHERE NOT EXISTS ("
        "  SELECT 1 FROM team_members"
        "  WHERE team_members.team_id = projects.team_id"
        "    AND team_members.user_id = comments.author_id)",
    ),
    (
        "an event acted by someone who is not on the issue's team",
        "SELECT count(*) AS n FROM issue_events"
        " JOIN issues ON issues.id = issue_events.issue_id"
        " JOIN projects ON projects.id = issues.project_id"
        " WHERE NOT EXISTS ("
        "  SELECT 1 FROM team_members"
        "  WHERE team_members.team_id = projects.team_id"
        "    AND team_members.user_id = issue_events.actor_id)",
    ),
    (
        "a label on an issue of another team",
        "SELECT count(*) AS n FROM issue_labels"
        " JOIN issues ON issues.id = issue_labels.issue_id"
        " JOIN projects ON projects.id = issues.project_id"
        " JOIN labels ON labels.id = issue_labels.label_id"
        " WHERE labels.team_id <> projects.team_id",
    ),
)


def _assert_nothing_predates_its_parent(inst: seahaven.Instance) -> None:
    """Every row is younger than the rows it refers to, and wears its own team's labels."""
    db = inst.inspect()
    for what, query in _PARENTS:
        found = db.one(query)
        assert found is not None
        assert found["n"] == 0, f"{found['n']} rows with {what}"


def _insert(ctx: seahaven.Ctx, table: str, records: Sequence[dict[str, Any]]) -> None:
    """Bulk-insert records whose keys are the column names.

    The statement is built from the first record's keys rather than written out
    beside the dicts, so a column added to a record cannot land in the wrong
    column of the table -- which is exactly what `tuple(record.values())` against
    a hand-written column list does, silently, the first time someone reorders a
    literal.
    """
    if not records:
        return
    columns = list(records[0])
    placeholders = ", ".join("?" * len(columns))
    ctx.db.executemany(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
        [tuple(record[column] for column in columns) for record in records],
    )


def _stamp(ctx: seahaven.Ctx, days_before: float) -> str:
    """A canonical timestamp that many days before the instance's `now`.

    `ctx.clock`, never the wall clock: the arithmetic is on the instance's frozen
    instant, and `Clock` is asked to render the result so the format is the one
    every other door of this world writes.
    """
    return _shift(ctx.clock, days_before)


def _shift(clock: seahaven.Clock, days: float) -> str:
    return seahaven.Clock(clock.now() - timedelta(days=days)).iso()


def _due_at(ctx: seahaven.Ctx, created: float) -> str | None:
    """A due date on roughly a third of issues, somewhere between filing and the horizon.

    Some of them are in the past, which is the point: "what is overdue" is one of
    the first questions a tracker is asked, and a fixture whose every due date is
    in the future has no answer to it.
    """
    if ctx.ids.random.random() >= 0.33:
        return None
    return _shift(ctx.clock, ctx.ids.random.uniform(-float(DUE_HORIZON_DAYS), created))


def _moments(ctx: seahaven.Ctx, created: float, count: int) -> list[float]:
    """`count` instants from the issue's creation forward to `now`, in order.

    Returned as days-before-`now`, so the list is descending: the first is the
    creation itself and the last is the most recent thing that happened, which is
    what the issue's `updated_at` is.
    """
    later = sorted((ctx.ids.random.uniform(0.0, created) for _ in range(count - 1)), reverse=True)
    return [created, *later]


def _title(ctx: seahaven.Ctx) -> str:
    if ctx.ids.random.random() < 0.5:
        return f"{_pick(ctx, _SUBJECTS).capitalize()} {_pick(ctx, _PROBLEMS)}"
    return f"{_pick(ctx, _ACTIONS)} {_pick(ctx, _SUBJECTS)}"


def _description(ctx: seahaven.Ctx) -> str:
    return (
        f"Seen on the latest build: {_pick(ctx, _SUBJECTS)} {_pick(ctx, _PROBLEMS)}. "
        f"{_pick(ctx, _COMMENTS)}"
    )


def _label_name(index: int) -> str:
    """A label's name, unique within its team however many a team has."""
    base = _LABEL_NAMES[index % len(_LABEL_NAMES)]
    round_ = index // len(_LABEL_NAMES)
    return base if round_ == 0 else f"{base}-{round_ + 1}"


def _pick(ctx: seahaven.Ctx, options: Sequence[Any]) -> Any:
    return options[ctx.ids.random.randrange(len(options))]


def _maybe(ctx: seahaven.Ctx, options: Sequence[Any], chance: float) -> Any:
    return _pick(ctx, options)["id"] if ctx.ids.random.random() < chance else None


def _sample(ctx: seahaven.Ctx, options: Sequence[Any], count: int) -> list[Any]:
    if not options or count <= 0:
        return []
    return ctx.ids.random.sample(list(options), min(count, len(options)))


def _weighted(ctx: seahaven.Ctx, weights: Sequence[tuple[str, int]]) -> str:
    total = sum(weight for _value, weight in weights)
    drawn = ctx.ids.random.randrange(total)
    for value, weight in weights:
        if drawn < weight:
            return value
        drawn -= weight
    return weights[-1][0]


if __name__ == "__main__":
    # Run as a script from a checkout, where the world may not be installed.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    raise SystemExit(main(sys.argv[1:]))
