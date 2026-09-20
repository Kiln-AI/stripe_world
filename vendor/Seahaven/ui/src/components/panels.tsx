/**
 * The four panels of an open environment: its tools, its state, what has been
 * called against it, and what the environment says about itself.
 */

import { useEffect, useMemo, useRef, useState } from "react"
import type { CallRecord } from "../lib/db"
import { isToolCallAction, type JsonSchema, type Tool } from "../lib/openenv"
import { coerce, fieldsOf, initialValue, type Field } from "../lib/schema"
import type { EnvActions, LiveEnv } from "../lib/types"
import {
  Badge,
  Button,
  Combobox,
  CopyButton,
  Disclosure,
  EmptyState,
  FormField,
  Input,
  JsonView,
  Switch,
  Textarea,
  cx,
} from "./ui"

// --- the generated argument form -------------------------------------------

function ArgsForm({
  fields,
  values,
  errors,
  onChange,
}: {
  fields: Field[]
  values: Record<string, string | boolean>
  errors: Record<string, string>
  onChange: (name: string, value: string | boolean) => void
}) {
  if (fields.length === 0) {
    return <p className="text-[13px] text-muted">This call takes no arguments.</p>
  }
  return (
    <div className="flex flex-col gap-4">
      {fields.map((field) => (
        <FormField
          key={field.name}
          label={field.label}
          description={field.description}
          required={field.required}
          typeHint={field.typeHint}
          error={errors[field.name]}
        >
          {(id) => {
            const value = values[field.name]
            if (field.kind === "boolean") {
              return (
                <div className="flex items-center gap-2">
                  <Switch
                    checked={value === true}
                    onChange={(next) => onChange(field.name, next)}
                    label={field.label}
                  />
                  <span className="text-[12.5px] text-muted">
                    {value === true ? "true" : "false"}
                  </span>
                </div>
              )
            }
            if (field.kind === "enum" && field.choices) {
              return (
                <div className="flex flex-wrap gap-1.5">
                  {field.choices.map((choice) => {
                    const text = typeof choice === "string" ? choice : JSON.stringify(choice)
                    const active = value === text
                    return (
                      <button
                        key={text}
                        type="button"
                        onClick={() => onChange(field.name, active ? "" : text)}
                        className={cx(
                          "rounded-md border px-2 py-1 font-mono text-[12.5px] transition-colors",
                          active
                            ? "border-accent bg-accent/10 text-accent"
                            : "border-border text-muted hover:border-faint hover:text-fg",
                        )}
                      >
                        {text}
                      </button>
                    )
                  })}
                </div>
              )
            }
            if (field.kind === "json" || field.kind === "text") {
              return (
                <Textarea
                  id={id}
                  rows={field.kind === "json" ? 4 : 3}
                  spellCheck={false}
                  value={String(value ?? "")}
                  placeholder={field.placeholder}
                  onChange={(event) => onChange(field.name, event.target.value)}
                  className={field.kind === "json" ? "font-mono text-[12.5px]" : ""}
                />
              )
            }
            return (
              <Input
                id={id}
                type={field.kind === "number" || field.kind === "integer" ? "number" : "text"}
                value={String(value ?? "")}
                placeholder={field.placeholder}
                onChange={(event) => onChange(field.name, event.target.value)}
              />
            )
          }}
        </FormField>
      ))}
    </div>
  )
}

function useFormValues(fields: Field[], key: string) {
  const [values, setValues] = useState<Record<string, string | boolean>>({})
  useEffect(() => {
    const next: Record<string, string | boolean> = {}
    for (const field of fields) next[field.name] = initialValue(field)
    setValues(next)
    // `key` and not `fields`: a new tool starts a new form, and `fields` is a
    // fresh array on every render, which would reset the form as it is typed
    // into. There is no linter here to tell about that.
  }, [key])
  return [values, setValues] as const
}

// --- result ----------------------------------------------------------------

