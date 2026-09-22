#!/usr/bin/env bash
# Upsert this workflow's section within one shared "CI summary" PR comment,
# so every job posts to the same comment instead of each leaving its own.
# Usage: pr_comment.sh SECTION_KEY < markdown-body
#
# Sections are delimited by HTML comments (invisible when rendered) keyed by
# SECTION_KEY, so a run only ever touches its own slice of the comment body
# and leaves whatever the other jobs last wrote alone.
#
# Three jobs write here and two of them (test and lint) start together, so
# the read-modify-write below really does interleave: both read the same
# body, both splice their own section into it, and the second PATCH drops
# the first one's section. There is no conditional write on this endpoint to
# prevent that, so instead each attempt reads the comment back afterwards
# and retries from the current body if its section did not survive.
set -euo pipefail

ATTEMPTS=5

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
comment_url() { echo "repos/${GITHUB_REPOSITORY}/issues/comments/$1"; }

# Oldest first, so concurrent creates converge on one comment rather than
# each job adopting a different duplicate.
find_comment() {
  gh api "$comments_url" --paginate \
    --jq "[.[] | select(.body | startswith(\"${top_marker}\"))][0].id // empty"
}

# Replace this section in place if it is already there, append it if not.
#
# The markers and the block reach awk through the environment, not -v: -v
# expands escape sequences in the value, so a section quoting tool output
# came out with every literal \n turned into a real line break.
splice() {
  local body="$1"
  if grep -qF "$begin" <<<"$body"; then
    awk_b="$begin" awk_e="$end" awk_s="$block" awk '
      $0 == ENVIRON["awk_b"] { print ENVIRON["awk_s"]; skip = 1; next }
      $0 == ENVIRON["awk_e"] { skip = 0; next }
      skip { next }
      { print }
    ' <<<"$body"
  else
    printf '%s\n\n%s\n' "$body" "$block"
  fi
}

for attempt in $(seq "$ATTEMPTS"); do
  existing_id="$(find_comment)"

  # -F, not -f: only -F expands the @- that reads the body from stdin. -f
  # takes its value literally, posting a comment whose whole text is "@-".
  if [[ -z "$existing_id" ]]; then
    printf '%s\n\n%s\n' "$top_marker" "$block" |
      gh api "$comments_url" -F body=@- >/dev/null
  else
    splice "$(gh api "$(comment_url "$existing_id")" --jq .body)" |
      gh api -X PATCH "$(comment_url "$existing_id")" -F body=@- >/dev/null
  fi

  # Read back rather than trust the write: a job that raced us may have
  # PATCHed over this section between our read and our write.
  settled_id="$(find_comment)"
  if [[ -n "$settled_id" ]] &&
     gh api "$(comment_url "$settled_id")" --jq .body | grep -qF "$begin"; then
    exit 0
  fi

  sleep "$(( RANDOM % 3 + 1 ))"
done

echo "could not get the ${section_key} section to stick after ${ATTEMPTS} attempts" >&2
exit 1
