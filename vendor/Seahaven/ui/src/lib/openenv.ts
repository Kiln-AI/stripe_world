/**
 * The OpenEnv wire protocol, as a browser client.
 *
 * Everything an episode needs travels over one WebSocket at `{root}/ws`. The
 * HTTP routes `POST /reset`, `POST /step` and `GET /state` exist but are not
 * usable: upstream builds a new environment inside each of those handlers and
 * closes it before replying, so no two requests ever share one. Seahaven
 * answers all three with a 501 for that reason. This client never calls them.
 *
 * `GET /schema` and `GET /metadata` are read-only and safe, but they are plain
 * HTTP and the OpenEnv server registers no CORS middleware, so a page on a
 * different origin cannot read them. They are treated as enhancements
 * throughout: every feature works without them.
 */

export type JsonSchema = {
  type?: string | string[]
  title?: string
  description?: string
  default?: unknown
  enum?: unknown[]
  const?: unknown
  properties?: Record<string, JsonSchema>
  required?: string[]
  items?: JsonSchema
  additionalProperties?: boolean | JsonSchema
  minimum?: number
  maximum?: number
  minLength?: number
  maxLength?: number
  pattern?: string
  format?: string
  anyOf?: JsonSchema[]
  oneOf?: JsonSchema[]
  $ref?: string
  $defs?: Record<string, JsonSchema>
  [key: string]: unknown
}

export type Tool = {
  name: string
  description: string
  input_schema: JsonSchema
}

/** What `serialize_observation` puts on the wire for a reset or a step. */
export type ObservationEnvelope = {
  observation: Record<string, unknown>
  reward: number | null
  done: boolean
  metadata?: Record<string, unknown> | null
}

/** The `{code, message, details}` triple OpenEnv and Seahaven both answer with. */
export type WireError = {
  code?: string
  message: string
  details?: unknown
}

export class ProtocolError extends Error {
  code?: string
  details?: unknown
  constructor(error: WireError) {
    super(error.message)
    this.name = "ProtocolError"
    this.code = error.code
    this.details = error.details
  }
}

export class ConnectionClosed extends Error {
  wsCode: number
  reason: string
  constructor(wsCode: number, reason: string) {
    super(reason || `the environment closed the connection (${wsCode})`)
    this.name = "ConnectionClosed"
    this.wsCode = wsCode
    this.reason = reason
  }
}

type ResponseFrame = { type: string; data: Record<string, unknown> }

type Pending = {
  resolve: (frame: ResponseFrame) => void
  reject: (error: Error) => void
  label: string
}

export function wsUrl(root: string): string {
  const url = new URL(normalizeRoot(root))
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:"
  url.pathname = joinPath(url.pathname, "ws")
  return url.toString()
}

