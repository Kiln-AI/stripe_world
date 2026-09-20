/**
 * The primitives. Hand-written in the shadcn/ui idiom -- same tokens, same
 * class-variant shape -- so swapping in the real components later is a copy
 * rather than a re-theme.
 */

import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type TextareaHTMLAttributes,
} from "react"

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ")
}

// --- button ----------------------------------------------------------------

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger"
  size?: "sm" | "md"
}

const BUTTON_VARIANTS = {
  primary:
    "bg-accent text-accent-fg hover:brightness-110 active:brightness-95 disabled:bg-border disabled:text-faint",
  secondary:
    "bg-raised text-fg border border-border hover:border-faint disabled:text-faint disabled:hover:border-border",
  ghost: "text-muted hover:text-fg hover:bg-raised disabled:text-faint disabled:hover:bg-transparent",
  danger: "bg-transparent text-danger border border-border hover:border-danger hover:bg-danger/10",
}

export function Button({ variant = "secondary", size = "md", className, ...rest }: ButtonProps) {
  return (
    <button
      {...rest}
      className={cx(
        "inline-flex shrink-0 items-center justify-center gap-1.5 rounded-lg font-medium transition-[background-color,border-color,filter,color] disabled:cursor-not-allowed",
        size === "sm" ? "h-7 px-2.5 text-[13px]" : "h-9 px-3.5 text-sm",
        BUTTON_VARIANTS[variant],
        className,
      )}
    />
  )
}

// --- text inputs -----------------------------------------------------------

const CONTROL =
  "w-full rounded-lg border border-border bg-surface px-3 text-fg placeholder:text-faint " +
  "transition-colors hover:border-faint focus:border-accent focus:outline-none"

export function Input({ className, ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...rest} className={cx(CONTROL, "h-9", className)} />
}

export function Textarea({ className, ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...rest} className={cx(CONTROL, "py-2 leading-relaxed", className)} />
}

export function Switch({
  checked,
  onChange,
  label,
}: {
  checked: boolean
  onChange: (next: boolean) => void
  label?: string
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className={cx(
        "relative h-5 w-9 shrink-0 rounded-full border transition-colors",
        checked ? "border-accent bg-accent" : "border-border bg-raised",
      )}
    >
      <span
        className={cx(
          "absolute top-0.5 h-3.5 w-3.5 rounded-full bg-surface transition-[left]",
          checked ? "left-[18px]" : "left-0.5",
        )}
      />
    </button>
  )
}

// --- labelled field --------------------------------------------------------

export function FormField({
  label,
  description,
  required,
  typeHint,
  error,
  children,
}: {
  label: string
  description?: string
  required?: boolean
  typeHint?: string
  error?: string
  children: (id: string) => ReactNode
}) {
  const id = useId()
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-baseline gap-2">
        <label htmlFor={id} className="text-[13px] font-medium text-fg">
          {label}
        </label>
        {required ? (
          <span className="text-[11px] font-medium text-accent">required</span>
        ) : (
          <span className="text-[11px] text-faint">optional</span>
        )}
        {typeHint ? (
          <span className="ml-auto font-mono text-[11px] text-faint">{typeHint}</span>
        ) : null}
      </div>
      {description ? <p className="text-[12.5px] leading-snug text-muted">{description}</p> : null}
      {children(id)}
      {error ? <p className="text-[12.5px] font-medium text-danger">{error}</p> : null}
    </div>
  )
}

// --- disclosure ------------------------------------------------------------

export function Disclosure({
  title,
  hint,
  defaultOpen = false,
  children,
}: {
  title: string
  hint?: string
  defaultOpen?: boolean
  children: ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="rounded-lg border border-border bg-surface">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-[13px] font-medium text-fg"
      >
        <Chevron open={open} />
        {title}
        {hint ? <span className="ml-auto text-[12px] font-normal text-faint">{hint}</span> : null}
      </button>
      {open ? <div className="border-t border-border p-3">{children}</div> : null}
    </div>
  )
}

export function Chevron({ open }: { open: boolean }) {
  return (
    <svg
      viewBox="0 0 12 12"
      className={cx("size-3 shrink-0 text-faint transition-transform", open && "rotate-90")}
      aria-hidden
    >
      <path d="M4 2.5 8 6l-4 3.5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

// --- badge -----------------------------------------------------------------

export function Badge({
  tone = "neutral",
  children,
}: {
  tone?: "neutral" | "ok" | "warn" | "danger" | "accent"
  children: ReactNode
}) {
  const tones = {
    neutral: "border-border text-muted",
    ok: "border-ok/40 text-ok",
    warn: "border-warn/40 text-warn",
    danger: "border-danger/40 text-danger",
    accent: "border-accent/40 text-accent",
  }
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] font-medium",
        tones[tone],
      )}
    >
      {children}
    </span>
  )
}

