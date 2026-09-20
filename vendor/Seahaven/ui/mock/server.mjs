/**
 * A fake OpenEnv environment, for developing the UI without a Python toolchain.
 *
 * It copies the parts of the real server the UI depends on, including the
 * awkward ones:
 *
 *   - `/ws` frames are `reset`, `step`, `state`, `close` and `mcp`, answered one
 *     at a time, in order, with no request ids.
 *   - a `step` on `{"type": "list_tools"}` answers the tool list; an
 *     environment that is not tool-shaped rejects it (see `--plain`).
 *   - `reset` silently drops kwargs the environment does not accept.
 *   - `GET /schema` and `GET /metadata` answer, and **no CORS headers are set**,
 *     because the real server sets none either.
 *   - `/reset`, `/step` and `/state` over HTTP answer 501, as Seahaven's do.
 *   - the built console is served at `/console`, on this same origin.
 *
 * Usage: node mock/server.mjs [--port 8000] [--reap 30] [--plain]
 */

import { createServer } from "node:http"
import { readFileSync, existsSync } from "node:fs"
import { fileURLToPath } from "node:url"
import { dirname, join } from "node:path"
import { WebSocketServer } from "ws"

const here = dirname(fileURLToPath(import.meta.url))
const argv = process.argv.slice(2)
const flag = (name, fallback) => {
  const at = argv.indexOf(`--${name}`)
  return at === -1 ? fallback : argv[at + 1]
}
const PORT = Number(flag("port", 8000))
const REAP_SECONDS = Number(flag("reap", 0))
const PLAIN = argv.includes("--plain")

// --- the world -------------------------------------------------------------

const FIXTURES = ["small_startup", "agency", "big_co"]

const TOOLS = [
  {
    name: "search_issues",
    description:
      "Full-text search over issue titles and bodies. Returns the newest matches first.",
    input_schema: {
      type: "object",
      title: "search_issues",
      properties: {
        query: {
          type: "string",
          title: "Query",
          description: "The text to search for. Supports quoted phrases.",
        },
        status: {
          type: "string",
          title: "Status",
          description: "Only return issues in this status. Omit for every status.",
          enum: ["todo", "in_progress", "blocked", "done"],
        },
        // The shape pydantic emits for `limit: int | None = 20`: the branch
        // carries the type and the wrapper carries every hint.
        limit: {
          anyOf: [{ type: "integer", minimum: 1, maximum: 100 }, { type: "null" }],
          title: "Limit",
          description: "How many issues to return.",
          default: 20,
        },
        include_closed: {
          type: "boolean",
          title: "Include closed",
          description: "Include issues that were closed more than 30 days ago.",
          default: false,
        },
      },
      required: ["query"],
    },
  },
  {
    name: "get_issue",
    description: "Read one issue by its key, with its comments and current assignee.",
    input_schema: {
      type: "object",
      title: "get_issue",
      properties: {
        key: {
          type: "string",
          title: "Key",
          description: 'The human-readable issue key, such as "ENG-12".',
          pattern: "^[A-Z]+-[0-9]+$",
        },
      },
      required: ["key"],
    },
  },
  {
    name: "create_issue",
    description: "File a new issue in a project. Returns the issue, including its new key.",
    input_schema: {
      type: "object",
      title: "create_issue",
      properties: {
        project: {
          type: "string",
          title: "Project",
          description: "The project to file the issue in.",
          enum: ["ENG", "DESIGN", "OPS"],
          default: "ENG",
        },
        title: {
          type: "string",
          title: "Title",
          description: "One line describing the problem.",
          maxLength: 120,
        },
        body: {
          type: "string",
          title: "Body",
          description: "The full description, in Markdown.",
          maxLength: 4000,
        },
        labels: {
          type: "array",
          title: "Labels",
          description: "Labels to attach. Unknown labels are created.",
          items: { type: "string" },
          default: [],
        },
        assignee: {
          anyOf: [{ type: "string" }, { type: "null" }],
          title: "Assignee",
          description: "The email of the person to assign. Leave empty for unassigned.",
          default: null,
        },
      },
      required: ["title"],
    },
  },
  {
    name: "transition_issue",
    description: "Move an issue to a new status. Refuses a transition the workflow does not allow.",
    input_schema: {
      type: "object",
      title: "transition_issue",
      properties: {
        issue_id: { type: "string", title: "Issue id", description: "The issue's opaque id." },
        status: {
          type: "string",
          title: "Status",
          description: "The status to move to.",
          enum: ["todo", "in_progress", "blocked", "done"],
        },
        comment: {
          type: "string",
          title: "Comment",
          description: "An optional comment to leave with the transition.",
          maxLength: 2000,
        },
      },
      required: ["issue_id", "status"],
    },
  },
  {
    name: "execute",
    description:
      "Run one read-write SQL statement against the instance. Sandboxed to this instance's database.",
    input_schema: {
      type: "object",
      title: "execute",
      properties: {
        sql: {
          type: "string",
          title: "Sql",
          description: "One SQL statement. Multiple statements are refused.",
          maxLength: 8000,
        },
      },
      required: ["sql"],
    },
  },
]