function CallResult({ call }: { call: CallRecord }) {
  if (!call.ok && call.error) {
    return (
      <div className="rounded-lg border border-danger/40 bg-danger/5 p-3">
        <div className="mb-1.5 flex items-center gap-2">
          <Badge tone="danger">{call.error.code ?? "error"}</Badge>
          <span className="text-[12px] text-faint">{call.durationMs} ms</span>
        </div>
        <p className="text-[13px] text-fg">{call.error.message}</p>
        {call.error.details ? (
          <div className="mt-2 border-t border-danger/20 pt-2">
            <JsonView value={call.error.details} />
          </div>
        ) : null}
      </div>
    )
  }
  return (
    <div className="rounded-lg border border-border bg-surface p-3">
      <div className="mb-1.5 flex items-center gap-2">
        <Badge tone="ok">result</Badge>
        <span className="text-[12px] text-faint">{call.durationMs} ms</span>
        <CopyButton text={JSON.stringify(call.result, null, 2)} />
      </div>
      <JsonView value={call.result} />
    </div>
  )
}

// --- tools -----------------------------------------------------------------

export function ToolsPanel({ env, actions }: { env: LiveEnv; actions: EnvActions }) {
  const tools = env.tools ?? []
  const [selected, setSelected] = useState<string | null>(tools[0]?.name ?? null)
  const [raw, setRaw] = useState(false)
  const [rawText, setRawText] = useState("{}")

  useEffect(() => {
    if (!selected && tools.length > 0) setSelected(tools[0].name)
  }, [tools, selected])

  // Everything that belongs to the tool in the picker goes with it: the errors
  // from the last attempt, and the raw JSON box, which would otherwise offer
  // one tool's arguments to the next one. The form is where a new tool starts.
  useEffect(() => {
    setErrors({})
    setRaw(false)
    setRawText("{}")
  }, [selected])

  const tool: Tool | undefined = tools.find((entry) => entry.name === selected)
  const fields = useMemo(() => fieldsOf(tool?.input_schema), [tool])
  const [values, setValues] = useFormValues(fields, tool?.name ?? "")
  const [errors, setErrors] = useState<Record<string, string>>({})

  // The result panel belongs to the tool in the picker, so choosing another
  // tool empties it: a result card under a different tool's form reads as that
  // tool's answer. The transcript is where every call stays.
  const callCount = useRef(env.calls.length)
  callCount.current = env.calls.length
  const [since, setSince] = useState(env.calls.length)
  useEffect(() => {
    setSince(callCount.current)
  }, [tool?.name])

  const lastCall = env.calls
    .slice(since)
    .reverse()
    .find((call) => call.kind === "tool" || call.kind === "step")

  if (env.tools === null) {
    return <GenericStepPanel env={env} actions={actions} />
  }

  const submit = async () => {
    if (!tool) return
    if (raw) {
      try {
        const parsed = JSON.parse(rawText || "{}")
        setErrors({})
        await actions.callTool(tool.name, parsed)
      } catch (error) {
        setErrors({ __raw: (error as Error).message })
      }
      return
    }
    const { values: args, errors: found } = coerce(fields, values)
    setErrors(found)
    if (Object.keys(found).length > 0) return
    await actions.callTool(tool.name, args)
  }

  return (
    <div className="grid h-full grid-rows-[auto_minmax(0,1fr)] gap-4 p-5">
      <div className="flex flex-col gap-3">
        <div className="flex items-center gap-3">
          <div className="min-w-0 flex-1">
            <Combobox
              choices={tools.map((entry) => ({
                value: entry.name,
                label: entry.name,
                hint: entry.description,
              }))}
              value={selected}
              placeholder="Choose a tool…"
              onChange={setSelected}
            />
          </div>
          <span className="shrink-0 text-[12.5px] text-faint">
            {tools.length} tool{tools.length === 1 ? "" : "s"}
          </span>
        </div>
        {tool?.description ? (
          <p className="text-[13px] leading-relaxed text-muted">{tool.description}</p>
        ) : null}
      </div>

      <div className="grid min-h-0 grid-cols-1 gap-5 lg:grid-cols-2">
        <div className="flex min-h-0 flex-col gap-3 overflow-y-auto pr-1">
          <div className="flex items-center gap-2">
            <h3 className="text-[13px] font-semibold text-fg">Arguments</h3>
            <div className="ml-auto flex items-center gap-2">
              <span className="text-[12px] text-muted">Raw JSON</span>
              <Switch
                checked={raw}
                onChange={(next) => {
                  if (next) {
                    const { values: args } = coerce(fields, values)
                    setRawText(JSON.stringify(args, null, 2))
                  }
                  setRaw(next)
                }}
                label="Edit arguments as raw JSON"
              />
            </div>
          </div>

          {raw ? (
            <div className="flex flex-col gap-1.5">
              <Textarea
                rows={10}
                spellCheck={false}
                value={rawText}
                onChange={(event) => setRawText(event.target.value)}
                className="font-mono text-[12.5px]"
              />
              {errors.__raw ? (
                <p className="text-[12.5px] font-medium text-danger">{errors.__raw}</p>
              ) : null}
            </div>
          ) : (
            <ArgsForm
              fields={fields}
              values={values}
              errors={errors}
              onChange={(name, value) => setValues((current) => ({ ...current, [name]: value }))}
            />
          )}

          <div className="sticky bottom-0 flex items-center gap-2 bg-bg pt-3">
            <Button
              variant="primary"
              onClick={submit}
              disabled={env.busy || !tool || env.record.status !== "live"}
            >
              {env.busy ? "Calling…" : "Call tool"}
            </Button>
            {env.record.status !== "live" ? (
              <span className="text-[12.5px] text-muted">
                This environment is {env.record.status}.
              </span>
            ) : null}
          </div>
        </div>

        <div className="flex min-h-0 flex-col gap-3 overflow-y-auto pl-1">
          <h3 className="text-[13px] font-semibold text-fg">Result</h3>
          {lastCall ? (
            <CallResult call={lastCall} />
          ) : (
            <p className="text-[13px] text-muted">Nothing called yet.</p>
          )}
          {tool ? (
            <Disclosure title="Tool schema" hint={`${fields.length} argument${fields.length === 1 ? "" : "s"}`}>
              <JsonView value={tool.input_schema} />
            </Disclosure>
          ) : null}
        </div>
      </div>
    </div>
  )
}

