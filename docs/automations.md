# Browser automations

Browser Use gives UKLOK powerful browser primitives. The automation layer turns repeatedly learned browser behavior into **small deterministic programs** so an agent is not required for every run.

## Model

~~~text
scheduler / human
      │
      ▼
browser-automation run
      │
      ├── reads contract.toml
      │      intent
      │      authority
      │      pseudocode
      │      checkpoints
      │      resume semantics
      │
      ├── executes run.py step_<id>() functions
      │      in contract order
      │
      ▼
Browser Use CLI ──► UKLOK Chrome/CDP

checkpoint passes ──► continue deterministically

checkpoint fails
      │
      ▼
.runs/.../failure.json
      │
      ▼
agent loads browser-automation skill
      │
      ├── starts at exact failed checkpoint
      ├── explores only when needed
      ├── updates contract behavior first
      └── updates the matching module step
~~~

The contract is the knowledge layer. Python is not allowed to become a second description of what the automation means.

## Why this shape

A browser agent is excellent at exploration and adaptation but expensive and unnecessarily variable for behavior we already understand.

Once a path is learned:

1. describe the intention as pseudocode;
2. name observable checkpoints;
3. encode only those steps deterministically;
4. run without an agent;
5. invoke an agent only when reality no longer satisfies a checkpoint;
6. preserve the newly learned behavior in the contract.

Repeated work therefore trends from agent-heavy exploration toward cheap deterministic execution.

## Create an automation

After `./bin/browser-use-setup`:

~~~bash
./bin/browser-automation new upwork_example
~~~

This creates:

~~~text
automations/upwork_example/
├── contract.toml
└── run.py
~~~

Write `contract.toml` **before** implementation.

### Contract shape

Every contract uses:

~~~toml
schema = "uklok.browser.automation/v1"
id = "upwork_example"
version = 1
status = "draft"
intent = "One observable outcome."
module = "run.py"

[trigger]
description = "When this should execute."

[authority]
allowed = ["Actions already authorized for unattended execution."]
forbidden = ["Actions this automation must never take silently."]

[[inputs]]
name = "example"
type = "string"
required = true
sensitive = false
description = "Meaning of this input."

[[preconditions]]
id = "session_ready"
condition = "Required browser identity/session already exists."

[[algorithm]]
id = "find_target"
pseudocode = """
OPEN the relevant page.
LOCATE the target using stable page state.
STOP if the state cannot be established.
"""
checkpoint = "The target is observably present."
on_failure = "Stop before any later action."
resume = "safe"
known_variants = []

[success]
condition = "Observable final state."

[failure]
capture_screenshot = true
agent_skill = ".agents/skills/browser-automation/SKILL.md"
~~~

### Implementation shape

Each algorithm step has exactly one synchronous implementation:

~~~python
def step_find_target(ctx):
    observed = js("document.title")
    return {
        "ok": bool(observed),
        "observed": {"title": observed},
    }
~~~

The runner validates that contract step IDs and Python `step_<id>()` functions match exactly. Extra hidden steps or missing implementations fail validation.

## Validate

~~~bash
./bin/browser-automation validate
./bin/browser-automation validate upwork_example
~~~

Validation checks contract shape, module syntax and exact step/function alignment.

A `draft` contract can validate, but only `status = "ready"` automations may run.

## Run

~~~bash
./bin/browser-automation run example_read_page
~~~

Inputs are explicit:

~~~bash
./bin/browser-automation run example_read_page \
  --input url=https://example.com
~~~

The runner acquires the single shared browser-lane lock, then feeds the deterministic module through the repo-local Browser Use CLI connected to the existing UKLOK Chrome.

No LLM/agent is loaded.

### Run evidence layout

The run root is an **index**, not a flat artifact bucket:

~~~text
.runs/<automation>/<run-id>/
├── run.json
├── state.json
├── events.ndjson
├── result.json | failure.json
├── steps/
│   ├── 01-<step-id>/
│   │   ├── checkpoint.json
│   │   └── <step evidence>
│   └── 02-<step-id>/
│       ├── checkpoint.json
│       └── <step evidence>
└── traffic/
    ├── events.jsonl
    ├── checkpoints.jsonl
    ├── state.json
    └── capture.log
~~~

The directory tree itself is part of the repair interface:

- root files describe the whole run;
- `steps/<ordinal>-<id>/` contains evidence whose meaning belongs to that contract checkpoint;
- `traffic/` contains network evidence whose lifecycle spans checkpoints.

During a step:

- `ctx["run_dir"]` and `ctx["step_dir"]` both name the current step workspace;
- `ctx["run_root"]` names the whole run and should be used only for deliberately cross-step artifacts;
- `artifact_path("name")` returns a safe path inside the current step and rejects traversal;
- `capture_screenshot()` is wrapped by the runtime so omitted, relative, or absolute screenshot names are normalized into the current step directory.

This makes placement an owner-level behavior rather than a convention every automation must remember. If a piece of evidence exists because of one checkpoint, write it through the step workspace. Do not spray per-step JSON/images into the run root.

Each step directory always contains `checkpoint.json`, even on failure. It records the functional status, expected checkpoint, observed/evidence values, and that step's traffic boundary when capture is enabled.

### Optional raw traffic capture

When backend/API behavior or page transitions matter to proof, enable the independent Chrome CDP observer:

~~~bash
./bin/browser-automation run example_read_page --capture-traffic
~~~

The run then contains:

~~~text
.runs/<automation>/<run-id>/traffic/
├── events.jsonl
├── state.json
├── checkpoints.jsonl
└── capture.log
~~~

`traffic/events.jsonl` is the raw evidence. Every Chrome `Network.*` event receives a monotonically increasing `seq`, capture time and target metadata. The recorder uses its own browser-level CDP connection; it does **not** drain Browser Harness's event buffer and therefore does not steal network events from automation helpers.

`traffic/state.json` is an internal live snapshot owned by the recorder. It tracks active requests, persistent streams and cumulative request outcomes so the runner can reason about in-flight work without parsing the append-only capture.

`traffic/checkpoints.jsonl` is the learning index. After each deterministic step returns — pass, checkpoint failure, or exception — the runner waits at the checkpoint boundary before moving on. Each boundary records `from_seq` and `through_seq`, so the corresponding raw traffic segment can be recovered without timestamp guessing.

#### Traffic settling

The current runtime defaults are:

- a request opened by `Network.requestWillBeSent` blocks the boundary until it reaches `loadingFinished` or `loadingFailed`;
- after no blocking request/lifecycle change is observed for **750 ms**, the boundary is `quiet`;
- EventSource plus open WebSocket/WebTransport connections are **persistent carry-over** and do not block forever;
- an ordinary request already active for **30 s** is classified **stale carry-over** rather than becoming an infinite barrier;
- if ordinary blocking traffic still exists after **10 s**, the boundary becomes `timeout_busy`;
- requests that consumed that timeout become explicit **carry-over** on later checkpoints, so the same stuck request does not charge another full 10 s at every transition; new requests still block normally;
- if the independent observer is missing or dies, the boundary becomes `unavailable`.

`loadingFailed`, HTTP responses with status >= 400, stale requests and persistent streams remain evidence in the checkpoint segment. They do **not** automatically mean the automation's business checkpoint failed. Likewise, `timeout_busy` or `unavailable` degrades traffic observability but does not convert an otherwise-valid deterministic step into failure.

This distinction is intentional:

~~~text
functional checkpoint
    proves the automation's domain claim

traffic boundary
    proves how cleanly network evidence can be segmented around that claim
~~~

Do not add arbitrary `wait()` calls to every automation in an attempt to recreate network-idle behavior. When traffic capture is enabled, the shared runner owns this transition once.

A request that remains stale, persistent, or previously timed-out across a boundary is listed as carry-over; its later raw events keep their sequence numbers and can therefore be connected across adjacent segments during repair or later learning.

This is protocol-event capture, not packet capture: response bodies are not fetched separately. Chrome events can still contain request/response headers, cookies and request post data. Treat all three traffic artifacts as sensitive browser-session evidence. They stay under gitignored `.runs/` and should only be enabled when they change diagnosis or proof.

## Checkpoints

Before each step the runtime writes local state. After a passing step it writes an event containing the observed evidence.

A step continues only when its implementation returns:

~~~python
{"ok": True, "observed": {...}}
~~~

Anything else is a failed checkpoint. Later steps do not execute after a mismatch.

## Failure handoff

Failures live under `.runs/`, which is intentionally gitignored.

`failure.json` contains:

- automation and run IDs;
- exact failed checkpoint;
- contract pseudocode and expected state;
- known variants;
- observed result;
- exception when one occurred;
- current browser/tab/page information when available;
- the failed `steps/<ordinal>-<id>/` directory;
- `checkpoint.json` plus failure screenshot/evidence colocated in that directory;
- screenshot path when capture succeeds;
- `traffic/events.jsonl` and `traffic/checkpoints.jsonl` paths when the run used `--capture-traffic`;
- the failed step's traffic boundary, including its exact raw sequence range and settle classification;
- contract and module paths;
- exact `step_<id>()` function;
- safe restart/resume instruction;
- `.agents/skills/browser-automation/SKILL.md` as the agent entrypoint.

That keeps agent startup narrow: it does not have to rediscover the entire automation or repository.

## Resume

A contract step explicitly declares `resume = "safe"` or `resume = "restart"`.

When safe:

~~~bash
./bin/browser-automation run <id> --from <checkpoint> ...
~~~

The runner rejects `--from` for a restart-only checkpoint.

This is a contract assertion, not a convenience flag. Mark a step safe only when previous state can legitimately be assumed from the current browser session.

## Repair

For a failed automation:

1. give the agent the emitted `failure.json`;
2. open the referenced failed step directory and its `checkpoint.json`;
3. load the [browser-automation skill](../.agents/skills/browser-automation/SKILL.md);
4. inspect only the failed contract step + matching module function first;
5. load Browser Use's generated skill only when live browser exploration is necessary;
6. update contract knowledge before implementation behavior;
7. validate;
8. retry from the checkpoint only when `resume = "safe"`;
9. prove final success.

The repair artifact is the new contract/module behavior committed to Git. `.runs/` is transient evidence, not permanent doctrine.

## Example

`automations/example_read_page/` is deliberately read-only except for opening a public tab. It proves the contract/module/run/failure path without depending on an authenticated site.

~~~bash
./bin/browser-automation run example_read_page
~~~

## Boundaries

This layer does **not**:

- replace Browser Use interaction knowledge;
- replace the browser runtime;
- put an agent into successful routine runs;
- infer permission beyond `[authority]`;
- make one Chrome identity safely parallel.

When Chrome/CDP itself is unhealthy, switch to [Operations](operations.md) and the `browser-runtime` skill rather than teaching an automation to work around broken infrastructure.