export function Dot({ tone }: { tone: "ok" | "warn" | "danger" | "neutral" }) {
  const tones = {
    ok: "bg-ok",
    warn: "bg-warn",
    danger: "bg-danger",
    neutral: "bg-faint",
  }
  return <span className={cx("size-2 shrink-0 rounded-full", tones[tone])} />
}

// --- modal -----------------------------------------------------------------

export function Modal({
  title,
  subtitle,
  onClose,
  children,
  footer,
}: {
  title: string
  subtitle?: string
  onClose: () => void
  children: ReactNode
  footer?: ReactNode
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [onClose])

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/45 p-6 backdrop-blur-[2px]">
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="my-8 w-full max-w-xl rounded-card border border-border bg-surface shadow-2xl"
      >
        <div className="flex items-start gap-3 border-b border-border px-5 py-4">
          <div className="min-w-0">
            <h2 className="text-[15px] font-semibold text-fg">{title}</h2>
            {subtitle ? <p className="mt-0.5 text-[12.5px] text-muted">{subtitle}</p> : null}
          </div>
          <Button variant="ghost" size="sm" onClick={onClose} className="ml-auto" aria-label="Close">
            ✕
          </Button>
        </div>
        <div className="max-h-[65vh] overflow-y-auto px-5 py-4">{children}</div>
        {footer ? (
          <div className="flex items-center gap-2 border-t border-border px-5 py-3">{footer}</div>
        ) : null}
      </div>
    </div>
  )
}

// --- combobox --------------------------------------------------------------

export type Choice = { value: string; label: string; hint?: string }

/**
 * The tool picker. A filterable dropdown rather than a text box, because the
 * environment already publishes the list and nobody should have to type a tool
 * name from memory.
 */
