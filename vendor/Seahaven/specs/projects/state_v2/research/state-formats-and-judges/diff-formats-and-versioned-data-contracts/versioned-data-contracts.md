# Versioning long-lived structured outputs

The question: a consumer saves a structured document today and re-reads it in three years, with
different code. What does the document have to carry, and what rules does the producer have to
follow, for that to work?

Seven bodies of practice, read from primary sources where the host was reachable. All fetched
2026-09-14. Blocked hosts noted per section.

---

## The one-line summary of the field

Everybody converged on the same two-part answer, with only the spelling varying:

1. **A version identifier travels inside every document**, not out of band — `specversion`,
   `$schema`, `schema_url`, `Stripe-Version`, the Avro writer's schema, the Schema Registry's
   5-byte prefix. Nobody relies on "you'll know which version you have."
2. **Someone owns a compatibility contract** that says which changes are allowed without bumping it
   — additive-only, defaults for absent fields, never reuse a name/number, never change a field's
   meaning. Where that contract is not enough, a **transformation chain** (upcasters, OTel schema
   files, Stripe's version-change modules) converts old to new at read time.

The second half is what "version field plus formatter" means in the wild. It has three independent
implementations documented below (OTel, Stripe, event-sourcing upcasters), and they agree on the
shape: **an ordered list of small, pure, one-version-step transformations, applied in sequence**.

---

## CloudEvents — `specversion`

Source: [`cloudevents/spec.md`](https://raw.githubusercontent.com/cloudevents/spec/main/cloudevents/spec.md)
— head of file reads `# CloudEvents - Version 1.0.3-wip`, so 1.0.2 is the last release and 1.0.3 is
in flight as of this fetch.

```
#### specversion

- Type: `String`
- Description: The version of the CloudEvents specification which the event
  uses. This enables the interpretation of the context. Compliant event
  producers MUST use a value of `1.0` when referring to this version of the
  specification.

  Currently, this attribute will only have the 'major' and 'minor' version
  numbers included in it. This allows for 'patch' changes to the specification
  to be made without changing this property's value in the serialization.
  Note: for 'release candidate' releases a suffix might be used for testing
  purposes.

- Constraints:
  - REQUIRED
  - MUST be a non-empty string
```

A whole CloudEvent is nine-ish flat attributes with `data` at the bottom:

```json
{
    "specversion" : "1.0",
    "type" : "com.github.pull_request.opened",
    "source" : "https://github.com/cloudevents/spec/pull",
    "subject" : "123",
    "id" : "A234-1234-1234",
    "time" : "2018-04-05T17:31:00Z",
    "datacontenttype" : "text/xml",
    "data" : "<much wow=\"xml\"/>"
}
```

Four design points worth copying:

- **`specversion` is REQUIRED and first.** A reader can dispatch on it before understanding anything
  else in the document.
- **Major.minor only; patch changes do not appear in the serialization.** This is the explicit
  answer to "won't my version field churn?" — you decide up front which class of change is allowed
  to be invisible.
- **Homogeneity is enforced at the batch boundary**: "all CloudEvents within the same batch MUST have
  the same value for the `specversion` attribute." Mixed-version collections are a real problem and
  they legislated it away rather than solving it.
- **`type` is producer-defined and is itself expected to carry a version**: the spec notes the format
  "might include information such as the version of the type". Two levels of versioning — the
  envelope's and the payload's — is normal, not a smell.

---

## JSON Schema — `$schema` and `$id`

Sources: [json-schema.org/understanding-json-schema/reference/schema](https://json-schema.org/understanding-json-schema/reference/schema)
and [.../structuring](https://json-schema.org/understanding-json-schema/structuring) (json-schema.org
is one of the few doc hosts reachable from this session).

`$schema` declares the **dialect**:

> "The `$schema` keyword is used to declare which dialect of JSON Schema the schema was written for."
> … "It's recommended that all JSON Schemas have a `$schema` keyword to communicate to readers and
> tooling which specification version is intended." … "`$schema` applies to the entire document and
> must be at the root level."

Note the scope rule: it does **not** propagate through `$ref`; a referenced schema declares its own.

`$id` declares the **identity / base URI**:

> "You can set the base URI by using the `$id` keyword at the root of the schema."

with a strong preference for absolute URIs — "it's recommended that you always use an absolute URI
when declaring a base URI with `$id`" — and, crucially, no requirement that it resolve:

> "Even though schemas are identified by URIs, those identifiers are not necessarily
> network-addressable. They are just identifiers."

That last sentence is the permission slip for `"$schema": "https://seahaven.example/state/v1"` with
nothing served at that URL. The identifier's job is to be *stable and unique*, not fetchable. (I did
not find explicit JSON-Schema-org guidance on how to version the `$id` itself — the structuring page
does not cover it. Unverified beyond the absence.)

Note the split of roles: `$schema` says "which rules govern this document's *shape language*",
`$id` says "which document this is". A state format needs the second and, arguably, both.

---

## OpenTelemetry — `schema_url` plus schema files

Sources: [`specification/schemas/README.md`](https://raw.githubusercontent.com/open-telemetry/opentelemetry-specification/main/specification/schemas/README.md),
[`specification/schemas/file_format_v1.1.0.md`](https://raw.githubusercontent.com/open-telemetry/opentelemetry-specification/main/specification/schemas/file_format_v1.1.0.md),
[`specification/versioning-and-stability.md`](https://raw.githubusercontent.com/open-telemetry/opentelemetry-specification/main/specification/versioning-and-stability.md).
(opentelemetry.io itself is blocked; these are the sources the site renders.)

This is the most complete published "version field plus formatter" design I found, and it is worth
reading in full if you are choosing one.

**The field.** Verbatim:

> "Schema URL is an identifier of a Schema. The URL specifies a location of a Schema File that can be
> retrieved (so it is a URL and not just a URI) using HTTP or HTTPS protocol."
>
> "The last part of the URL path is the version number of the schema.
> ```
> http[s]://server[:port]/path/<version>
> ```
> The part of the URL preceding the `<version>` is called Schema Family identifier. All schemas in one
> Schema Family have identical Schema Family identifiers."

**The immutability rule** — this is the load-bearing one:

> "To create a new version of the schema copy the schema file for the last version in the schema
> family and add the definition of the new version. The schema file that corresponds to the new
> version MUST be retrievable at a new URL.
>
> Important: schema files are immutable once they are published. Once the schema file is retrieved it
> is recommended to be cached permanently. Schema files may be also packaged at build time with the
> software that anticipates it may need the schema."

Note that OTel took the opposite of JSON Schema's stance: here the URL really must resolve, because
a *machine* fetches it to perform translation.

**Where it is attached.** `schema_url` lives on `ResourceSpans` / `ResourceMetrics` / `ResourceLogs`
and again on the instrumentation-scope message, with a precedence rule: "If schema_url field is
non-empty both in Resource* message and in the contained InstrumentationLibrary* message then the
value in InstrumentationLibrary* message takes the precedence." Two-level versioning again — an
envelope version and a per-component version — with an explicit tie-break.

**The formatter.** The schema *file* is YAML with a `file_format` field (itself versioned — "1.1.0"),
a `schema_url`, and a `versions:` map keyed by semconv version. Each version entry lists changes in
up to six sections (`all`, `resources`, `spans`, `span_events`, `metrics`, `logs`) with typed
transformations — `rename_attributes`, `rename_metrics`, `rename_events`, `split`:

```yaml
metrics:
  changes:
    - rename_metrics:
        container.cpu.usage.total: cpu.usage.total
        container.memory.usage.max: memory.usage.max
```

Application is a **chain over a version range**: "when converting from older version X to newer
version Y … the transformations specified in each version in the range [X..Y] are applied one by
one", with a defined intra-version section order, and the whole thing reversed (each transformation
performing its backward conversion) to go the other way.

**Why they bothered.** The stability doc ties the two together: semantic-convention stability means
"Changes must be describable via schema files, allowing renaming of spans, metrics, and attributes
without breaking consumers." I.e. *the existence of the formatter is what lets the schema evolve at
all while claiming stability*. And the surrounding guarantees are strong and dated:
"Backwards compatibility is a strict requirement"; "It MUST always be possible to upgrade to the
latest minor version of the OpenTelemetry SDK, without creating compilation or runtime errors";
API support "minimum three years after the next major version release".

**The honest caveat:** OTel schema files are a beautiful design with thin adoption — the translation
is implemented in the Collector's schema processor and little else, and in practice most consumers
just hard-code the convention version they know. I could not verify current adoption numbers from
reachable sources; treat "everyone uses this" as unsupported.

---

## Stripe — pinned API versions and version-change modules

⚠️ **Source quality caveat**: stripe.com, docs.stripe.com and medium.com are all blocked by this
session's egress proxy. Everything below is from search-result summaries of Stripe's own blog post
"APIs as infrastructure: future-proofing Stripe with versioning" and docs.stripe.com's versioning
pages, not from text I fetched. Treat the mechanism description as second-hand.

The model:

- Each account is **pinned** to the API version current when it was created, and stays there
  indefinitely. Versions are **dates** (`2024-10-01`), not semver.
- Resolution order for a given request: an explicit `Stripe-Version` header, else the version of the
  authorizing OAuth app, else the account's pinned version.
- Internally, Stripe implements the newest version only, then **transforms responses backwards**: a
  chain of small "version change" modules, each owning one backwards-incompatible change, applied in
  reverse chronological order until the response matches the caller's pinned version. Gates sort
  ascending by version, transformers descending. Changes that cannot be expressed as a pure response
  transformation are annotated `has_side_effects` and their transformation becomes a no-op — and
  Stripe reportedly tries to avoid those.

**The transferable idea** is not the date format; it is that the *producer* holds exactly one
implementation (the newest) and pays for compatibility with a chain of downgraders, rather than
maintaining N parallel code paths. It is the exact dual of event-sourcing upcasters (below), which
put the chain on the *reader* side and upgrade old→new. Which side you put the chain on depends on
who is more numerous and who you can force to update.

For a saved-artifact format like a state payload, the reader-side (upcaster) direction is the right
one: documents written years ago are immutable and numerous, readers are few and current.

---

## Avro — the writer's schema travels with the data

Source: [Avro specification, `Schema Resolution`](https://raw.githubusercontent.com/apache/avro/main/doc/content/en/docs/%2B%2Bversion%2B%2B/Specification/_index.md).

> "A reader of Avro data, whether from an RPC or a file, can always parse that data because the
> original schema must be provided along with the data. However, the reader may be programmed to read
> data into a different schema."

That is the whole thesis: there are always **two** schemas, the writer's and the reader's, and
compatibility is a *resolution algorithm* between them rather than a property of one schema. The
record rules, verbatim:

> - "the ordering of fields may be different: fields are matched by name."
> - "if the writer's record contains a field with a name not present in the reader's record, the
>   writer's value for that field is ignored."
> - "if the reader's record schema has a field that contains a default value, and writer's schema
>   does not have a field with the same name, then the reader should use the default value from its
>   field."
> - "if the reader's record schema has a field with no default value, and writer's schema does not
>   have a field with the same name, an error is signalled."

Plus type promotion (int→long/float/double, long→float/double, float→double, string↔bytes) and
enum-with-default handling.

Avro also defines **Parsing Canonical Form** and fingerprints: strip `doc`/`aliases`, expand names to
fullnames, order object keys `name, type, fields, symbols, items, values, size`, normalise string
escapes — then hash. "If the Parsing Canonical Forms of two different schemas are textually equal,
then those schemas are 'the same' as far as any reader is concerned." That is a directly stealable
trick for identifying a state-format version by content rather than by a hand-maintained number.

**Confluent Schema Registry** puts policy on top of resolution — compatibility modes `BACKWARD`
(default), `BACKWARD_TRANSITIVE`, `FORWARD`, `FORWARD_TRANSITIVE`, `FULL`, `FULL_TRANSITIVE`, `NONE`,
where BACKWARD = "new schema can read data produced by the latest registered schema" and the
`_TRANSITIVE` variants check against *all* prior versions rather than only the latest. (docs.confluent.io
is blocked; this is from search-surfaced doc text — second-hand.) The vocabulary is useful even if
you never run a registry: "backward compatible" is ambiguous until you say *transitive or not*, and
for an archive that is re-read years later you want the transitive one.

---

## Protobuf — field numbers are the contract

Source: [`content/programming-guides/proto3.md`](https://raw.githubusercontent.com/protocolbuffers/protocolbuffers.github.io/main/content/programming-guides/proto3.md),
"Updating A Message Type". Verbatim highlights:

Wire-**unsafe**:
> "Changing field numbers for any existing field is not safe. Changing the field number is equivalent
> to deleting the field and adding a new field with the same type."
> "Moving fields into an existing `oneof` is not safe."

Wire-**safe**:
> "Adding new fields is safe. … old binaries simply ignore the new field when parsing."
> "Removing fields is safe. The same field number must not used again in your updated message type.
> You may want to rename the field instead, perhaps adding the prefix "OBSOLETE_", or make the field
> number reserved, so that future users of your `.proto` can't accidentally reuse the number."
> "Adding additional values to an enum is safe."

Wire-**compatible but lossy** (their term: "conditionally safe"): e.g. int32→int64 parses fine but
"if a value larger than INT32_MAX is written, a client that reads it as an int32 will discard the
high order bits".

And an important scoping note the doc puts up front: **the rules are different for the JSON
encoding.** "If you use ProtoJSON or proto text format to store your protocol buffer messages, the
changes that you can make in your proto definition are different." Protobuf's famous
add/remove-freely story is a property of the *binary* wire format's tag numbers; a JSON document has
no tag numbers, so its compatibility story is closer to Avro's name-matching — which is what a JSON
state payload will actually be subject to.

---

## Google AIP-180 — the prose version of the same rules

Source: [`aip/general/0180.md`](https://raw.githubusercontent.com/aip-dev/google.aip.dev/master/aip/general/0180.md).
Useful because it states the rules for *any* API surface, not a particular encoding, and because it
names the failure mode people actually hit — semantic drift, not shape drift.

> "New required fields must not be added to existing request messages or resources."
> "Any field being populated by clients must have a default behavior matching the behavior before the
> field was introduced."
> "Any field previously populated by the server must continue to be populated, even if it introduces
> redundancy."
> "Existing components (interfaces, methods, messages, fields, enums, or enum values) must not be
> removed from existing APIs in the same major version."
> "Changing the default value is considered breaking and must not be done."
> "APIs must not change the expected format or algorithm used to construct the value of an existing
> field—even if OUTPUT_ONLY—within an API version."

That last one is the sleeper. For a state format it reads: *once a judge can parse a `key` dict or a
timestamp string a particular way, you cannot change how you build it inside the same version* — even
though you never changed the type.

AIP-180 also splits compatibility into three kinds — **source** (does old code still compile),
**wire** (does old code still parse), **semantic** (does old code still *mean the same thing*) — which
is a better checklist than "backwards compatible" for reasoning about a document nobody will
recompile.

---

## "Version field plus formatter" in event sourcing: upcasters

Source: [Marten, `docs/events/versioning.md`](https://raw.githubusercontent.com/JasperFx/marten/master/docs/events/versioning.md).
(The Axon, event-driven.io and Greg Young sources that come up in search are all on blocked hosts;
Marten's doc is the reachable primary source and it cites the others.)

The problem statement is exactly the state-format problem, restated:

> "Events, by their nature, represent facts that happened in the past. They should be immutable even
> if they had wrong or missing values."

The mechanism, verbatim:

> "Upcasting is a process of transforming the old JSON schema into the new one. It's performed on the
> fly each time the event is read. You can think of it as a pluggable middleware between the
> deserialization and application logic. Having that, we can either grab raw JSON or a deserialized
> object of the old CLR type and transform them into the new schema. Thanks to that, we can keep only
> the last version of the event schema in our stream aggregation or projection handling."

Two implementation styles, both documented: transform **typed old object → typed new object** (keep
the old class around), or transform **raw JSON → new object** (don't). The doc argues for pure
functions: "As upcasting is a process that takes the old event payload and returns the new one, we
could think of them as pure functions without side effects. That makes them also easy to test with
unit or contract tests."

Marten also records *two* identity columns per event — a mapped `type` string plus the CLR
assembly-qualified name in `mt_dotnet_type` — so renaming or moving the class does not orphan the
data. The generalisation: **store a stable logical type name alongside whatever your language calls
the type.**

The doc's strategy advice is worth quoting because it is the part people skip:

> "The best strategy is not to change the past data but compensate our mishaps."

and the tiering of data by access pattern — hot (active business logic), warm (read-only / UI), cold
(archival/legal) — with the observation that you only need to maintain upcasters for schemas whose
data is still *hot*. For an eval archive, almost all saved state is warm-to-cold, which is an
argument for keeping the formatter small and accepting that very old payloads may be read only by an
explicitly-versioned legacy path.

---

## Synthesis: what a durable structured output carries

Collating the seven, the recurring elements are:

| element | who does it | why |
|---|---|---|
| A required version field, at the root, first | CloudEvents `specversion`, JSON Schema `$schema`, OTel `schema_url` | lets a reader dispatch before parsing anything else |
| A stable *identifier* separate from the version | JSON Schema `$id`, OTel Schema Family, Marten's `type` string | says *which* document this is, independent of which revision |
| A documented rule for which changes are invisible | CloudEvents (patch invisible), Protobuf (add/remove-by-number), AIP-180 (additive only) | stops the version field from churning on every commit |
| Two-level versioning with a precedence rule | OTel (resource vs scope), CloudEvents (`specversion` vs `type`) | the envelope and the payload evolve at different rates |
| A transformation chain, one step per version | OTel schema files, Stripe version changes, upcasters | the escape hatch when additive-only isn't enough |
| Immutability of published versions | OTel ("schema files are immutable once they are published") | otherwise the version number means nothing |
| Content-derived identity | Avro Parsing Canonical Form + fingerprint | version numbers people maintain by hand drift; hashes don't |

And the two things nobody does:
- **Nobody versions per-field.** The version is on the document, once.
- **Nobody omits the version to save bytes.** Even Avro, which is obsessive about size, ships the
  writer's schema (or a registry id) with every payload.