// --- the environment whose actions are not tools ---------------------------

function GenericStepPanel({ env, actions }: { env: LiveEnv; actions: EnvActions }) {
  const action = env.schema?.action
  const skip = isToolCallAction(action) ? [] : ["metadata"]
  // `skip` is derived from `action` and rebuilt on every render, so `action`
  // alone is what the result depends on.
  const fields = useMemo(() => fieldsOf(action as JsonSchema | undefined, skip), [action])
  const [values, setValues] = useFormValues(fields, action?.title ?? "generic")
  const [errors, setErrors] = useState<Record<string, string>>({})
  const lastCall = [...env.calls].reverse().find((call) => call.kind === "step")

  const submit = async () => {
    const { values: payload, errors: found } = coerce(fields, values)
    setErrors(found)
    if (Object.keys(found).length > 0) return
    // A `const` discriminator is the action's own type, and the server needs it
    // even though there is nothing to choose: send it whether or not it was touched.
    for (const field of fields) {
      if (field.choices?.length === 1 && payload[field.name] === undefined) {
        payload[field.name] = field.choices[0]
      }
    }
    await actions.step(payload)
  }

  return (
    <div className="grid h-full grid-cols-1 gap-5 overflow-y-auto p-5 lg:grid-cols-2">
      <div className="flex flex-col gap-3">
        <div>
          <h3 className="text-[13px] font-semibold text-fg">
            Action{action?.title ? <span className="ml-2 font-mono text-muted">{action.title}</span> : null}
          </h3>
          <p className="mt-1 text-[12.5px] leading-snug text-muted">
            {env.toolsNote ??
              "This environment does not publish tools, so the form is generated from its action schema."}
          </p>
        </div>
        {fields.length === 0 ? (
          <p className="text-[13px] text-muted">
            No action schema is available. `GET /schema` could not be read from this origin.
          </p>
        ) : (
          <ArgsForm
            fields={fields}
            values={values}
            errors={errors}
            onChange={(name, value) => setValues((current) => ({ ...current, [name]: value }))}
          />
        )}
        <div className="flex items-center gap-2 pt-1">
          <Button
            variant="primary"
            onClick={submit}
            disabled={env.busy || fields.length === 0 || env.record.status !== "live"}
          >
            {env.busy ? "Stepping…" : "Step"}
          </Button>
        </div>
      </div>
      <div className="flex flex-col gap-3">
        <h3 className="text-[13px] font-semibold text-fg">Observation</h3>
        {lastCall ? <CallResult call={lastCall} /> : <p className="text-[13px] text-muted">Nothing stepped yet.</p>}
      </div>
    </div>
  )
}

// --- state -----------------------------------------------------------------