export function normalizeRoot(root: string): string {
  const trimmed = root.trim().replace(/\/+$/, "")
  if (!trimmed) return window.location.origin
  if (/^https?:\/\//i.test(trimmed)) return trimmed
  if (/^wss?:\/\//i.test(trimmed)) return trimmed.replace(/^ws/i, "http")
  return `http://${trimmed}`
}

function joinPath(base: string, segment: string): string {
  const left = base.replace(/\/+$/, "")
  return `${left}/${segment}`
}

/**
 * One environment instance: one WebSocket, one session, one private instance on
 * the server.
 *
 * The protocol carries no request ids -- the server reads a frame, answers one
 * frame, and reads the next -- so requests are issued strictly in order and
 * answers are matched to a FIFO queue. Two overlapping calls on one connection
 * would pair the wrong answer with the wrong question.
 */
export class EnvConnection {
  readonly root: string
  private socket: WebSocket | null = null
  private queue: Pending[] = []
  private chain: Promise<unknown> = Promise.resolve()
  private closedBy: "client" | "server" | null = null

  onClose: ((event: ConnectionClosed) => void) | null = null

  constructor(root: string) {
    this.root = normalizeRoot(root)
  }

  get ready(): boolean {
    return this.socket?.readyState === WebSocket.OPEN
  }

  connect(timeoutMs = 10_000): Promise<void> {
    return new Promise((resolve, reject) => {
      let socket: WebSocket
      try {
        socket = new WebSocket(wsUrl(this.root))
      } catch (error) {
        reject(new Error(`cannot open a socket to ${this.root}: ${String(error)}`))
        return
      }
      this.socket = socket

      const timer = window.setTimeout(() => {
        socket.close()
        reject(new Error(`no answer from ${this.root} after ${timeoutMs / 1000}s`))
      }, timeoutMs)

      socket.onopen = () => {
        window.clearTimeout(timer)
        resolve()
      }

      socket.onerror = () => {
        window.clearTimeout(timer)
        // The browser never says why a socket failed. Name the likely causes
        // rather than printing "error".
        reject(
          new Error(
            `could not connect to ${this.root}. Is the environment running, and ` +
              `does the page's scheme allow it? An https:// page cannot open a ws:// socket.`,
          ),
        )
      }

      socket.onmessage = (event) => this.receive(event)

      socket.onclose = (event) => {
        window.clearTimeout(timer)
        const closed = new ConnectionClosed(event.code, event.reason)
        for (const pending of this.queue.splice(0)) pending.reject(closed)
        if (this.closedBy !== "client") {
          this.closedBy = "server"
          this.onClose?.(closed)
        }
      }
    })
  }

  private receive(event: MessageEvent) {
    let frame: ResponseFrame
    try {
      frame = JSON.parse(String(event.data))
    } catch {
      return
    }
    const pending = this.queue.shift()
    if (!pending) return
    if (frame.type === "error") {
      const data = (frame.data ?? {}) as Record<string, unknown>
      pending.reject(
        new ProtocolError({
          message: String(data.message ?? `the environment refused the ${pending.label}`),
          code: typeof data.code === "string" ? data.code : undefined,
          details: data.details,
        }),
      )
      return
    }
    pending.resolve(frame)
  }

  /** Queue one request. Frames go out in call order and answers come back in the same order. */
  private send(frame: { type: string; data?: unknown }, label: string): Promise<ResponseFrame> {
    const run = () =>
      new Promise<ResponseFrame>((resolve, reject) => {
        if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
          reject(new ConnectionClosed(1006, "the connection to this environment is gone"))
          return
        }
        this.queue.push({ resolve, reject, label })
        this.socket.send(JSON.stringify(frame))
      })
    const next = this.chain.then(run, run)
    this.chain = next.catch(() => undefined)
    return next
  }

  /** Create the instance. `kwargs` is passed straight to the environment's `reset()`. */
  async reset(kwargs: Record<string, unknown> = {}): Promise<ObservationEnvelope> {
    const frame = await this.send({ type: "reset", data: kwargs }, "reset")
    return frame.data as unknown as ObservationEnvelope
  }

  /** Step on an arbitrary action payload. */
  async step(action: Record<string, unknown>): Promise<ObservationEnvelope> {
    const frame = await this.send({ type: "step", data: action }, "step")
    return frame.data as unknown as ObservationEnvelope
  }

  /**
   * The tool list, for environments that speak the MCP action dialect.
   *
   * This is a `step` on a `ListToolsAction` and not an `mcp` frame: Seahaven
   * refuses `/mcp` on both transports, and `deserialize_action` intercepts MCP
   * action types on the `step` path regardless of the environment's own action
   * class. An environment whose actions are not tools rejects this frame, which
   * is exactly the signal that the generic form is the right interface for it.
   */
  async listTools(): Promise<Tool[]> {
    const envelope = await this.step({ type: "list_tools" })
    const tools = (envelope.observation?.tools ?? []) as Tool[]
    return Array.isArray(tools) ? tools : []
  }

  async callTool(name: string, args: Record<string, unknown>): Promise<ObservationEnvelope> {
    return this.step({ type: "call_tool", tool_name: name, arguments: args })
  }

  async state(): Promise<Record<string, unknown>> {
    const frame = await this.send({ type: "state" }, "state request")
    return frame.data
  }

  async close(): Promise<void> {
    this.closedBy = "client"
    if (this.socket?.readyState === WebSocket.OPEN) {
      try {
        this.socket.send(JSON.stringify({ type: "close" }))
      } catch {
        // The socket went while we were closing it, which is the outcome anyway.
      }
      this.socket.close(1000, "closed from the UI")
    }
    this.socket = null
  }
}

export type EnvSchema = {
  action?: JsonSchema
  observation?: JsonSchema
  state?: JsonSchema
}

export type EnvMetadata = {
  name?: string
  description?: string
  version?: string
  [key: string]: unknown
}

/**
 * The two read-only HTTP routes, if the browser is allowed to read them.
 *
 * A cross-origin page is not: the OpenEnv server adds no CORS middleware, so
 * the fetch fails with an opaque `TypeError`. Both callers treat `null` as
 * "render without the extra detail", never as an error.
 */
export async function fetchJson<T>(root: string, path: string): Promise<T | null> {
  try {
    const response = await fetch(`${normalizeRoot(root)}/${path}`, {
      headers: { accept: "application/json" },
    })
    if (!response.ok) return null
    return (await response.json()) as T
  } catch {
    return null
  }
}

export const fetchSchema = (root: string) => fetchJson<EnvSchema>(root, "schema")
export const fetchMetadata = (root: string) => fetchJson<EnvMetadata>(root, "metadata")

/** `true` when the action schema is the fixed MCP tool-call triple rather than a real action. */
export function isToolCallAction(action: JsonSchema | undefined): boolean {
  const properties = action?.properties
  if (!properties) return false
  return "tool_name" in properties && "arguments" in properties
}
