---
name: browser-automation
description: >-
  Use when designing, reviewing, or repairing deterministic browser automations
  built on UKLOK Browser + Browser Use. On runtime failure, start from the
  emitted failure.json and the exact failed checkpoint instead of reloading the
  whole browser system.
metadata:
  title: Browser automation
---

# Browser automation

Normal automation execution is deterministic. **Do not put an agent in the happy path.**

The contract owns intention. The module reflects it. The agent appears only when a checkpoint fails, a browser/site behavior changed, or a human explicitly asks to design/refine an automation.

## Core law

~~~text
contract.toml
  owns intent + authority + pseudocode + checkpoint order
        ↓
run.py
  implements exactly step_<id>() for each algorithm step
        ↓
browser-automation runner
  executes contract order through Browser Use
        ↓
failure.json
  exists only when deterministic behavior cannot satisfy the contract
~~~

Do not hide new intent, fallback logic, or extra steps only in Python.

## Fast entry on failure

When given a `.runs/.../failure.json`, do not begin by reading the whole repo.

Read, in this order:

1. the failure file;
2. the referenced `steps/<ordinal>-<id>/checkpoint.json` and sibling evidence;
3. the referenced failed `[[algorithm]]` step in `contract.toml`;
4. the referenced `step_<id>()` function in `run.py`.

The directory is an index: do not scan sibling checkpoints unless the failed evidence proves the dependency crosses them.

That is the default repair context.

Only then:

- load the generated `.agents/skills/browser-use/SKILL.md` if live browser exploration is required;
- load `browser-runtime` only when Chrome/CDP/noVNC/profile health is actually the failing substrate;
- widen to adjacent steps only if evidence proves the failed checkpoint depends on them.

## Classify before changing

A failed checkpoint is usually one of four things:

- **substrate failure** — Chrome/CDP/profile/runtime is unhealthy;
- **behavior change** — the site/page no longer behaves as the contract describes;
- **implementation drift** — the module no longer faithfully implements the contract;
- **authority/human boundary** — the next action is outside the contract's pre-authorized behavior;
- **observability degradation** — the functional checkpoint may be valid while optional traffic evidence is `timeout_busy`, `unavailable`, stale, or otherwise incomplete.

Do not “fix” a substrate failure with selector logic or an authority boundary with a silent workaround. Do not turn an observability degradation into a business failure unless the contract's actual outcome depends on that network fact.

## Repair loop

1. Reproduce or inspect the failed checkpoint with `./bin/browser-use`; preserve the human's current browser state.
2. Compare **expected** vs **observed** from `failure.json`. If it references traffic evidence, read that checkpoint's `from_seq..through_seq` boundary first and inspect only that slice of `traffic/events.jsonl` before broad browser exploration. Treat stale/persistent carry-over separately from traffic initiated inside the failed step.
3. If browser/site behavior changed, update the failed contract step first:
   - `pseudocode`;
   - `checkpoint`;
   - `known_variants`;
   - `resume`, when recovery semantics changed.
4. Make `step_<id>()` reflect the updated contract. Avoid unrelated refactors.
5. Run:
   ~~~bash
   ./bin/browser-automation validate <automation>
   ~~~
6. If `failure.json` says `resume = "safe"`, verify from the failed checkpoint:
   ~~~bash
   ./bin/browser-automation run <automation> --from <checkpoint> ...
   ~~~
   Otherwise restart the automation from its beginning.
7. Prove the final `[success].condition`, not merely that Python exited zero.

The repair should leave a **new reusable behavior**, not a diary of the incident. Runtime `.runs/` evidence stays local and gitignored.

## Creating an automation

Start with:

~~~bash
./bin/browser-automation new <automation_id>
~~~

Then work contract-first:

1. state one observable `intent`;
2. define the external `trigger`;
3. bound `authority.allowed` and `authority.forbidden`;
4. define inputs and preconditions;
5. write the algorithm as small pseudocode steps;
6. give every step an observable checkpoint and explicit failure behavior;
7. mark `resume = "safe"` only when the step can really be re-entered from existing browser state;
8. set `[success].condition`;
9. implement one synchronous `step_<id>(ctx)` for every contract step;
10. set `status = "ready"` only after validation and a real browser proof.

Use the Browser Use skill for interaction mechanics instead of copying those mechanics into every automation contract.

## Step implementation contract

Each `step_<id>(ctx)`:

- may use Browser Use helpers already provided by the CLI execution namespace;
- receives `ctx["inputs"]`, current contract metadata and the current step;
- receives `ctx["run_dir"]` / `ctx["step_dir"]` as the current checkpoint workspace and `ctx["run_root"]` only for genuinely cross-step artifacts;
- may call `artifact_path(name)` to obtain a traversal-safe step-local path;
- returns a mapping with `ok: true` only when the contract checkpoint is observably satisfied;
- returns `observed` and optional `evidence` useful to a future repair;
- raises on unexpected execution errors;
- must not implement another contract step invisibly.

A checkpoint failure stops later steps.

The runtime owns evidence placement. `capture_screenshot()` is normalized into the active step directory even when the caller supplies an absolute filename. Keep checkpoint-specific JSON/images beside that checkpoint; do not make each automation invent another run-layout convention.

When `--capture-traffic` is active, the shared runner owns the network transition after the step returns. Do **not** add generic sleeps or duplicate "network idle" loops inside `run.py`. The runner waits for a quiet window and records a traffic boundary before the next step.

Traffic settling is a second evidence dimension, not the business checkpoint itself. `loadingFailed`, HTTP >= 400, stale requests, persistent streams, `timeout_busy`, or an unavailable recorder can be highly relevant evidence without automatically making `ok: true` false. A request that already exhausted the busy budget becomes carry-over on subsequent checkpoints instead of repeatedly charging the same wait.

## Inputs and secrets

Inputs may be marked `sensitive = true`.

The runtime passes their real values to the module but records `<redacted>` in durable run/failure metadata. Prefer the authenticated Chrome profile over passing credentials as automation inputs.

`--capture-traffic` is diagnostic evidence, not a default. Its `traffic/events.jsonl` can contain headers, cookies and request post data; keep it local under `.runs/` and enable it only when network behavior materially helps proof or repair. `traffic/checkpoints.jsonl` is the index that maps each automation checkpoint to an exact raw sequence range plus quiet/stale/persistent/busy classification.

## One interactive lane

The current persistent Chrome identity is one interactive lane.

`browser-automation` serializes runs with a local lock. Do not bypass that lock with parallel direct Browser Use controllers unless the architecture intentionally provisions separate browser identities.

## Proof

A repaired/created automation is complete when:

- `./bin/browser-automation validate <id>` passes;
- contract steps and `step_<id>()` functions are exactly aligned;
- a real run reaches `[success].condition`;
- no hidden agent/model call exists in the normal execution path;
- any learned site behavior is discoverable in the contract, not only the Python module or chat history;
- run evidence is recoverable by checkpoint from the directory structure without reconstructing filename correlations.