export function StatePanel({
  env,
  actions,
  follow,
  onFollowChange,
}: {
  env: LiveEnv
  actions: EnvActions
  follow: boolean
  onFollowChange: (next: boolean) => void
}) {
  const descriptions = useMemo(() => {
    const properties = env.schema?.state?.properties ?? {}
    const out: Record<string, string> = {}
    for (const [name, property] of Object.entries(properties)) {
      if (property.description) out[name] = property.description
    }
    return out
  }, [env.schema])

  const shown = env.state ?? env.record.finalState

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-5">
      <div className="flex items-center gap-3">
        <h3 className="text-[13px] font-semibold text-fg">State</h3>
        {env.record.finalState && !env.state ? <Badge tone="warn">saved at close</Badge> : null}
        <div className="ml-auto flex items-center gap-2">
          <span className="text-[12px] text-muted">Refresh after every call</span>
          <Switch checked={follow} onChange={onFollowChange} label="Refresh state after every call" />
          <Button
            size="sm"
            onClick={actions.refreshState}
            disabled={env.busy || env.record.status !== "live"}
          >
            Refresh
          </Button>
        </div>
      </div>
      {shown ? (
        <div className="rounded-lg border border-border bg-surface p-3">
          <JsonView value={shown} descriptions={descriptions} openDepth={4} />
        </div>
      ) : (
        <p className="text-[13px] text-muted">
          No state read yet. This is the `state` frame on the socket, not `GET /state` — that route
          answers from an environment that was never stepped.
        </p>
      )}
      {Object.keys(descriptions).length > 0 ? (
        <p className="text-[12px] text-faint">
          Dotted keys carry the description the environment publishes for that field. Hover to read it.
        </p>
      ) : null}
    </div>
  )
}

// --- transcript ------------------------------------------------------------

/**
 * A value as Python source.
 *
 * `JSON.stringify` is not a Python literal writer: it spells booleans `true`
 * and `false` and null `null`, none of which parse, and both turn up in ordinary
 * tool arguments. Strings are the one case JSON and Python agree on, escapes
 * included.
 */
function python(value: unknown): string {
  if (value === null || value === undefined) return "None"
  if (typeof value === "boolean") return value ? "True" : "False"
  if (typeof value === "number") return String(value)
  if (typeof value === "string") return JSON.stringify(value)
  if (Array.isArray(value)) return `[${value.map(python).join(", ")}]`
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>)
    return `{${entries.map(([key, item]) => `${JSON.stringify(key)}: ${python(item)}`).join(", ")}}`
  }
  return JSON.stringify(value)
}

function pythonFor(env: LiveEnv): string {
  const lines = [
    "from openenv import GenericEnvClient",
    "from openenv.core.env_server.mcp_types import CallToolAction",
    "",
    `with GenericEnvClient(base_url=${python(env.record.root)}) as env:`,
  ]
  const resetArgs = Object.entries(env.record.resetArgs)
    .map(([name, value]) => `${name}=${python(value)}`)
    .join(", ")
  lines.push(`    env.reset(${resetArgs})`)
  for (const call of env.calls) {
    if (call.kind === "tool") {
      lines.push(
        `    env.step(CallToolAction(tool_name=${python(call.name)}, arguments=${python(call.args)}))`,
      )
    } else if (call.kind === "step") {
      lines.push(`    env.step(${python(call.args)})`)
    }
  }
  return lines.join("\n")
}

