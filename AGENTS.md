# UKLOK Browser — agent entrypoint

This repository inherits organization working rules from:

<https://github.com/uklok/ai-exchange/blob/main/AGENTS.md>

Read that entrypoint as the authority for issue → branch → review, evidence, attribution, and skill selection. This file adds only **browser-repository-specific** guidance.

## First 60 seconds

1. Open the assigned issue/PR and identify the concrete outcome.
2. Use [README.md](README.md) to choose the relevant task path.
3. For runtime, Docker, CDP/noVNC, profile, platform, or browser-startup work, load [browser-runtime](.agents/skills/browser-runtime/SKILL.md).
4. For **operating pages in a healthy browser**, use [Browser Use — Phase A](docs/browser-use.md). If `.agents/skills/browser-use/SKILL.md` is absent, run `./bin/browser-use-setup`, then load that generated upstream skill and drive the browser through `./bin/browser-use`.
5. For a **browser automation failure**, start from its `.runs/.../failure.json`, then load [browser-automation](.agents/skills/browser-automation/SKILL.md). Do not reload an agent for successful routine automation runs.
6. For designing a new deterministic browser automation, read [Browser automations](docs/automations.md) and load `browser-automation`; write the contract before its module.
7. Read only the task-specific page under [docs/](docs/README.md); do not ingest the entire documentation set by default.
8. Prove the user-visible/runtime claim, not merely file validity.

## Repo invariants

Treat these as current implementation constraints unless the task explicitly changes them:

- one **headful** Chrome process is shared by human noVNC and agent CDP;
- base Compose publishes no host ports;
- current image target is `linux/amd64`;
- production keeps Chrome sandbox enabled with no automatic fallback;
- the local overlay may retry with `--no-sandbox` only after the known namespace failure;
- `/data/chrome` is a Linux-container-owned browser identity;
- macOS/Windows Chrome profiles are not authentication-portable into this Linux browser;
- bind-mounted non-empty profiles require an explicit UKLOK marker or intentional adoption;
- stale Chrome `Singleton*` artifacts are startup debris, not durable identity;
- accepted image publication is separate from documentation/Compose-only changes;
- Browser Use is a capability layer over the existing Chrome: `BU_CDP_URL` must point at the UKLOK-owned CDP endpoint rather than silently introducing another browser owner;
- the Browser Use operation skill is generated from the pinned upstream package and is not UKLOK-owned doctrine;
- routine automations are deterministic: `contract.toml` owns intent/order/checkpoints and `run.py` only reflects those steps;
- an agent enters an automation only on explicit design/refinement work or after a checkpoint failure; normal successful runs do not load an agent;
- local `.runs/` evidence is transient and gitignored; durable learned behavior belongs in the automation contract/module;
- a run root is an index: checkpoint-owned evidence belongs under ordered `steps/<ordinal>-<id>/`; cross-step network evidence belongs under `traffic/`; do not flatten correlated artifacts back into the root;
- the runtime owns step-local placement (`ctx["run_dir"]`, `artifact_path()`, screenshot normalization) so individual automations do not re-own that architecture;
- when `--capture-traffic` is active, checkpoint traffic settling belongs to the shared runner, not individual automation sleeps; degraded traffic evidence is recorded without silently redefining functional success.

## Where truth lives

| Question | Source |
| --- | --- |
| What should the user run? | `docs/quickstart.md` |
| How does the system fit together? | `docs/architecture.md` |
| What owns login/session state? | `docs/profiles.md` |
| What is safe to expose/weaken? | `docs/security.md` |
| How do I diagnose runtime failures? | `docs/operations.md` |
| When/how is an image published? | `docs/releases.md` |
| What proves runtime acceptance? | `tests/smoke.sh` + workflow restart smoke |
| How should an agent reason about this runtime? | `.agents/skills/browser-runtime/SKILL.md` |
| How should an agent operate healthy pages? | `docs/browser-use.md` + generated `.agents/skills/browser-use/SKILL.md` |
| How are deterministic browser automations defined/repaired? | `docs/automations.md` + `.agents/skills/browser-automation/SKILL.md` |

Do not duplicate these rules into new files. Update the natural owner and repair inbound links when the contract changes.

## Merge gate

Before claiming a runtime change complete:

- relevant review threads are resolved;
- the actual browser path was exercised or the missing proof is named precisely;
- CDP and noVNC are not confused with Chrome health;
- profile lifecycle is tested when identity behavior changes;
- docs/rules are updated when a discovered invariant changes;
- human approval exists before merge.
