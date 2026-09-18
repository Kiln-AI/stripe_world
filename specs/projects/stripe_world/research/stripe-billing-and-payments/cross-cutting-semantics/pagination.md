# Pagination (list endpoints) and Search (separate cursor mechanism)

There are **two distinct pagination mechanisms** in the API, confirmed from `spec3.json`, and they
must not be conflated:

1. **List endpoints** (`GET /v1/customers`, `GET /v1/charges`, etc.) — `starting_after` /
   `ending_before` / `has_more`, no `total_count`.
2. **Search endpoints** (`GET /v1/customers/search`, `GET /v1/charges/search`, etc.) — `page` /
   `has_more` / `total_count` (capped), no `starting_after`/`ending_before`.

## 1. List pagination

### Parameters (verbatim from `spec3.json`, `/v1/customers` GET — identical shape reused across every
listable resource via the generator)

```json
{
  "name": "limit",
  "description": "A limit on the number of objects to be returned. Limit can range between 1 and 100, and the default is 10.",
  "schema": { "type": "integer" }
},
{
  "name": "starting_after",
  "description": "A cursor for use in pagination. `starting_after` is an object ID that defines your place in the list. For instance, if you make a list request and receive 100 objects, ending with `obj_foo`, your subsequent call can include `starting_after=obj_foo` in order to fetch the next page of the list.",
  "schema": { "type": "string", "maxLength": 5000 }
},
{
  "name": "ending_before",
  "description": "A cursor for use in pagination. `ending_before` is an object ID that defines your place in the list. For instance, if you make a list request and receive 100 objects, starting with `obj_bar`, your subsequent call can include `ending_before=obj_bar` in order to fetch the previous page of the list.",
  "schema": { "type": "string", "maxLength": 5000 }
}
```