export function TranscriptPanel({ env, actions }: { env: LiveEnv; actions: EnvActions }) {
  const [open, setOpen] = useState<number | null>(null)
  const calls = env.calls.filter((call) => call.kind !== "state")

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-5">
      <div className="flex items-center gap-3">
        <h3 className="text-[13px] font-semibold text-fg">Transcript</h3>
        <span className="text-[12.5px] text-faint">
          {calls.length} call{calls.length === 1 ? "" : "s"}
        </span>
        <div className="ml-auto flex items-center gap-2">
          <CopyButton text={pythonFor(env)} label="Copy as Python" />
          <Button size="sm" onClick={() => actions.replay(env.record.id)} disabled={calls.length === 0}>
            Replay into a new env
          </Button>
        </div>
      </div>

      <p className="text-[12.5px] leading-snug text-muted">
        The transcript is saved in this browser and outlives the socket. Replay opens a new
        environment with the same reset arguments and re-issues every call, which is how a run
        survives a reload without a server holding it.
      </p>

      {calls.length === 0 ? (
        <p className="text-[13px] text-muted">Nothing called yet.</p>
      ) : (
        <ol className="flex flex-col gap-1.5">
          {calls.map((call, index) => (
            <li key={call.id ?? index} className="rounded-lg border border-border bg-surface">
              <button
                type="button"
                onClick={() => setOpen(open === index ? null : index)}
                className="flex w-full items-center gap-2.5 px-3 py-2 text-left"
              >
                <span className="w-6 shrink-0 text-right font-mono text-[12px] text-faint">
                  {call.seq}
                </span>
                <span className={cx("size-1.5 shrink-0 rounded-full", call.ok ? "bg-ok" : "bg-danger")} />
                <span className="font-mono text-[13px] text-fg">{call.name}</span>
                <span className="truncate text-[12.5px] text-muted">
                  {Object.keys(call.args).length > 0 ? JSON.stringify(call.args) : "no arguments"}
                </span>
                <span className="ml-auto shrink-0 text-[12px] text-faint">{call.durationMs} ms</span>
              </button>
              {open === index ? (
                <div className="border-t border-border p-3">
                  <CallResult call={call} />
                </div>
              ) : null}
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}

// --- environment info ------------------------------------------------------

export function EnvInfoPanel({ env }: { env: LiveEnv }) {
  const rows: [string, string][] = [
    ["Root", env.record.root],
    ["Name", env.metadata?.name ?? env.record.envName ?? "unknown"],
    ["Status", env.record.status],
    ["Opened", new Date(env.record.createdAt).toLocaleString()],
  ]
  if (env.record.closedAt) rows.push(["Closed", new Date(env.record.closedAt).toLocaleString()])
  if (env.record.note) rows.push(["Note", env.record.note])

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-5">
      <div>
        <h3 className="text-[13px] font-semibold text-fg">Environment</h3>
        {env.metadata?.description ? (
          <p className="mt-1 text-[13px] leading-relaxed text-muted">{env.metadata.description}</p>
        ) : null}
      </div>

      <dl className="grid grid-cols-[110px_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-[13px]">
        {rows.map(([name, value]) => (
          <div key={name} className="contents">
            <dt className="text-muted">{name}</dt>
            <dd className="truncate font-mono text-[12.5px] text-fg">{value}</dd>
          </div>
        ))}
      </dl>

      <Disclosure title="Reset arguments" hint="what created this instance" defaultOpen>
        {Object.keys(env.record.resetArgs).length === 0 ? (
          <p className="text-[13px] text-muted">
            Reset was called with no arguments, so the environment used its own defaults.
          </p>
        ) : (
          <JsonView value={env.record.resetArgs} />
        )}
        <p className="mt-2 text-[12px] leading-snug text-faint">
          OpenEnv filters these against the signature of the environment's `reset()` and drops
          anything it does not accept, without an error. An argument that seems to do nothing was
          probably dropped.
        </p>
      </Disclosure>

      {env.schema ? (
        <>
          <Disclosure title="Action schema">
            <JsonView value={env.schema.action} />
          </Disclosure>
          <Disclosure title="Observation schema">
            <JsonView value={env.schema.observation} />
          </Disclosure>
          <Disclosure title="State schema">
            <JsonView value={env.schema.state} />
          </Disclosure>
        </>
      ) : (
        <div className="rounded-lg border border-warn/40 bg-warn/5 p-3">
          <p className="text-[13px] text-fg">{env.schemaNote ?? "No schema available."}</p>
          <p className="mt-1.5 text-[12.5px] leading-snug text-muted">
            `GET /schema` and `GET /metadata` are plain HTTP and the OpenEnv server registers no CORS
            middleware, so a page served from another origin cannot read them. The socket is
            unaffected: tools, calls and state all work. Open this UI from the environment's own
            origin to get the schema too.
          </p>
        </div>
      )}
    </div>
  )
}

export function NoEnvSelected({ onNew }: { onNew: () => void }) {
  return (
    <EmptyState
      title="No environment open"
      body="Every environment is one WebSocket session holding one private instance. Open one to list its tools, call them, and watch its state change."
      action={
        <Button variant="primary" onClick={onNew}>
          New environment
        </Button>
      }
    />
  )
}
