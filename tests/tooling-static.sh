#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${root}"

shell_targets=(
  "bin/browser-use"
  "bin/browser-use-setup"
  "bin/browser-use-doctor"
  "bin/browser-automation"
)

for path in "${shell_targets[@]}"; do
  bash -n "${path}"
done

if grep -nF '\${' "${shell_targets[@]}"; then
  echo "ERROR: escaped Bash parameter expansion found in executable adapter(s)." >&2
  echo "Use ${VAR}, not \${VAR}, unless a literal token is intentionally required." >&2
  exit 1
fi

python3 - <<'PY'
import ast
import pathlib
import tomllib

root = pathlib.Path.cwd()
for path in sorted((root / 'automations').rglob('*.py')):
    ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
for path in sorted((root / 'automations').rglob('contract.toml')):
    tomllib.loads(path.read_text(encoding='utf-8'))
print('tooling static validation passed')
PY

python3 automations/_runtime/cli.py run --help | grep -q -- '--capture-traffic'

python3 tests/traffic-boundaries.py

python3 tests/run-layout.py
