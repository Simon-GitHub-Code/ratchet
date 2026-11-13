# Acceptance records and kept checks

This is the reference for the files an acceptance tester writes and the acceptance harness reads
and writes (ADR 0035). `scripts/acceptance_record.py` reads the record and writes the summary.
`scripts/kept_checks.py` reads the kept checks. A test holds each table below to the fields the
code knows, in the same order. A field the code does not know is refused with a sentence that
names it, never ignored, so a misspelt field cannot quietly weaken a record or a check.

"Required" means the field must be there and not empty. "For `pass`" and the like mean the field
is required for that verdict or severity and optional otherwise.

## The record

`tester/record.json` in the session's directory. One record per session.

| Field | Type | Required | What it holds |
|---|---|---|---|
| `schema` | number | yes | `1`, the version of this file the harness reads. |
| `session` | text | yes | The id of the session `up` began, from `session.json`. |
| `tree` | text | yes | The tree the session served, from `session.json`. |
| `author` | text | no | `tester`, the default, or `harness`. The harness writes the record itself when the product cannot start. |
| `criteria` | list of criteria | yes | One criterion for each checklist line of the ticket. At least one must be something other than `not-observable`. |
| `findings` | list of findings | no | What the tester found beyond the checklist. |
| `kept` | list of kept checks | no | The checks that keep a `pass` true on later changes. |
| `notChecked` | list of items | no | What the tester did not try, and why. |
| `tryIt` | list of texts | no | How the owner can try the change. |

## A criterion

An item of the record's `criteria`: the verdict on one checklist line.

| Field | Type | Required | What it holds |
|---|---|---|---|
| `id` | text | yes | `C1`, `C2` and so on, each used once. |
| `text` | text | yes | The checklist line, as the ticket has it. |
| `verdict` | text | yes | `pass`, `fail`, `unsure` or `not-observable`. |
| `how` | text | yes | What the tester did, in plain words. |
| `requests` | list of citations | for `pass` | The logged requests the verdict rests on. |
| `screenshots` | list of texts | for `pass` on a `web-ui` | PNG files, as paths under the tester's directory. |
| `steps` | list of texts | for `fail` | How to see the failure again. |
| `why` | text | for `unsure` and `not-observable` | Why use cannot settle the line. |
| `judge` | text | for `not-observable` | Who can judge the line instead, such as code review. |
| `unkept` | text | for `pass` with no kept check | Why no kept check can hold the line. |

## A citation

An item of a criterion's or a finding's `requests`: a request the proxy logged.

| Field | Type | Required | What it holds |
|---|---|---|---|
| `request` | text | yes | The id the proxy gave the request. |
| `method` | text | yes | The method, as the log holds it. |
| `path` | text | yes | The path without its query, as the log holds it. |
| `status` | number | yes | The status, as the log holds it. |
| `contains` | list of texts | no | Texts the stored answer shows. For `pass`, none of them is a text the request itself sent. |

## A finding

An item of the record's `findings`.

| Field | Type | Required | What it holds |
|---|---|---|---|
| `id` | text | yes | `F1`, `F2` and so on, each used once. |
| `severity` | text | yes | `blocker`, `major`, `minor` or `note`. |
| `scope` | text | yes | `this` for this ticket, or `later: <ticket>`. |
| `title` | text | yes | What is wrong, in a line. |
| `steps` | list of texts | yes | How to see it again. |
| `expected` | text | yes | What the owner would expect. |
| `actual` | text | yes | What the product did. |
| `requests` | list of citations | for `blocker` and `major`, this or `screenshots` | The logged requests that show it. |
| `screenshots` | list of texts | for `blocker` and `major`, this or `requests` | PNG files that show it. |

## An item of notChecked

An item of the record's `notChecked`.

| Field | Type | Required | What it holds |
|---|---|---|---|
| `what` | text | yes | What was not tried. |
| `why` | text | yes | Why not. |

## A kept check

An item of the record's `kept`, and of the checks file's `checks`. A kept check is data, never a
program. Every text in it may hold `{BASE_URL}`, the address of the product it runs against,
and `{unique}`, a value new to each run.

