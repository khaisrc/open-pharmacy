#!/usr/bin/env bash
# Upsert this workflow's section within one shared "CI summary" PR comment,
# so the e2e and test workflows post to the same comment instead of each
# leaving their own. Usage: pr_comment.sh SECTION_KEY < markdown-body
#
# Sections are delimited by HTML comments (invisible when rendered) keyed by
# SECTION_KEY, so a run only ever touches its own slice of the comment body
# and leaves whatever the other workflow last wrote alone.
#
# Not race-proof: two workflows finding no comment at the same instant on a
# brand-new PR can both create one. Harmless -- every later run keeps
# updating the older (first) comment, so the duplicate just goes stale.
# Given how rarely two workflows finish within the same instant on a fresh
# PR, a lock to close that window was not worth the complexity here.
set -euo pipefail

section_key="$1"
new_section="$(cat)"

: "${GITHUB_REPOSITORY:?}"
: "${PR_NUMBER:?}"

top_marker="<!-- ci-summary -->"
begin="<!-- ci-summary:${section_key}:begin -->"
end="<!-- ci-summary:${section_key}:end -->"
block="${begin}
${new_section}
${end}"

comments_url="repos/${GITHUB_REPOSITORY}/issues/${PR_NUMBER}/comments"

existing_id="$(gh api "$comments_url" --paginate \
  --jq "[.[] | select(.body | startswith(\"${top_marker}\"))][0].id // empty")"

# -F, not -f: only -F expands the @- that reads the body from stdin. -f takes
# its value literally, which posts a comment whose entire text is "@-".
if [[ -z "$existing_id" ]]; then
  printf '%s\n\n%s\n' "$top_marker" "$block" | gh api "$comments_url" -F body=@- >/dev/null
  exit 0
fi

old_body="$(gh api "repos/${GITHUB_REPOSITORY}/issues/comments/${existing_id}" --jq .body)"

if grep -qF "$begin" <<<"$old_body"; then
  new_body="$(awk -v b="$begin" -v e="$end" -v s="$block" '
    $0 == b { print s; skip = 1; next }
    $0 == e { skip = 0; next }
    skip { next }
    { print }
  ' <<<"$old_body")"
else
  new_body="${old_body}"$'\n\n'"${block}"
fi

printf '%s' "$new_body" |
  gh api -X PATCH "repos/${GITHUB_REPOSITORY}/issues/comments/${existing_id}" -F body=@- >/dev/null
