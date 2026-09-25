#!/usr/bin/env bash
# Install zstack skills without the plugin system: symlink them into ~/.claude/skills
# (personal) or ./.claude/skills (one project).
#
#   scripts/install.sh                 # every skill, personal
#   scripts/install.sh vehicle         # one skill, personal
#   scripts/install.sh --project .     # every skill into ./.claude/skills
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
dest="$HOME/.claude/skills"
if [[ "${1:-}" == "--project" ]]; then
  dest="$(cd "${2:-.}" && pwd)/.claude/skills"
  shift 2 || shift $#
fi
mkdir -p "$dest"
names=("$@")
if [[ ${#names[@]} -eq 0 ]]; then
  for d in "$root"/skills/*/; do
    [[ -f "$d/SKILL.md" ]] && names+=("$(basename "$d")")
  done
fi
for n in "${names[@]}"; do
  src="$root/skills/$n"
  [[ -f "$src/SKILL.md" ]] || { echo "no such skill: $n" >&2; exit 1; }
  ln -sfn "$src" "$dest/$n"
  echo "linked $dest/$n -> $src"
done
