"""The two tool factories a world with a SQL surface registers.

```python
world.tool(run_sql(tables=["issues", "comments"]))
world.tool(describe_schema(tables=["issues", "comments"]))
```

They are the only helpers in V1. Both build a `Tool` and hand it back; the world
registers it like any other, and overrides `name` and `description` to match the
product it is mimicking.
"""

from seahaven.helpers.describe_schema import describe_schema
from seahaven.helpers.run_sql import run_sql

__all__ = ["describe_schema", "run_sql"]