const PLAIN_ACTION_SCHEMA = {
  title: "MoveAction",
  type: "object",
  properties: {
    type: { const: "move", default: "move", title: "Type", type: "string" },
    direction: {
      type: "string",
      title: "Direction",
      description: "Which way to move the agent on the grid.",
      enum: ["north", "south", "east", "west"],
    },
    distance: {
      type: "integer",
      title: "Distance",
      description: "How many cells to move.",
      default: 1,
      minimum: 1,
      maximum: 8,
    },
  },
  required: ["direction"],
}

const SCHEMA = {
  action: PLAIN
    ? PLAIN_ACTION_SCHEMA
    : {
        title: "CallToolAction",
        type: "object",
        properties: {
          type: { const: "call_tool", default: "call_tool", title: "Type", type: "string" },
          tool_name: { type: "string", title: "Tool Name", description: "Name of the tool to call" },
          arguments: {
            type: "object",
            title: "Arguments",
            description: "Arguments to pass to the tool",
            additionalProperties: true,
          },
        },
        required: ["tool_name"],
      },
  observation: {
    title: "SeahavenObservation",
    type: "object",
    properties: {
      tool_name: { type: "string", description: "The tool this observation answers." },
      result: { description: "What the tool returned. Null when the call failed." },
      error: { description: "The error the call failed with, or null." },
    },
  },
  state: {
    title: "SeahavenState",
    type: "object",
    properties: {
      episode_id: { type: "string", description: "The id of the episode this instance is running." },
      step_count: { type: "integer", description: "How many tool calls this episode has made." },
      now: { type: "string", description: "The instance's frozen clock, as an ISO 8601 instant." },
      fixture: { type: "string", description: "The fixture this instance was copied from." },
      changes: {
        type: "object",
        description: "Every row the agent changed, by table, since the instance was created.",
      },
    },
  },
}

const METADATA = {
  name: PLAIN ? "gridworld" : "projecttracker",
  description: PLAIN
    ? "A small grid the agent walks around."
    : "Issues, sprints and comments, and the tools that touch them.",
  version: "1.0.0",
}

// --- sessions --------------------------------------------------------------

let counter = 0

function newSession() {
  return {
    instance: null,
    lastSeen: Date.now(),
  }
}

function resetInstance(session, kwargs) {
  // The real server filters kwargs against the signature of `reset()` and drops
  // the rest without a word. Copying that is the point: the UI has to cope.
  const fixture = FIXTURES.includes(kwargs.fixture) ? kwargs.fixture : null
  session.instance = {
    episodeId: kwargs.episode_id ?? `ep-${++counter}`,
    fixture,
    seed: typeof kwargs.seed === "number" ? kwargs.seed : null,
    now: typeof kwargs.now === "string" ? kwargs.now : new Date().toISOString(),
    steps: 0,
    changes: {},
    issues: fixture === "big_co" ? 812 : fixture === "agency" ? 96 : fixture ? 14 : 0,
  }
  return {
    observation: {
      metadata: {
        fixture,
        now: session.instance.now,
        tools: TOOLS.length,
      },
    },
    reward: null,
    done: false,
    metadata: { fixture, now: session.instance.now, tools: TOOLS.length },
  }
}

