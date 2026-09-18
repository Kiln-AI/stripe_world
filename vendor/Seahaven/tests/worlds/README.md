# Worlds the lints and composition are run against

Ten small packages, each a real world in the layout of functional spec §2.1, because the rules
that need a package -- import coverage, the world attribute, a `World` that refuses to be
constructed, one world adding another -- cannot be exercised by an object built in a test. They are
fixtures, not examples to copy: `messy` and `broken_ddl` are wrong on purpose.

| World | What it is for |
|---|---|
| `tidy` | A world with no findings at all: the negative case for every rule |
| `messy` | A wall clock, `random`, `uuid.uuid4()`, an undescribed tool and an orphan module |
| `broken_ddl` | DDL SQLite refuses, so the `World` cannot be constructed (SH104) |
| `no_world` | A package with no `world` attribute (SH501) |
| `payments` | The leaf of the composite: two tools, one table, one bindable startup keyword, a pin of its own and a state format of its own |
| `shop` | Adds `payments` with an empty allow list, and has one tool of its own |
| `emporium` | The composite host: `payments`, a second `payments` account, and `shop` |
| `ledger` | A leaf whose two tools' descriptions name each other, which a prefix makes stale |
| `bazaar` | A host that is wrong in every way the composition rules know how to say |
| `unsealed` | A world whose `tool_allow_list` names a tool nobody contributes, so it never seals |

`payments`, `shop` and `emporium` are one tree, and the tree the composition tests are written
against: `emporium` resolves to the four nodes `main`, `payments`, `payments_eu` and `shop`, with
`shop/payments` an alias of `payments` rather than a fifth store. `tests/conftest.py` puts the `src`
directory of every world in the two trees on `sys.path`, because a host adds another world by
importing it.

`bazaar` and `unsealed` are the second tree, over `ledger`, and both are wrong on purpose:
`bazaar` seals and then earns SH206, SH207, SH208, SH209, SH502 and SH503, while `unsealed` does
not seal at all and is SH504 -- plus one undescribed tool, so that "every rule that needs no
tree still runs" is something a test can hold the seal to. The negative cases live in the first
tree: `emporium` earns none of them, and `shop` declares a complete and correct `Worlds` subclass.

The DDL rules are driven with an inline `World` instead (`test_lint_ddl.py`): they need neither an
import nor a package, and a committed world per malformed table would be a directory of them.