| Field | Type | Required | What it holds |
|---|---|---|---|
| `id` | text | yes | `K1`, `K2` and so on, each used once. |
| `criterion` | text | yes | The id of the criterion it keeps, which passed. |
| `via` | text | yes | `http` or `browser`. |
| `width` | number | no | For `browser`, the window's width in pixels, from 200 to 4000. 1280 when absent. |
| `steps` | list of steps | yes | HTTP steps for `http`, browser steps for `browser`. At least one step must expect something. |

## An HTTP step

A step of a kept check that is `via` `http`: one request, which follows no redirect.

| Field | Type | Required | What it holds |
|---|---|---|---|
| `request` | text | yes | `METHOD /path`, a path on `BASE_URL` and never a host. |
| `headers` | object of texts | no | Headers to send. |
| `form` | object of texts | no | A form to send URL-encoded. Not with `body`. |
| `body` | text | no | A body to send as it is. Not with `form`. |
| `expect` | expect | no | What the answer must show. |

## An expect

The `expect` of an HTTP step. A field that is absent asks nothing.

| Field | Type | Required | What it holds |
|---|---|---|---|
| `status` | number | no | The status, from 100 to 599. |
| `headers` | object of texts | no | Exact values. A header's name matches in any case. |
| `contains` | list of texts | no | Texts the body shows, in this order. |
| `lacks` | list of texts | no | Texts the body never shows. |
| `count` | object of numbers | no | How many times the body shows each text. |

## A browser step

A step of a kept check that is `via` `browser`. A step has exactly one of the fields below but
`with`, which goes with `fill`. Each step but `open` waits up to five seconds for what it needs.

| Field | Type | Required | What it holds |
|---|---|---|---|
| `open` | text | one of these | A path on `BASE_URL` to open, never a host. |
| `fill` | text | one of these | The label of the field to fill. |
| `with` | text | for `fill` | The value to fill it with. |
| `click` | text | one of these | The text of the button or link to click. |
| `see` | text | one of these | A text the page shows. |
| `lacks` | text | one of these | A text the page never shows. |
| `true` | text | one of these | A script expression that must be `true`. |

## The summary

`docs/features/<slug>/acceptance.json`, which `summary` writes from an accepted record, the same
bytes on every run. The orchestrator commits it.

| Field | Type | Required | What it holds |
|---|---|---|---|
| `schema` | number | yes | `1`, the version of this file. |
| `slug` | text | yes | The ticket's slug. |
| `tree` | text | yes | The tree that was served and judged. |
| `session` | text | yes | The session's id. |
| `started` | text | yes | When `up` began the session. |
| `ended` | text | yes | When the proxy sealed the log, or `null` when there is no seal. |
| `requests` | number | yes | How many requests the log holds. |
| `log` | text | yes | The SHA-256 of the log, or `null` when there is no seal. |
| `blocking` | list of texts | yes | The ids that keep the change from merging: each `fail`, and each `blocker` or `major` finding for this ticket. |
| `questions` | list of texts | yes | The ids the owner decides: each `unsure`, and each `blocker` finding for a later ticket. |
| `notObservable` | number | yes | How many criteria no use of the product could judge. |
| `criteria` | list | yes | Each criterion's `id`, `text`, `verdict` and `how`, and its `why` and `judge` when it has them. |
| `findings` | list | yes | Each finding, less what it cites. |
| `kept` | number | yes | How many kept checks the record has. |
| `unkept` | list | yes | Each `pass` with no kept check: its `criterion` and `why`. |
| `replay` | object | yes | The last replay of these very checks: how many `held`, went `unmet` and were `notRun`, `of` how many. `null` when they were not replayed. |
| `control` | object | yes | The same against a product that does nothing, where each should go `unmet`. |
| `notChecked` | list | yes | The record's `notChecked`. |
| `tryIt` | list of texts | yes | The record's `tryIt`. |

## The checks file

`docs/features/<slug>/acceptance-checks.json`, which `summary` writes beside the summary when the
record keeps any check. `replay --checks` reads it.

| Field | Type | Required | What it holds |
|---|---|---|---|
| `schema` | number | yes | `1`, the version of this file. |
| `slug` | text | yes | The ticket's slug. |
| `checks` | list of kept checks | yes | The record's kept checks, as the tester wrote them. |