function callTool(instance, name, args) {
  const tool = TOOLS.find((entry) => entry.name === name)
  if (!tool) {
    return {
      tool_name: name,
      result: null,
      error: {
        code: "TOOL_NOT_FOUND",
        message: `no tool named ${name}`,
        details: { known: TOOLS.map((entry) => entry.name) },
      },
    }
  }
  instance.steps += 1

  switch (name) {
    case "search_issues": {
      const limit = args.limit ?? 20
      const rows = Array.from({ length: Math.min(limit, 3) }, (_unused, index) => ({
        key: `ENG-${12 + index}`,
        title: `${args.query} in the billing worker`,
        status: args.status ?? "todo",
        updated_at: instance.now,
      }))
      return { tool_name: name, result: rows, error: null }
    }
    case "get_issue": {
      if (!/^[A-Z]+-[0-9]+$/.test(String(args.key ?? ""))) {
        return {
          tool_name: name,
          result: null,
          error: {
            code: "NOT_FOUND",
            message: `issue ${args.key} not found`,
            details: { kind: "issue", key: args.key ?? null },
          },
        }
      }
      return {
        tool_name: name,
        result: {
          id: "i_8f21",
          key: args.key,
          title: "The session cookie leaks a stack trace",
          status: "in_progress",
          assignee: "dana@example.com",
          comments: [{ author: "sam@example.com", body: "Reproduced on staging." }],
        },
        error: null,
      }
    }
    case "create_issue": {
      const key = `${args.project ?? "ENG"}-${400 + instance.steps}`
      instance.issues += 1
      instance.changes.issues = [
        ...(instance.changes.issues ?? []),
        { op: "insert", key, title: args.title, labels: args.labels ?? [] },
      ]
      return {
        tool_name: name,
        result: { id: `i_${key.toLowerCase()}`, key, title: args.title, status: "todo" },
        error: null,
      }
    }
    case "transition_issue": {
      if (args.status === "done" && !args.comment) {
        return {
          tool_name: name,
          result: null,
          error: {
            code: "WORKFLOW_REFUSED",
            message: "closing an issue needs a comment",
            details: { issue_id: args.issue_id, required: "comment" },
          },
        }
      }
      instance.changes.issues = [
        ...(instance.changes.issues ?? []),
        { op: "update", id: args.issue_id, status: args.status },
      ]
      return { tool_name: name, result: { id: args.issue_id, status: args.status }, error: null }
    }
    case "execute": {
      const sql = String(args.sql ?? "")
      if (/;\s*\S/.test(sql)) {
        return {
          tool_name: name,
          result: null,
          error: {
            code: "SQL_REFUSED",
            message: "more than one statement in one call",
            details: { statements: sql.split(";").filter((part) => part.trim()).length },
          },
        }
      }
      instance.changes.sql = [...(instance.changes.sql ?? []), sql]
      return { tool_name: name, result: { rowcount: 1 }, error: null }
    }
    default:
      return { tool_name: name, result: null, error: null }
  }
}

function stateOf(session) {
  const instance = session.instance
  return {
    episode_id: instance.episodeId,
    step_count: instance.steps,
    now: instance.now,
    fixture: instance.fixture,
    changes: instance.changes,
  }
}

// --- HTTP ------------------------------------------------------------------

const REFUSAL = {
  detail: {
    code: "http_episode_control_unsupported",
    message: "this route cannot hold an episode, so it is refused. Use /ws.",
    details: { use_instead: "/ws" },
  },
}

function servePage(response) {
  const built = join(here, "..", "dist", "index.html")
  if (!existsSync(built)) {
    response.writeHead(404, { "content-type": "text/plain" })
    response.end("no dist/index.html yet -- run `npm run build`\n")
    return
  }
  response.writeHead(200, { "content-type": "text/html; charset=utf-8" })
  response.end(readFileSync(built))
}

