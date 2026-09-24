#!/usr/bin/env bash
# Render the lint job's checks as one short markdown block for the shared
# PR comment. Usage: lint_summary.sh NAME OUTCOME FILE [NAME OUTCOME FILE ...]
#
# OUTCOME is a step's GitHub Actions outcome; FILE is where that step teed
# its combined output. Only failing checks contribute output, and that goes
# behind a <details> so the comment stays a summary.
set -euo pipefail

MAX_LINES=25

statuses=""
details=""
failed=0
skipped=0

while (($#)); do
  name="$1" outcome="$2" file="$3"
  shift 3

  case "$outcome" in
    success) statuses+="${statuses:+, }${name} ✅"; continue ;;
    failure) statuses+="${statuses:+, }${name} ❌"; failed=1 ;;
    *)       statuses+="${statuses:+, }${name} ⏭"; skipped=1; continue ;;
  esac

  # Compose narrates container startup on stderr, which is captured along
  # with the tool's own output and is noise in a summary.
  body="$(grep -vE '^[[:space:]]*(Container|Network|Volume) ' "$file" 2>/dev/null |
          sed '/^[[:space:]]*$/d' || true)"
  shown="$(head -n "$MAX_LINES" <<<"$body")"
  if [[ "$(wc -l <<<"$body")" -gt "$MAX_LINES" ]]; then
    shown+=$'\n''… truncated'
  fi
  details+=$'\n'"\`${name}\`"$'\n\n```\n'"${shown}"$'\n```\n'
done

icon="✅"
((skipped)) && icon="⚠️"
((failed)) && icon="❌"

printf '**lint:** %s %s\n' "$icon" "$statuses"
((failed)) && printf '\n<details><summary>Output</summary>\n%s\n</details>\n' "$details"

exit 0
