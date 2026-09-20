/**
 * Drives the built UI against the mock environment in a real browser.
 *
 * Start the mocks first:
 *   node mock/server.mjs --port 8000 &
 *   node mock/server.mjs --port 8001 --plain &
 *   npm run build && node e2e/smoke.mjs
 *
 * Screenshots land in e2e/shots/. Any console error or page error fails the run.
 */

import { chromium } from "playwright"
import { mkdirSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"

const here = dirname(fileURLToPath(import.meta.url))
const shots = join(here, "shots")
mkdirSync(shots, { recursive: true })

const problems = []
let step = 0
const shot = async (page, name) => {
  step += 1
  await page.screenshot({ path: join(shots, `${String(step).padStart(2, "0")}-${name}.png`) })
}

const check = (condition, message) => {
  if (!condition) problems.push(message)
}

// This container ships a Chromium that may not match the version the installed
// playwright package expects, so allow an explicit binary.
const executablePath = process.env.CHROMIUM_PATH || undefined
const browser = await chromium.launch(executablePath ? { executablePath } : {})
const context = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: 2,
  colorScheme: process.env.SCHEME === "light" ? "light" : "dark",
})
const page = await context.newPage()

// A cross-origin `/schema` fetch is blocked by the browser and logged by it.
// That case is exercised on purpose below, so it is not a failure here.
const EXPECTED = /CORS policy|ERR_FAILED|favicon|status of 404/
page.on("console", (message) => {
  if (message.type() === "error" && !EXPECTED.test(message.text())) {
    problems.push(`console error: ${message.text()}`)
  }
})
page.on("pageerror", (error) => problems.push(`page error: ${error.message}`))

// --- open the page, served by the environment itself ------------------------

await page.goto("http://127.0.0.1:8000/console", { waitUntil: "networkidle" })
await shot(page, "empty")
check(await page.getByText("No environment open").isVisible(), "the empty state did not render")

// --- new environment, with reset arguments in the Advanced box --------------

await page.getByRole("button", { name: "New environment" }).first().click()
await page.getByText("Advanced: reset arguments").click()
await page.locator("textarea").first().fill('{\n  "fixture": "agency",\n  "seed": 7\n}')
await page.getByPlaceholder("run 1").fill("agency run")
await page.waitForTimeout(500)
await shot(page, "new-env-dialog")
check(
  await page.getByText("Issues, sprints and comments").isVisible(),
  "the dialog did not name the environment from /metadata",
)

await page.getByRole("button", { name: "Open environment" }).click()
await page.waitForTimeout(600)
await shot(page, "tools-empty")
check(await page.getByText("5 tools").first().isVisible(), "the tool list did not load")

// --- an optional argument keeps the hints its wrapper carries ---------------
//
// `search_issues.limit` is spelled `int | None = 20`, so pydantic puts the type
// on the `anyOf` branch and the title, description and default on the wrapper.
// Reading only the branch loses all three.

check(
  (await page.getByLabel("Limit").inputValue()) === "20",
  "an optional argument lost the default its wrapper carries",
)
check(
  await page.getByText("How many issues to return.").isVisible(),
  "an optional argument lost the description its wrapper carries",
)

// --- a tool call, through the generated form --------------------------------

await page.getByRole("button", { name: /Choose a tool|search_issues/ }).first().click()
await page.getByPlaceholder("Filter tools…").fill("create")
await shot(page, "tool-dropdown")
await page.getByRole("button", { name: /create_issue/ }).click()
await page.waitForTimeout(200)

await page.getByLabel("Title").fill("Stack trace in the session cookie")
await page.getByLabel("Body").fill("Reproduced on staging with a stale session.")
await page.getByRole("button", { name: "DESIGN", exact: true }).click()
await shot(page, "tool-form")

await page.getByRole("button", { name: "Call tool" }).click()
await page.waitForTimeout(400)
await shot(page, "tool-result")
check(await page.getByText("result").first().isVisible(), "the result card did not render")
check(await page.getByText("DESIGN-").first().isVisible(), "the new issue key is not in the result")

// --- changing tool empties the result panel and the raw JSON box ------------

// Raw mode holds the arguments of the tool it was opened for, so it cannot
// survive into the next tool's form.
await page.getByRole("switch", { name: "Edit arguments as raw JSON" }).click()
check(
  (await page.locator("textarea").first().inputValue()).includes("Stack trace"),
  "raw mode did not open on the current tool's arguments",
)