- **Default limit: 10. Range: 1–100.** This is identical across every list endpoint I sampled (the
  spec generator stamps the same description onto every resource's `limit` param).
- **Ordering guarantee**: reverse-chronological by object creation (implicit in "your subsequent call
  can include `starting_after=obj_foo`" to get objects *after* — i.e. newer than — the last one
  returned in a page that itself came back newest-first... actually Stripe's own framing is
  "starting_after ... to fetch the **next** page," "ending_before ... to fetch the **previous**
  page" — the precise semantic is: object IDs are (for essentially all core resources) issued in
  increasing/roughly-time-ordered fashion, and list results are returned **newest-first** by
  default. I could not re-fetch docs.stripe.com/api/pagination directly this session to quote its
  ordering-guarantee sentence verbatim; treat the "newest first" framing as corroborated-but-not-
  independently-reverified (see Gaps).
- **`starting_after` and `ending_before` are mutually exclusive.** Sending both produces an error;
  the reported message text (via WebSearch synthesis, not independently re-fetched from Stripe) is:
  > "Received both starting_after and ending_before parameters. Please pass in only one."
- Cursors are plain **object IDs**, not opaque tokens — `maxLength: 5000`, `type: string`, no format
  constraint in the schema itself.

### List response envelope (verbatim from `spec3.json`, `/v1/customers` GET 200 response —
`CustomerResourceCustomerList`, and this exact shape — `object`/`data`/`has_more`/`url` with no
`total_count` — recurs for every plain list endpoint)

```json
{
  "data": { "type": "array", "items": { "$ref": "#/components/schemas/customer" } },
  "has_more": {
    "description": "True if this list has another page of items after this one that can be fetched.",
    "type": "boolean"
  },
  "object": { "enum": ["list"], "type": "string" },
  "url": {
    "description": "The URL where this list can be accessed.",
    "type": "string", "pattern": "^/v1/customers"
  }
}
```
Required: `data`, `has_more`, `object`, `url`. **No `total_count` field exists on ordinary list
responses** — confirmed by grepping all 7 occurrences of `total_count` in `spec3.json`: every one is
inside a **search**-result schema, none inside a plain list schema. (This matches Stripe's public
history: `total_count` on list endpoints was a legacy/deprecated `include[]=total_count` opt-in in
very old API versions and is not present at all in the current `2026-08-26.dahlia` schema.)

### `has_more` at a page boundary / on the last page
- Exactly-full page that happens to be the last one: the schema gives no special-case; behaviorally
  (per general REST-cursor-pagination convention plus the description text "there's another page
  *after this one* that can be fetched") `has_more` reflects whether a next page exists, independent
  of whether the current page is "full" (`== limit`) — a full page is not proof `has_more` is true,
  and a short/empty page is not proof it's false only in the trivial case (an empty page always
  implies `has_more: false` unless something is deeply wrong). I did not find an authoritative
  docs.stripe.com quote pinning down the exact boundary semantics beyond the schema description
  itself this session — flagged as a gap.
- **Client behavior for the empty-page terminal case** is explicit in `stripe-python`
  (`_list_object.py`, `ListObject._auto_paging_iter`):
  ```python
  def _auto_paging_iter(self) -> Iterator[T]:
      page = self
      while True:
          ...
          page = page.next_page()
          if page.is_empty:
              break
  ```
  and `next_page()` **short-circuits to a locally-constructed empty list object without an HTTP call
  at all** when `self.has_more` is falsy:
  ```python
  def next_page(self, **params):
      if not self.has_more:
          ...
          return self._empty_list(**request_options)
      return self.list(**self._get_filters_for_next_page(params))
  ```
  So the client trusts `has_more` as the sole stopping signal and never issues a request that "comes
  back empty" in the ordinary auto-pagination path; the `is_empty` check exists as a belt-and-braces
  terminal condition after that short-circuit.

### `starting_after` / `ending_before` exactness — exclusive cursor, confirmed by client construction
`stripe-python`'s `_get_filters_for_next_page` builds the next request as:
```python
last_id = getattr(self.data[-1], "id")
params_with_filters.update({"starting_after": last_id})
```
i.e. it takes the **last item's own id** of the current page and passes it as `starting_after` for
the next call — which only makes sense if `starting_after` is **exclusive** (returns items strictly
after that id; otherwise the client would receive a duplicate of the last item forever). Symmetrically
`_get_filters_for_previous_page` uses `self.data[0].id` as `ending_before`. This is strong indirect
confirmation of exclusivity from the reference client's own pagination algorithm, though I did not
independently re-fetch Stripe's own "exclusive" wording from docs.stripe.com this session.

### `starting_after` on a deleted object id
**Not conclusively confirmed either way from a source I could fetch this session** — flagged
explicitly as a gap rather than guessed. What I *did* find, from a real `stripe-node` GitHub issue
(`stripe/stripe-node#2368`): when a caller was auto-paginating over a **filtered** list (active
subscriptions) and the object at the current page boundary changed state such that it no longer
matched the filter (canceled mid-pagination), the next-page request threw `resource_missing`. That's
a related-but-distinct scenario (state change interacting with a *filter*, not the cursor itself
being an id that no longer exists at all) and should not be read as proof of behavior for a plain
`GET /v1/customers?starting_after=<a_genuinely_deleted_customer_id>` with no other filters. Cursor
pagination is conventionally implemented against an immutable ordering key (creation order / id),
which would suggest a deleted-but-previously-existing id should still work as a positional marker —
but this is my inference from general cursor-pagination design, not a confirmed Stripe behavior.
**Recommend testing this against the live API (test mode) before committing to emulation semantics.**

### Auto-pagination in the clients
Fully covered above via `_list_object.py`. Summary: `auto_paging_iter()` wraps a generator that
walks forward (`next_page()`, keyed on `starting_after=<last id>`) or backward (`previous_page()`,
keyed on `ending_before=<first id>`, chosen when the *original* call was constructed with
`ending_before` set and `starting_after` unset — see `_auto_paging_iter`'s branch on
`self._retrieve_params`), stopping when a page comes back with `has_more: false` (short-circuited
locally to an empty page, no extra HTTP call) or, defensively, when a page's `data` is empty.

## 2. Search pagination (separate mechanism — flagged as related but out of the list-pagination model)

Endpoints: `/v1/charges/search`, `/v1/customers/search`, `/v1/invoices/search`,
`/v1/payment_intents/search`, `/v1/prices/search`, `/v1/products/search`,
`/v1/subscriptions/search` (full list from `spec3.json` paths containing `search`).

Parameters (verbatim, `/v1/charges/search` GET):
```json
{
  "name": "page",
  "description": "A cursor for pagination across multiple pages of results. Don't include this parameter on the first call. Use the next_page value returned in a previous response to request subsequent results.",
  "schema": { "type": "string", "maxLength": 5000 }
},
{
  "name": "query",
  "required": true,
  "description": "The search query string. See [search query language](https://docs.stripe.com/search#search-query-language) ...",
  "schema": { "type": "string", "maxLength": 5000 }
}
```
(plus the same `expand` and `limit` params as list endpoints — 1–100, default 10).

Response envelope (verbatim, search-result schema embedded in `/v1/charges/search`'s 200 response):
```json
{
  "data": { "type": "array", "items": { "$ref": "#/components/schemas/charge" } },
  "has_more": { "type": "boolean" },
  "next_page": { "type": "string", "nullable": true, "maxLength": 5000 },
  "object": { "enum": ["search_result"], "type": "string" },
  "total_count": {
    "description": "The total number of objects that match the query, only accurate up to 10,000.",
    "type": "integer"
  },
  "url": { "type": "string", "maxLength": 5000 }
}
```
Key differences from list pagination, all directly from the schema:
- Cursor is an **opaque `page` token** (server-issued `next_page` value), not an object id — you do
  not construct it yourself the way you do `starting_after`.
- `total_count` **does** exist here, explicitly documented as "only accurate up to 10,000" — i.e. it
  is a capped/approximate count, not an exact count for large result sets.
- `next_page` is only meaningful when `has_more` is true; `stripe-mock`'s generator explicitly skips
  emitting `next_page` when `has_more` is false (see `errors-and-mock-notes.md` / stripe-mock section
  below), which is consistent with treating `next_page: null`/absent as "no more pages."

## Sources
- `research/stripe-openapi/spec3.json` — authoritative for every verbatim schema/parameter quote
  above (queried directly with Python, not recalled).
- `research/repos/stripe-python/stripe/_list_object.py` — authoritative for client-observed
  auto-pagination algorithm.
- WebSearch synthesis of docs.stripe.com/api/pagination and docs.stripe.com/pagination (direct
  WebFetch to docs.stripe.com was blocked by this session's egress policy — see summary.md Gaps).
- `stripe/stripe-node#2368` (GitHub issue) — real-world edge case on filtered auto-pagination +
  state change.
