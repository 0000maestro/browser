# Browser automations

This directory is the contract-first knowledge layer over Browser Use.

A tracked automation lives at:

~~~text
automations/<id>/
├── contract.toml   intention, authority, pseudocode, checkpoints
└── run.py          deterministic reflection of those steps
~~~

Infrastructure shared by every automation stays under `_runtime/`; `_template/` is the scaffold source.

## Commands

~~~bash
./bin/browser-automation list
./bin/browser-automation new <id>
./bin/browser-automation validate [<id>]
./bin/browser-automation run <id> --input key=value
~~~

Every run is a local, gitignored evidence index:

~~~text
.runs/<id>/<run-id>/
├── run.json
├── result.json | failure.json
├── steps/
│   └── <ordinal>-<step-id>/
│       ├── checkpoint.json
│       └── <step evidence>
└── traffic/                 # only when capture is enabled
~~~

During a step, `ctx["run_dir"]` points at that step's directory; `ctx["run_root"]` points at the whole run. Screenshots and `artifact_path()` outputs are step-local by construction.

On failure, root `failure.json` points directly to the failed step directory, contract checkpoint and `step_<id>()`; the next mind should start there instead of scanning the full run.

See [Browser automations](../docs/automations.md) for the operating model and [.agents/skills/browser-automation/SKILL.md](../.agents/skills/browser-automation/SKILL.md) for agent repair behavior.