export function Combobox({
  choices,
  value,
  placeholder,
  onChange,
}: {
  choices: Choice[]
  value: string | null
  placeholder: string
  onChange: (value: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [filter, setFilter] = useState("")
  const [active, setActive] = useState(0)
  const container = useRef<HTMLDivElement>(null)

  const matches = useMemo(() => {
    const needle = filter.trim().toLowerCase()
    if (!needle) return choices
    return choices.filter(
      (choice) =>
        choice.label.toLowerCase().includes(needle) ||
        (choice.hint ?? "").toLowerCase().includes(needle),
    )
  }, [choices, filter])

  useEffect(() => {
    if (!open) return
    const onClick = (event: MouseEvent) => {
      if (!container.current?.contains(event.target as Node)) setOpen(false)
    }
    window.addEventListener("mousedown", onClick)
    return () => window.removeEventListener("mousedown", onClick)
  }, [open])

  const selected = choices.find((choice) => choice.value === value)

  const commit = (choice: Choice) => {
    onChange(choice.value)
    setOpen(false)
    setFilter("")
  }

  return (
    <div ref={container} className="relative">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className={cx(
          CONTROL,
          "flex h-10 items-center gap-2 text-left",
          open && "border-accent",
        )}
      >
        {selected ? (
          // The description is shown in full under the picker, so the collapsed
          // trigger carries the name alone rather than repeating it truncated.
          <span className="font-mono text-[13px] text-fg">{selected.label}</span>
        ) : (
          <span className="text-faint">{placeholder}</span>
        )}
        <span className="ml-auto text-faint">
          <Chevron open={open} />
        </span>
      </button>

      {open ? (
        <div className="absolute z-40 mt-1 w-full overflow-hidden rounded-lg border border-border bg-surface shadow-xl">
          <input
            autoFocus
            value={filter}
            placeholder="Filter tools…"
            onChange={(event) => {
              setFilter(event.target.value)
              setActive(0)
            }}
            onKeyDown={(event) => {
              if (event.key === "ArrowDown") {
                event.preventDefault()
                setActive((index) => Math.min(index + 1, matches.length - 1))
              } else if (event.key === "ArrowUp") {
                event.preventDefault()
                setActive((index) => Math.max(index - 1, 0))
              } else if (event.key === "Enter" && matches[active]) {
                event.preventDefault()
                commit(matches[active])
              } else if (event.key === "Escape") {
                setOpen(false)
              }
            }}
            className="w-full border-b border-border bg-raised px-3 py-2 text-[13px] text-fg placeholder:text-faint focus:outline-none"
          />
          <div className="max-h-72 overflow-y-auto py-1">
            {matches.length === 0 ? (
              <p className="px-3 py-2 text-[13px] text-faint">No tool matches that.</p>
            ) : (
              matches.map((choice, index) => (
                <button
                  key={choice.value}
                  type="button"
                  onMouseEnter={() => setActive(index)}
                  onClick={() => commit(choice)}
                  className={cx(
                    "flex w-full flex-col items-start gap-0.5 px-3 py-1.5 text-left",
                    index === active && "bg-raised",
                  )}
                >
                  <span className="font-mono text-[13px] text-fg">{choice.label}</span>
                  {choice.hint ? (
                    <span className="line-clamp-2 text-[12px] leading-snug text-muted">
                      {choice.hint}
                    </span>
                  ) : null}
                </button>
              ))
            )}
          </div>
        </div>
      ) : null}
    </div>
  )
}

// --- JSON viewer -----------------------------------------------------------

function isCollection(value: unknown): value is Record<string, unknown> | unknown[] {
  return typeof value === "object" && value !== null
}

function preview(value: unknown[] | Record<string, unknown>): string {
  return Array.isArray(value)
    ? `[] ${value.length} item${value.length === 1 ? "" : "s"}`
    : `{} ${Object.keys(value).length} key${Object.keys(value).length === 1 ? "" : "s"}`
}

function Leaf({ value }: { value: unknown }) {
  if (typeof value === "string") return <span className="text-ok">"{value}"</span>
  if (typeof value === "number") return <span className="text-accent">{value}</span>
  if (typeof value === "boolean") return <span className="text-warn">{String(value)}</span>
  if (value === null) return <span className="text-faint">null</span>
  return <span>{String(value)}</span>
}

function Node({
  name,
  value,
  depth,
  openDepth,
  descriptions,
}: {
  name: string | null
  value: unknown
  depth: number
  openDepth: number
  descriptions?: Record<string, string>
}) {
  const [open, setOpen] = useState(depth < openDepth)
  const description = name ? descriptions?.[name] : undefined

  if (!isCollection(value)) {
    return (
      <div className="flex gap-2 py-px pl-4" title={description}>
        {name === null ? null : (
          <span className={cx("shrink-0 text-fg", description && "underline decoration-dotted decoration-faint underline-offset-2")}>
            {name}:
          </span>
        )}
        <Leaf value={value} />
      </div>
    )
  }

  const entries = Array.isArray(value)
    ? value.map((item, index) => [String(index), item] as const)
    : Object.entries(value)

  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        title={description}
        className="flex w-full items-center gap-1 py-px text-left hover:bg-raised"
      >
        <Chevron open={open} />
        {name === null ? null : (
          <span className={cx("text-fg", description && "underline decoration-dotted decoration-faint underline-offset-2")}>
            {name}:
          </span>
        )}
        <span className="text-faint">{preview(value)}</span>
      </button>
      {open ? (
        <div className="ml-2 border-l border-border pl-2">
          {entries.length === 0 ? (
            <div className="py-px pl-4 text-faint">empty</div>
          ) : (
            entries.map(([key, item]) => (
              <Node
                key={key}
                name={key}
                value={item}
                depth={depth + 1}
                openDepth={openDepth}
                descriptions={descriptions}
              />
            ))
          )}
        </div>
      ) : null}
    </div>
  )
}

/**
 * A JSON tree, with the field descriptions from `GET /schema` attached to the
 * keys they describe. Hovering a dotted key shows what the environment says it
 * means.
 */
export function JsonView({
  value,
  descriptions,
  openDepth = 2,
}: {
  value: unknown
  descriptions?: Record<string, string>
  /** How many levels start expanded. A state document is worth opening deeper. */
  openDepth?: number
}) {
  return (
    <div className="overflow-x-auto font-mono text-[12.5px] leading-[1.7]">
      <Node name={null} value={value} depth={0} openDepth={openDepth} descriptions={descriptions} />
    </div>
  )
}

export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false)
  return (
    <Button
      size="sm"
      variant="ghost"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text)
          setDone(true)
          window.setTimeout(() => setDone(false), 1200)
        } catch {
          // A denied clipboard is not worth an error state in a dev tool, but
          // it is not worth saying "Copied" over either.
        }
      }}
    >
      {done ? "Copied" : label}
    </Button>
  )
}

export function EmptyState({
  title,
  body,
  action,
}: {
  title: string
  body: string
  action?: ReactNode
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 px-8 text-center">
      <h3 className="text-[15px] font-semibold text-fg">{title}</h3>
      <p className="max-w-md text-[13px] leading-relaxed text-muted">{body}</p>
      {action}
    </div>
  )
}
