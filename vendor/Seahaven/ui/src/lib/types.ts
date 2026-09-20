import type { CallRecord, EnvRecord } from "./db"
import type { EnvMetadata, EnvSchema, Tool } from "./openenv"

/** One environment as the UI holds it: the saved record plus what the socket found. */
export type LiveEnv = {
  record: EnvRecord
  /** The environment's tools, or `null` when its actions are not tools. */
  tools: Tool[] | null
  /** Why there is no tool list, shown where the tool picker would be. */
  toolsNote: string | null
  schema: EnvSchema | null
  /** Why `/schema` and `/metadata` are missing, when they are. */
  schemaNote: string | null
  metadata: EnvMetadata | null
  calls: CallRecord[]
  state: Record<string, unknown> | null
  busy: boolean
}

export type EnvActions = {
  callTool: (name: string, args: Record<string, unknown>) => Promise<void>
  step: (action: Record<string, unknown>) => Promise<void>
  refreshState: () => Promise<void>
  close: (id: string) => Promise<void>
  forget: (id: string) => Promise<void>
  replay: (id: string) => Promise<void>
}
