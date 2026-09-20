# Seahaven Console

A general-purpose web console for [OpenEnv](https://github.com/huggingface/OpenEnv)
environments. It builds to one self-contained `index.html` that an environment server mounts and
serves itself.

The name is the only Seahaven-shaped thing about it, and it is in one file: `src/brand.ts` holds
`PRODUCT_NAME` and `PRODUCT_TAG`, and `index.html` holds the `<title>`.

Nothing in here is specific to any one environment. It speaks the standard protocol: the
`/ws` socket for episodes, `step` on a `list_tools` action for the tool list, and `GET /schema`
for the action, observation and state models. An environment that answers those gets a full
interface without writing any UI code.

> **Status: prototype.** It drives a real environment, is covered by a browser test, and is served
> by `seahaven serve` at `/console`.

## What it does

- **Many environments at once.** The left pane lists every environment you have opened. Each one
  is its own WebSocket session holding its own private instance on the server. Open more, close
  the ones you are done with.
- **Reset belongs to New environment.** `reset` creates an instance; it is not something you do
  to a running one. It is in the New environment dialog and nowhere else, so an environment in
  the list is always exactly one run.
- **Tools first.** The tool picker is a filterable dropdown built from the environment's own tool
  list. Each argument gets a real control, its description, its default, its allowed values and a
  required marker, all from the tool's `input_schema`. A raw JSON toggle is there when you want it.
- **State is a document, not a dump.** The state panel renders the `state` frame as a tree, with
  the field descriptions from `GET /schema` attached to the keys they describe. It can refresh
  after every call.
- **Every call is kept.** The transcript is saved in IndexedDB and outlives the socket. Copy it as
  Python, or replay it into a new environment.

## Run it

```sh
npm install
npm run build          # writes dist/index.html, one file, no sibling assets

npm run mock           # a fake environment on :8000 that also serves dist/index.html at /console
open http://127.0.0.1:8000/console
```

For development against a real environment on `:8000`:

```sh
npm run dev            # vite proxies /ws, /schema and /metadata to OPENENV_TARGET
```

The browser test drives the built file against the mock in Chromium and screenshots each step:

```sh
npm run mock &
npm run mock:plain &   # a second mock on :8001 whose actions are not tools
npm run build && npm run e2e
```

## Serving it from an environment

Seahaven serves the built page at `/console`, from a copy in its own package
(`src/seahaven/openenv/console/index.html`). Rebuild and replace that copy after a change here:

```sh
npm run build && cp dist/index.html ../src/seahaven/openenv/console/index.html
```

The build is one file with no external requests, so any other OpenEnv app can serve it the same
way:

```py
from fastapi.responses import FileResponse

app = ...  # whatever your environment's create_app returned


@app.get("/console", include_in_schema=False)
def console() -> FileResponse:
    return FileResponse("path/to/index.html")
```

Same-origin matters. The socket works from anywhere, but `GET /schema` and `GET /metadata` are
plain HTTP and **an OpenEnv server registers no CORS middleware**, so a page on another origin
cannot read them. The console treats both as optional and says so in the Environment panel when
they are missing, but serving it from the environment is what makes the schema available.

## What it uses, and what it avoids

| Wants | Uses | Why not the other thing |
|---|---|---|
| create an instance | `reset` frame on `/ws` | `POST /reset` builds a new environment per request and closes it before replying |
| call a tool | `step` frame, `{"type": "call_tool"}` | `POST /step` has the same defect |
| list tools | `step` frame, `{"type": "list_tools"}` | `POST /mcp` is not MCP, has no `reset`, and some servers refuse it outright |
| read state | `state` frame | `GET /state` reads an environment that was never stepped |
| action and state models | `GET /schema` | read-only and safe, when the page's origin is allowed to read it |
| environment name | `GET /metadata` | same |

The protocol carries no request ids: the server reads one frame and answers one frame. The client
therefore issues requests strictly in order and matches answers to a FIFO queue, because two
overlapping calls on one socket would pair the wrong answer with the wrong question.

## Known limits

- **Reset arguments cannot be a generated form.** OpenEnv publishes no schema for what an
  environment's `reset()` accepts, so the New environment dialog offers a JSON box under
  Advanced. Worse, the server filters the payload against the signature of `reset()` and drops
  anything that does not match without an error, so a misspelled argument is silent. A schema for
  reset arguments is the one addition to the standard that would close this.
- **The leave warning cannot say why.** Every browser ignores a custom `beforeunload` message and
  shows its own generic text, so the reason lives in the page instead: the sidebar names how many
  sockets are open and what closing the tab destroys.
- **A socket does not survive a reload.** The connections live in the page. Reopening it shows
  every past environment with its transcript and its saved final state, marked expired. Replay
  reruns a transcript into a fresh instance, which for a deterministic environment reproduces the
  run exactly.
- **Closing an environment saves its state first.** The instance is destroyed with the socket, so
  the UI reads `state` before it closes and keeps that as the final state.

## Layout

```
src/brand.ts          the product name, for renaming in one place
src/lib/openenv.ts    the protocol: frames, ordering, errors, the two HTTP reads
src/lib/schema.ts     JSON Schema to form fields, and form values back to a payload
src/lib/db.ts         IndexedDB: settings, environments, transcripts
src/components/       the primitives, the panels, the New environment dialog
mock/server.mjs       a fake environment, including its awkward behaviours
e2e/smoke.mjs         drives the built file in Chromium
```

Built with Vite, React and Tailwind. The components are hand-written in the shadcn/ui idiom, with
the same tokens, so replacing them with the real ones is a copy rather than a re-theme.
