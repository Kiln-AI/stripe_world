/**
 * Fail if anything bundled into the console carries a copyleft licence.
 *
 * The build inlines its dependencies into one `index.html`, and that file is
 * vendored into the Python package and shipped in the wheel. So an npm
 * dependency here reaches the same people a PyPI dependency does, under the
 * same rule: no copyleft, because Seahaven is vendored into other people's
 * products. `scripts/check_licences.py` is that rule for the Python side; this
 * is the same rule for the side that ends up inside a single HTML file.
 *
 * Production dependencies only. A build tool is not bundled -- what a bundler
 * or a CSS engine emits is this project's own output, not a copy of the tool --
 * which is the same boundary the Python gate draws around `pytest` and `ruff`.
 * Today that boundary keeps `lightningcss` (MPL-2.0) and `caniuse-lite`
 * (CC-BY-4.0) out of scope; moving either into `dependencies` would put it in.
 *
 * This is an allowlist, not a list of the licences we thought to refuse: an
 * identifier nobody has classified fails, which is the only way an unread
 * licence cannot ship by accident.
 *
 * Run it as `npm run licences`; CI does.
 */

import { execFileSync } from "node:child_process"

/** SPDX identifiers this project accepts in a bundled dependency. */
const ALLOWED = new Set([
  "0BSD",
  "Apache-2.0",
  "BSD-2-Clause",
  "BSD-3-Clause",
  "BlueOak-1.0.0",
  "BSL-1.0",
  "CC0-1.0",
  "ISC",
  "MIT",
  "MIT-0",
  // Copyleft at file scope only: changing an MPL-2.0 file means publishing that
  // file, and nothing reaches the code around it. Allowed for that reason and
  // no other -- this is not a door for copyleft in general.
  "MPL-2.0",
  "Unlicense",
  "Zlib",
])

/** Refused however they are spelled: `GPL-3.0-or-later`, `LGPL-2.1-only`. */
const COPYLEFT = /^(?:A|L)?GPL/i

/** What a package says about its licence, in any of the shapes npm reports. */
function expressionOf(node) {
  const declared = node.license
  if (typeof declared === "string") return declared.trim()
  // The pre-SPDX shapes, still published by a few old packages.
  if (declared && typeof declared === "object" && typeof declared.type === "string") {
    return declared.type.trim()
  }
  if (Array.isArray(declared)) return declared.map((entry) => expressionOf({ license: entry })).join(" OR ")
  return ""
}

/**
 * Whether every identifier in an expression is allowed.
 *
 * Every term, `OR` included: `GPL-3.0-only OR MIT` is a choice a person should
 * make and record, not one this script should make quietly by reading past the
 * branch it may not take.
 */
function allowed(expression) {
  const terms = expression
    .replace(/[()]/g, " ")
    .split(/\s+/)
    .filter((term) => term && !["AND", "OR", "WITH"].includes(term.toUpperCase()))
  if (terms.some((term) => COPYLEFT.test(term))) return false
  return terms.length > 0 && terms.every((term) => ALLOWED.has(term))
}

const query = execFileSync("npm", ["query", ".prod", "--json"], { encoding: "utf8" })
// A node with no `location` is this package itself, which is not a dependency.
const packages = JSON.parse(query).filter((node) => node.location)

const problems = packages
  .map((node) => ({ id: node._id ?? node.name, expression: expressionOf(node) || "<none declared>" }))
  .filter((entry) => !allowed(entry.expression))

for (const { id, expression } of problems) {
  console.error(`${id}: ${expression} is not allowed`)
}

if (problems.length > 0) {
  console.error(`${problems.length} bundled dependencies need review`)
  process.exit(1)
}

console.log(`every bundled dependency is allowed (${packages.length} packages)`)
