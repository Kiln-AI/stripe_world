/**
 * The New environment flow.
 *
 * `reset` lives here and nowhere else. It is not an action you take against a
 * running environment, it is the thing that creates one: an environment that
 * has been reset twice is two different runs sharing one row in the list, which
 * is exactly the confusion this dialog exists to remove.
 */

import { useEffect, useRef, useState } from "react"
import { fetchMetadata, normalizeRoot, type EnvMetadata } from "../lib/openenv"
import { parseArgsObject } from "../lib/schema"
import { Badge, Button, Disclosure, FormField, Input, Modal, Textarea } from "./ui"

export type NewEnvRequest = {
  root: string
  label: string
  resetArgs: Record<string, unknown>
}

export function NewEnvDialog({
  defaultRoot,
  defaultArgs,
  busy,
  error,
  onCancel,
  onCreate,
}: {
  defaultRoot: string
  defaultArgs?: Record<string, unknown>
  busy: boolean
  error: string | null
  onCancel: () => void
  onCreate: (request: NewEnvRequest) => void
}) {
  const [root, setRoot] = useState(defaultRoot)
  const [label, setLabel] = useState("")
  const [argsText, setArgsText] = useState(
    defaultArgs && Object.keys(defaultArgs).length > 0 ? JSON.stringify(defaultArgs, null, 2) : "",
  )
  const [probe, setProbe] = useState<{ state: "idle" | "looking" | "found" | "quiet"; meta: EnvMetadata | null }>({
    state: "idle",
    meta: null,
  })
  const probeToken = useRef(0)

  const parsed = parseArgsObject(argsText)

  // Name the environment before connecting, when the browser is allowed to read
  // `/metadata`. A quiet answer is not an error: it usually means this page is
  // on a different origin than the environment.
  useEffect(() => {
    const token = ++probeToken.current
    setProbe({ state: "looking", meta: null })
    const timer = window.setTimeout(async () => {
      const meta = await fetchMetadata(root)
      if (probeToken.current !== token) return
      setProbe({ state: meta ? "found" : "quiet", meta })
    }, 350)
    return () => window.clearTimeout(timer)
  }, [root])

  const submit = () => {
    if (parsed.error) return
    onCreate({ root: normalizeRoot(root), label: label.trim(), resetArgs: parsed.value })
  }

  return (
    <Modal
      title="New environment"
      subtitle="Opens a socket, then resets it. One environment is one session holding one private instance."
      onClose={onCancel}
      footer={
        <>
          <Button variant="primary" onClick={submit} disabled={busy || Boolean(parsed.error)}>
            {busy ? "Opening…" : "Open environment"}
          </Button>
          <Button variant="ghost" onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
          {error ? <span className="ml-auto text-[12.5px] text-danger">{error}</span> : null}
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <FormField
          label="Environment URL"
          description="The root the server is on. The socket is this plus /ws."
          required
        >
          {(id) => (
            <Input
              id={id}
              value={root}
              spellCheck={false}
              placeholder="http://127.0.0.1:8000"
              onChange={(event) => setRoot(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") submit()
              }}
            />
          )}
        </FormField>

        <div className="flex min-h-[20px] items-center gap-2 text-[12.5px]">
          {probe.state === "looking" ? <span className="text-faint">Looking…</span> : null}
          {probe.state === "found" && probe.meta ? (
            <>
              <Badge tone="ok">{probe.meta.name ?? "environment"}</Badge>
              <span className="truncate text-muted">{probe.meta.description}</span>
            </>
          ) : null}
          {probe.state === "quiet" ? (
            <span className="text-muted">
              No metadata from this origin. That is normal cross-origin, and the socket still works.
            </span>
          ) : null}
        </div>

        <FormField label="Label" description="What to call this run in the list. Optional.">
          {(id) => (
            <Input
              id={id}
              value={label}
              placeholder={probe.meta?.name ? `${probe.meta.name} run` : "run 1"}
              onChange={(event) => setLabel(event.target.value)}
            />
          )}
        </FormField>

        <Disclosure
          title="Advanced: reset arguments"
          hint="passed to the environment's reset()"
          defaultOpen={Boolean(defaultArgs && Object.keys(defaultArgs).length > 0)}
        >
          <div className="flex flex-col gap-2">
            <p className="text-[12.5px] leading-snug text-muted">
              OpenEnv publishes no schema for these, so this is the one box the UI cannot generate a
              form for. Whatever the environment's `reset()` accepts goes here, as a JSON object:
              commonly <code className="font-mono text-fg">seed</code> and{" "}
              <code className="font-mono text-fg">episode_id</code>, plus whatever that environment
              adds. Anything it does not accept is dropped silently by the server, so an argument
              that seems to do nothing was probably not one of its parameters.
            </p>
            <Textarea
              rows={5}
              spellCheck={false}
              value={argsText}
              placeholder={'{\n  "fixture": "small_startup",\n  "seed": 7\n}'}
              onChange={(event) => setArgsText(event.target.value)}
              className="font-mono text-[12.5px]"
            />
            {parsed.error ? (
              <p className="text-[12.5px] font-medium text-danger">{parsed.error}</p>
            ) : null}
          </div>
        </Disclosure>
      </div>
    </Modal>
  )
}
