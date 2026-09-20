import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { NewEnvDialog, type NewEnvRequest } from "./components/NewEnvDialog"
import {
  EnvInfoPanel,
  NoEnvSelected,
  StatePanel,
  ToolsPanel,
  TranscriptPanel,
} from "./components/panels"
import { Badge, Button, Dot, cx } from "./components/ui"
import { PRODUCT_NAME, PRODUCT_TAG } from "./brand"
import { calls as callStore, envs as envStore, newId, settings, type CallRecord, type EnvRecord } from "./lib/db"
import {
  ConnectionClosed,
  EnvConnection,
  ProtocolError,
  fetchMetadata,
  fetchSchema,
  type ObservationEnvelope,
} from "./lib/openenv"
import type { EnvActions, LiveEnv } from "./lib/types"

type Tab = "tools" | "state" | "transcript" | "env"

const TABS: { id: Tab; label: string }[] = [
  { id: "tools", label: "Tools" },
  { id: "state", label: "State" },
  { id: "transcript", label: "Transcript" },
  { id: "env", label: "Environment" },
]

const STATUS_TONE = {
  live: "ok",
  connecting: "warn",
  closed: "neutral",
  expired: "warn",
  failed: "danger",
} as const

export default function App() {
  const connections = useRef(new Map<string, EnvConnection>())
  const [envs, setEnvs] = useState<LiveEnv[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [tab, setTab] = useState<Tab>("tools")
  const [dialog, setDialog] = useState<{ args?: Record<string, unknown> } | null>(null)
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)
  const [defaultRoot, setDefaultRoot] = useState(window.location.origin)
  const [follow, setFollow] = useState(true)

  const active = envs.find((env) => env.record.id === activeId) ?? null
  const liveCount = envs.filter((env) => env.record.status === "live").length

  // --- persistence ---------------------------------------------------------

  // Nothing below writes to IndexedDB from inside a `setEnvs` updater. React
  // requires an updater to be pure and calls it twice under StrictMode, which
  // turned one tool call into two rows in the transcript. The updaters compute
  // state; the effect further down saves whatever the render settled on.
  const patch = useCallback((id: string, change: Partial<LiveEnv>) => {
    setEnvs((current) =>
      current.map((env) => (env.record.id === id ? { ...env, ...change } : env)),
    )
  }, [])

  const patchRecord = useCallback((id: string, change: Partial<EnvRecord>) => {
    setEnvs((current) =>
      current.map((env) =>
        env.record.id === id ? { ...env, record: { ...env.record, ...change } } : env,
      ),
    )
  }, [])

  // What has already been written, by identity for a record and by key for a
  // call. Records are replaced rather than mutated, so a reference that has not
  // changed is a row that does not need writing again.
  const savedRecords = useRef(new Map<string, EnvRecord>())
  const savedCalls = useRef(new Set<string>())

  useEffect(() => {
    for (const env of envs) {
      if (savedRecords.current.get(env.record.id) !== env.record) {
        savedRecords.current.set(env.record.id, env.record)
        void envStore.put(env.record)
      }
      for (const call of env.calls) {
        if (savedCalls.current.has(call.id)) continue
        savedCalls.current.add(call.id)
        void callStore.put(call)
      }
    }
  }, [envs])

  useEffect(() => {
    void (async () => {
      const lastRoot = await settings.get<string>("root")
      if (lastRoot) setDefaultRoot(lastRoot)
      const savedFollow = await settings.get<boolean>("follow")
      if (typeof savedFollow === "boolean") setFollow(savedFollow)

      const records = await envStore.all()
      const restored: LiveEnv[] = []
      for (const saved of records.sort((left, right) => right.createdAt - left.createdAt)) {
        // A socket does not survive a reload, so an environment that was live
        // when the tab closed is gone. Say so rather than showing a dead row as
        // live: this is the cost of holding the connections in the browser.
        const record: EnvRecord =
          saved.status === "live" || saved.status === "connecting"
            ? {
                ...saved,
                status: "expired",
                closedAt: saved.closedAt ?? Date.now(),
                note: "the page was reloaded, which drops the socket and destroys the instance",
              }
            : saved
        const restoredCalls = await callStore.forEnv(record.id)
        // A record this loop rewrote is left unmarked, so the effect above
        // saves it; one it did not touch is already what is on disk.
        if (record === saved) savedRecords.current.set(record.id, record)
        for (const call of restoredCalls) savedCalls.current.add(call.id)
        restored.push({
          record,
          tools: null,
          toolsNote: null,
          schema: null,
          schemaNote: null,
          metadata: null,
          calls: restoredCalls,
          state: null,
          busy: false,
        })
      }
      setEnvs(restored)
    })()
  }, [])

  // The socket dies with the tab, so say so before the tab goes.
  useEffect(() => {
    if (liveCount === 0) return
    const onUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ""
    }
    window.addEventListener("beforeunload", onUnload)
    return () => window.removeEventListener("beforeunload", onUnload)
  }, [liveCount])

  // --- recording -----------------------------------------------------------

  const record = useCallback(
    async (envId: string, entry: Omit<CallRecord, "id" | "envId" | "seq">) => {
      setEnvs((current) =>
        current.map((env) => {
          if (env.record.id !== envId) return env
          const seq = env.calls.length + 1
          const call: CallRecord = { ...entry, id: `${envId}:${seq}`, envId, seq }
          return { ...env, calls: [...env.calls, call] }
        }),
      )
    },
    [],
  )

  const markGone = useCallback(
    (envId: string, closed: ConnectionClosed) => {
      connections.current.delete(envId)
      patchRecord(envId, {
        status: closed.wsCode === 1000 ? "closed" : "expired",
        closedAt: Date.now(),
        note:
          closed.wsCode === 1000
            ? null
            : `the environment closed the connection: ${closed.reason || `code ${closed.wsCode}`}`,
      })
    },
    [patchRecord],
  )

  // --- opening -------------------------------------------------------------

  const openEnv = useCallback(
    async (request: NewEnvRequest): Promise<string | null> => {
      setCreating(true)
      setCreateError(null)

      const id = newId()
      const draft: EnvRecord = {
        id,
        root: request.root,
        label: request.label || `run ${new Date().toLocaleTimeString()}`,
        envName: null,
        resetArgs: request.resetArgs,
        createdAt: Date.now(),
        closedAt: null,
        status: "connecting",
        note: null,
        finalState: null,
      }

      const connection = new EnvConnection(request.root)
      connection.onClose = (closed) => markGone(id, closed)

      try {
        await connection.connect()
        const started = performance.now()
        const observation: ObservationEnvelope = await connection.reset(request.resetArgs)

        connections.current.set(id, connection)
        const live: LiveEnv = {
          record: { ...draft, status: "live" },
          tools: null,
          toolsNote: null,
          schema: null,
          schemaNote: null,
          metadata: null,
          calls: [],
          state: null,
          busy: false,
        }
        setEnvs((current) => [live, ...current])
        setActiveId(id)
        setTab("tools")
        void settings.set("root", request.root)
        setDefaultRoot(request.root)

        await record(id, {
          ts: Date.now(),
          kind: "reset",
          name: "reset",
          args: request.resetArgs,
          ok: true,
          result: observation,
          error: null,
          durationMs: Math.round(performance.now() - started),
        })

        // The tool list belongs to the environment rather than the episode, but
        // asking for it is how we learn whether this environment has one at all.
        try {
          const tools = await connection.listTools()
          patch(id, { tools, toolsNote: null })
        } catch (error) {
          patch(id, {
            tools: null,
            toolsNote:
              error instanceof ProtocolError
                ? `This environment rejected a list_tools action (${error.code ?? "error"}), so its actions are not tools.`
                : String(error),
          })
        }

        void (async () => {
          const [schema, metadata] = await Promise.all([
            fetchSchema(request.root),
            fetchMetadata(request.root),
          ])
          patch(id, {
            schema,
            metadata,
            schemaNote: schema ? null : "The schema could not be read from this page's origin.",
          })
          if (metadata?.name) patchRecord(id, { envName: metadata.name })
        })()

        setDialog(null)
        return id
      } catch (error) {
        await connection.close()
        setCreateError(error instanceof Error ? error.message : String(error))
        return null
      } finally {
        setCreating(false)
      }
    },
    [markGone, patch, patchRecord, record],
  )

  // --- calling -------------------------------------------------------------

  const runOnEnv = useCallback(
    async (
      envId: string,
      kind: "tool" | "step",
      name: string,
      args: Record<string, unknown>,
      body: (connection: EnvConnection) => Promise<ObservationEnvelope>,
    ) => {
      const connection = connections.current.get(envId)
      if (!connection) return
      patch(envId, { busy: true })
      const started = performance.now()
      try {
        const envelope = await body(connection)
        const observation = (envelope.observation ?? {}) as Record<string, unknown>
        // A tool error travels on the observation, as data: the call reached the
        // tool and the tool refused. That is not the same as a protocol error
        // and it is not shown the same way.
        const toolError = observation.error as CallRecord["error"] | null | undefined
        await record(envId, {
          ts: Date.now(),
          kind,
          name,
          args,
          ok: !toolError,
          result: kind === "tool" && "result" in observation ? observation.result : envelope,
          error: toolError ?? null,
          durationMs: Math.round(performance.now() - started),
        })
      } catch (error) {
        if (error instanceof ConnectionClosed) markGone(envId, error)
        await record(envId, {
          ts: Date.now(),
          kind,
          name,
          args,
          ok: false,
          result: null,
          error:
            error instanceof ProtocolError
              ? { code: error.code, message: error.message, details: error.details }
              : { message: error instanceof Error ? error.message : String(error) },
          durationMs: Math.round(performance.now() - started),
        })
      } finally {
        patch(envId, { busy: false })
      }
    },
    [markGone, patch, record],
  )

  const refreshState = useCallback(
    async (envId: string) => {
      const connection = connections.current.get(envId)
      if (!connection) return
      try {
        const state = await connection.state()
        patch(envId, { state })
      } catch (error) {
        if (error instanceof ConnectionClosed) markGone(envId, error)
      }
    },
    [markGone, patch],
  )

  const actionsFor = useCallback(
    (envId: string): EnvActions => ({
      callTool: async (name, args) => {
        await runOnEnv(envId, "tool", name, args, (connection) => connection.callTool(name, args))
        if (follow) await refreshState(envId)
      },
      step: async (action) => {
        const name = typeof action.type === "string" ? action.type : "step"
        await runOnEnv(envId, "step", name, action, (connection) => connection.step(action))
        if (follow) await refreshState(envId)
      },
      refreshState: () => refreshState(envId),
      close: async (id) => {
        const connection = connections.current.get(id)
        if (!connection) {
          patchRecord(id, { status: "closed", closedAt: Date.now() })
          return
        }
        // Read the state before closing: the instance is destroyed with the
        // socket, and this is the last moment its final state exists.
        let finalState: Record<string, unknown> | null = null
        try {
          finalState = await connection.state()
        } catch {
          // An environment that was never reset has no state to save.
        }
        await connection.close()
        connections.current.delete(id)
        patchRecord(id, { status: "closed", closedAt: Date.now(), finalState, note: null })
      },
      forget: async (id) => {
        connections.current.get(id)?.close()
        connections.current.delete(id)
        await callStore.removeForEnv(id)
        await envStore.remove(id)
        setEnvs((current) => current.filter((env) => env.record.id !== id))
        setActiveId((current) => (current === id ? null : current))
      },
      replay: async (id) => {
        const source = envs.find((env) => env.record.id === id)
        if (!source) return
        const replayed = await openEnv({
          root: source.record.root,
          label: `${source.record.label} (replay)`,
          resetArgs: source.record.resetArgs,
        })
        if (!replayed) return
        const connection = connections.current.get(replayed)
        if (!connection) return
        for (const call of source.calls) {
          if (call.kind === "tool") {
            await runOnEnv(replayed, "tool", call.name, call.args, (live) =>
              live.callTool(call.name, call.args),
            )
          } else if (call.kind === "step") {
            await runOnEnv(replayed, "step", call.name, call.args, (live) => live.step(call.args))
          }
        }
        await refreshState(replayed)
      },
    }),
    [envs, follow, openEnv, patchRecord, refreshState, runOnEnv],
  )

  const actions = useMemo(
    () => (activeId ? actionsFor(activeId) : null),
    [activeId, actionsFor],
  )

  // --- render --------------------------------------------------------------

  return (
    <div className="grid h-full grid-cols-[288px_minmax(0,1fr)]">
      <aside className="flex min-h-0 flex-col border-r border-border bg-surface">
        <header className="flex items-center gap-2 px-4 py-3.5">
          <svg viewBox="0 0 16 16" className="size-4 text-accent" aria-hidden>
            <rect x="1" y="1" width="6" height="6" rx="1.5" fill="currentColor" opacity="0.9" />
            <rect x="9" y="1" width="6" height="6" rx="1.5" fill="currentColor" opacity="0.5" />
            <rect x="1" y="9" width="6" height="6" rx="1.5" fill="currentColor" opacity="0.5" />
            <rect x="9" y="9" width="6" height="6" rx="1.5" fill="currentColor" opacity="0.25" />
          </svg>
          <h1 className="truncate text-[13px] font-semibold tracking-tight text-fg">
            {PRODUCT_NAME}
          </h1>
          <Badge tone="accent">{PRODUCT_TAG}</Badge>
        </header>

        <div className="px-3 pb-3">
          <Button variant="primary" className="w-full" onClick={() => setDialog({})}>
            New environment
          </Button>
        </div>

        <div className="flex items-baseline gap-2 px-4 pb-1.5">
          <span className="text-[11px] font-medium tracking-wide text-faint uppercase">
            Environments
          </span>
          <span className="ml-auto text-[11.5px] text-faint">
            {liveCount} of {envs.length} live
          </span>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
          {envs.length === 0 ? (
            <p className="px-2 py-6 text-center text-[12.5px] leading-relaxed text-faint">
              No environments yet.
            </p>
          ) : (
            <ul className="flex flex-col gap-1">
              {envs.map((env) => {
                const selected = env.record.id === activeId
                const steps = env.calls.filter((call) => call.kind !== "reset" && call.kind !== "state").length
                return (
                  <li key={env.record.id}>
                    <div
                      className={cx(
                        "group flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left transition-colors",
                        selected ? "bg-raised" : "hover:bg-raised/60",
                      )}
                    >
                      <button
                        type="button"
                        onClick={() => setActiveId(env.record.id)}
                        className="flex min-w-0 flex-1 items-center gap-2"
                      >
                        <Dot tone={STATUS_TONE[env.record.status]} />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-[13px] font-medium text-fg">
                            {env.record.label}
                          </span>
                          <span className="block truncate text-[11.5px] text-faint">
                            {env.record.envName ?? new URL(env.record.root).host} · {steps} call
                            {steps === 1 ? "" : "s"}
                          </span>
                        </span>
                      </button>
                      <button
                        type="button"
                        aria-label={env.record.status === "live" ? "Close environment" : "Forget environment"}
                        title={env.record.status === "live" ? "Close environment" : "Forget environment"}
                        onClick={() =>
                          env.record.status === "live"
                            ? actionsFor(env.record.id).close(env.record.id)
                            : actionsFor(env.record.id).forget(env.record.id)
                        }
                        className="shrink-0 rounded px-1 text-faint opacity-0 transition-opacity hover:text-fg group-hover:opacity-100"
                      >
                        ✕
                      </button>
                    </div>
                  </li>
                )
              })}
            </ul>
          )}
        </div>

        {liveCount > 0 ? (
          <footer className="border-t border-border px-4 py-3">
            <p className="text-[11.5px] leading-snug text-faint">
              {liveCount} socket{liveCount === 1 ? "" : "s"} open. Closing this tab destroys{" "}
              {liveCount === 1 ? "that instance" : "those instances"} on the server.
            </p>
          </footer>
        ) : null}
      </aside>

      <main className="grid min-h-0 grid-rows-[auto_minmax(0,1fr)] bg-bg">
        {active ? (
          <>
            <header className="flex items-center gap-3 border-b border-border px-5 py-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <h2 className="truncate text-[14px] font-semibold text-fg">{active.record.label}</h2>
                  <Badge tone={STATUS_TONE[active.record.status]}>{active.record.status}</Badge>
                  {active.tools ? <Badge>{active.tools.length} tools</Badge> : null}
                </div>
                <p className="truncate font-mono text-[11.5px] text-faint">{active.record.root}</p>
              </div>

              <nav className="ml-auto flex gap-0.5 rounded-lg border border-border bg-surface p-0.5">
                {TABS.map((entry) => (
                  <button
                    key={entry.id}
                    type="button"
                    onClick={() => setTab(entry.id)}
                    className={cx(
                      "rounded-md px-2.5 py-1 text-[12.5px] font-medium transition-colors",
                      tab === entry.id ? "bg-raised text-fg" : "text-muted hover:text-fg",
                    )}
                  >
                    {entry.label}
                  </button>
                ))}
              </nav>
            </header>

            {active.record.note ? (
              <div className="border-b border-warn/30 bg-warn/5 px-5 py-2 text-[12.5px] text-fg">
                {active.record.note}
              </div>
            ) : null}

            <div className="min-h-0">
              {actions && tab === "tools" ? <ToolsPanel env={active} actions={actions} /> : null}
              {actions && tab === "state" ? (
                <StatePanel
                  env={active}
                  actions={actions}
                  follow={follow}
                  onFollowChange={(next) => {
                    setFollow(next)
                    void settings.set("follow", next)
                  }}
                />
              ) : null}
              {actions && tab === "transcript" ? (
                <TranscriptPanel env={active} actions={actions} />
              ) : null}
              {tab === "env" ? <EnvInfoPanel env={active} /> : null}
            </div>
          </>
        ) : (
          <div className="row-span-2">
            <NoEnvSelected onNew={() => setDialog({})} />
          </div>
        )}
      </main>

      {dialog ? (
        <NewEnvDialog
          defaultRoot={defaultRoot}
          defaultArgs={dialog.args}
          busy={creating}
          error={createError}
          onCancel={() => {
            setDialog(null)
            setCreateError(null)
          }}
          onCreate={(request) => void openEnv(request)}
        />
      ) : null}
    </div>
  )
}