await page.getByRole("button", { name: /create_issue/ }).first().click()
await page.getByPlaceholder("Filter tools…").fill("get_issue")
await page.getByRole("button", { name: /get_issue/ }).first().click()
await page.waitForTimeout(200)
check(
  await page.getByText("Nothing called yet.").isVisible(),
  "the previous tool's result was still shown after changing tool",
)
check(
  (await page.getByRole("switch", { name: "Edit arguments as raw JSON" }).getAttribute(
    "aria-checked",
  )) === "false",
  "the raw JSON box survived a tool change",
)

// --- a tool that refuses ----------------------------------------------------

await page.getByRole("button", { name: /get_issue/ }).first().click()
await page.getByPlaceholder("Filter tools…").fill("transition")
await page.getByRole("button", { name: /transition_issue/ }).click()
await page.getByLabel("Issue id").fill("i_8f21")
await page.getByRole("button", { name: "done", exact: true }).click()
await page.getByRole("button", { name: "Call tool" }).click()
await page.waitForTimeout(400)
await shot(page, "tool-error")
check(
  await page.getByText("WORKFLOW_REFUSED").isVisible(),
  "a tool error did not render as an error card",
)

// --- required argument, caught before the frame goes out --------------------

await page.getByLabel("Issue id").fill("")
await page.getByRole("button", { name: "Call tool" }).click()
await page.waitForTimeout(150)
check(await page.getByText("required").first().isVisible(), "an empty required field was not caught")

// --- state ------------------------------------------------------------------

await page.getByRole("button", { name: "State", exact: true }).click()
await page.waitForTimeout(300)
await page.getByRole("button", { name: "Refresh" }).click()
await page.waitForTimeout(300)
await shot(page, "state")
check(await page.getByText("episode_id").isVisible(), "the state document did not render")

// --- transcript -------------------------------------------------------------

await page.getByRole("button", { name: "Transcript", exact: true }).click()
await page.waitForTimeout(200)
await shot(page, "transcript")
check(await page.getByText("3 calls").isVisible(), "the transcript did not count the calls")

// --- environment info, schema present because this page is same-origin ------

await page.getByRole("button", { name: "Environment", exact: true }).click()
await page.waitForTimeout(200)
await shot(page, "env-info")
check(await page.getByText("Action schema").isVisible(), "the schema panel is missing")

// --- a second environment, cross-origin -------------------------------------
//
// This page is served from :8000, so `/schema` and `/metadata` on :8001 are
// unreadable: no CORS middleware exists on an OpenEnv server. The socket is
// unaffected, which is the degradation the UI is built around.

await page.getByRole("button", { name: "New environment" }).first().click()
await page.getByRole("textbox").first().fill("http://127.0.0.1:8001")
await page.waitForTimeout(700)
await page.getByRole("button", { name: "Open environment" }).click()
await page.waitForTimeout(700)
await shot(page, "cross-origin")
check(
  await page.getByText(/rejected a list_tools action/).isVisible(),
  "the non-tool environment did not fall back to the action form",
)

// --- both environments in the list ------------------------------------------

await page.getByText("agency run").click()
await page.waitForTimeout(200)
await shot(page, "two-envs")
check((await page.getByText(/live$/).count()) >= 1, "no environment is shown as live")

// --- the same non-tool environment, same-origin, so the form generates ------

const plain = await context.newPage()
plain.on("pageerror", (error) => problems.push(`page error: ${error.message}`))
await plain.goto("http://127.0.0.1:8001/console", { waitUntil: "networkidle" })
await plain.getByRole("button", { name: "New environment" }).first().click()
await plain.waitForTimeout(700)
await plain.getByRole("button", { name: "Open environment" }).click()
await plain.waitForTimeout(700)
await plain.getByRole("button", { name: "north", exact: true }).click()
await plain.getByRole("button", { name: "Step" }).click()
await plain.waitForTimeout(400)
await shot(plain, "plain-step")
check(
  await plain.getByText("Which way to move the agent on the grid.").isVisible(),
  "the action schema form did not render for a non-tool environment",
)
check(await plain.getByText("0.5").first().isVisible(), "the observation did not render")

await browser.close()

if (problems.length > 0) {
  console.error(`${problems.length} problem(s):`)
  for (const problem of problems) console.error(`  - ${problem}`)
  process.exit(1)
}
console.log(`ok: ${step} screenshots in e2e/shots/`)