const json = (response, status, body) => {
  response.writeHead(status, { "content-type": "application/json" })
  response.end(JSON.stringify(body))
}

const server = createServer((request, response) => {
  const url = new URL(request.url, `http://${request.headers.host}`)
  // No CORS headers anywhere, on purpose: the real server sets none.
  switch (url.pathname) {
    case "/health":
      return json(response, 200, { status: "healthy" })
    case "/schema":
      return json(response, 200, SCHEMA)
    case "/metadata":
      return json(response, 200, METADATA)
    case "/reset":
    case "/step":
    case "/state":
      return json(response, 501, REFUSAL)
    case "/":
    case "/console":
    case "/console/":
      return servePage(response)
    default:
      return json(response, 404, { detail: "not found" })
  }
})

// --- WebSocket -------------------------------------------------------------

const sockets = new WebSocketServer({ server, path: "/ws" })

sockets.on("connection", (socket) => {
  const session = newSession()
  const send = (frame) => socket.send(JSON.stringify(frame))
  const error = (code, message, details) =>
    send({ type: "error", data: { code, message, ...(details ? { details } : {}) } })

  socket.on("message", (raw) => {
    session.lastSeen = Date.now()
    let frame
    try {
      frame = JSON.parse(raw.toString())
    } catch {
      return error("INVALID_JSON", "the frame is not JSON")
    }

    switch (frame.type) {
      case "reset":
        return send({ type: "observation", data: resetInstance(session, frame.data ?? {}) })

      case "step": {
        const action = frame.data ?? {}

        if (action.type === "list_tools") {
          if (PLAIN) {
            return error(
              "VALIDATION_ERROR",
              "1 validation error for MoveAction\ntype\n  Input should be 'move'",
            )
          }
          return send({
            type: "observation",
            data: { observation: { tools: TOOLS }, reward: null, done: false },
          })
        }

        if (!session.instance) {
          return error("EXECUTION_ERROR", "reset first: this session has no instance")
        }

        if (PLAIN) {
          session.instance.steps += 1
          return send({
            type: "observation",
            data: {
              observation: {
                position: [session.instance.steps, 0],
                direction: action.direction ?? "north",
              },
              reward: 0.5,
              done: false,
            },
          })
        }

        if (action.type !== "call_tool") {
          return error("VALIDATION_ERROR", `unsupported action type: ${action.type ?? "(none)"}`)
        }
        const observation = callTool(session.instance, action.tool_name, action.arguments ?? {})
        return send({
          type: "observation",
          data: { observation, reward: null, done: false },
        })
      }

      case "state":
        if (!session.instance) {
          return error("EXECUTION_ERROR", "reset first: this session has no instance")
        }
        return send({ type: "state", data: stateOf(session) })

      case "close":
        return socket.close(1000, "closed by the client")

      case "mcp":
        // Seahaven refuses this whole dialect; the mock does the same.
        return send({
          type: "mcp",
          data: {
            jsonrpc: "2.0",
            id: frame.data?.id ?? null,
            error: { code: -32601, message: "method is not available on this environment" },
          },
        })

      default:
        return error("UNKNOWN_TYPE", `Unknown message type: ${frame.type}`)
    }
  })

  if (REAP_SECONDS > 0) {
    const timer = setInterval(() => {
      if (Date.now() - session.lastSeen > REAP_SECONDS * 1000) {
        clearInterval(timer)
        socket.close(1001, "session reaped after being idle")
      }
    }, 1000)
    socket.on("close", () => clearInterval(timer))
  }
})

server.listen(PORT, "127.0.0.1", () => {
  const kind = PLAIN ? "a plain action environment" : "a tool environment"
  console.log(`mock OpenEnv (${kind}) on http://127.0.0.1:${PORT}`)
  if (REAP_SECONDS > 0) console.log(`idle sessions are reaped after ${REAP_SECONDS}s`)
})
