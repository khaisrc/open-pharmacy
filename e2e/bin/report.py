#!/usr/bin/env python3
"""Render the suite's event log as a single HTML page.

Reads the JSONL that lib/http.sh appends to and lays it out as the feature file
the run already describes: case file as Feature, section as Scenario, and under
each the Given and When steps with the Then and Ands that read their responses.
The steps are not inferred here -- every event names the step it belongs to --
so this page, the JUnit XML and the terminal all tell the same story.

Every exchange keeps its full detail behind a disclosure, so a passing run is a
short page and a failing one can be opened down to the header that caused it.

The page pulls Tailwind and daisyUI from a CDN rather than defining its own
components, so there is no stylesheet here to drift out of date -- and no build
step either. It needs a network connection to render.
"""

import argparse
import html
import json
import re
import sys
from collections import OrderedDict
from datetime import datetime, timezone
from urllib.parse import urlsplit, parse_qsl

TAILWIND_CDN = "https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4"
DAISYUI_CDN = "https://cdn.jsdelivr.net/npm/daisyui@5/daisyui.css"

# highlight.js, as its ES-module core plus the one grammar this report needs:
# 21 KB against the 128 KB of the bundle that carries every common language.
HLJS = "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.11.2"
HLJS_CORE = f"{HLJS}/es/core.min.js"
HLJS_JSON = f"{HLJS}/es/languages/json.min.js"
HLJS_LIGHT_CSS = f"{HLJS}/styles/github.min.css"
HLJS_DARK_CSS = f"{HLJS}/styles/github-dark.min.css"

# Bodies are shown in full up to this much. A few list endpoints return a
# hundred nested prescriptions, and three of those would dominate the page.
MAX_BODY_CHARS = 20_000

METHOD_TONE = {
    "GET": "badge-info",
    "POST": "badge-success",
    "PUT": "badge-warning",
    "PATCH": "badge-warning",
    "DELETE": "badge-error",
}


def e(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def status_tone(status: int) -> str:
    if 200 <= status < 300:
        return "badge-success"
    if status in (401, 403):
        return "badge-warning"
    if 400 <= status < 500:
        return "badge-warning"
    if status >= 500:
        return "badge-error"
    return "badge-ghost"


def pretty_body(body: str):
    """Indent a JSON body and say so; anything else is returned unchanged.

    The caller needs to know which it got, because only real JSON is handed to
    highlight.js -- a plain-text error page tagged as JSON highlights as a mess.
    """
    if not body:
        return "", False
    stripped = body.strip()
    if stripped[:1] in "{[":
        try:
            return json.dumps(json.loads(stripped), indent=2, ensure_ascii=False), True
        except (ValueError, RecursionError):
            pass
    return body, False


def truncate(text: str) -> tuple:
    if len(text) <= MAX_BODY_CHARS:
        return text, False
    return text[:MAX_BODY_CHARS], True


def title_case(name: str) -> str:
    """'20_medications' -> 'Medications'."""
    return re.sub(r"^\d+_", "", name).replace("_", " ").capitalize()


class Step:
    """A Gherkin step, with everything the run did inside it.

    A When is one request and the Then/And assertions that read its response.
    A Given is a fixture builder, which often takes several requests and
    asserts nothing: it either held, or the case file stopped on it.
    """

    __slots__ = ("kind", "text", "number", "requests", "assertions")

    def __init__(self, kind, text, number):
        self.kind = kind
        self.text = text
        self.number = number
        self.requests = []
        self.assertions = []

    @property
    def keyword(self):
        return "Given" if self.kind == "given" else "When"

    @property
    def failed(self):
        return any(a["outcome"] == "fail" for a in self.assertions)


def scenario_label(section):
    """Steps taken before the first `section` are the case file's Background."""
    return section if section else "Background"


def load(path):
    """Group events into features -> scenarios -> steps, preserving run order."""
    cases = OrderedDict()
    # Falls back to matching an assertion to its request when a step number is
    # missing, which is all an event log written by an older lib/http.sh has.
    by_request = {}
    for line_number, line in enumerate(open(path, encoding="utf-8"), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            print(f"report: skipping malformed event on line {line_number}", file=sys.stderr)
            continue

        case = cases.setdefault(event["case"], OrderedDict())
        section = case.setdefault(event.get("section") or "", OrderedDict())
        number = event.get("step", event["seq"])

        if event["t"] == "request":
            step = section.get(number)
            if step is None:
                step = section[number] = Step(
                    event.get("step_kind") or "when", event.get("step_text") or "", number
                )
            step.requests.append(event)
            by_request[(event["case"], event["seq"])] = step
        elif event["t"] == "assertion":
            step = section.get(number) or by_request.get((event["case"], event["seq"]))
            if step is None:
                # A Given that died before its first request still recorded a
                # failure; give it somewhere to live rather than dropping it.
                step = section.setdefault(number, Step("given", "", number))
            step.assertions.append(event)
    return cases


def counts(cases):
    passed = failed = requests = 0
    for sections in cases.values():
        for section in sections.values():
            for step in section.values():
                requests += len(step.requests)
                for assertion in step.assertions:
                    if assertion["outcome"] == "pass":
                        passed += 1
                    else:
                        failed += 1
    return passed, failed, requests


def header_table(headers):
    if not headers:
        return '<p class="text-sm opacity-60 italic">none</p>'
    rows = "".join(
        f'<tr><td class="font-mono text-xs whitespace-nowrap align-top opacity-70">{e(name)}</td>'
        f'<td class="font-mono text-xs break-all">{e(value)}</td></tr>'
        for name, value in headers
    )
    return f'<table class="table table-xs"><tbody>{rows}</tbody></table>'


def body_block(body, label):
    if not body:
        return f'<p class="text-sm opacity-60 italic">no {label} body</p>'
    pretty, is_json = pretty_body(body)
    shown, clipped = truncate(pretty)
    note = (
        '<p class="text-xs opacity-60 mt-1">'
        f"shown to the first {MAX_BODY_CHARS:,} characters</p>" if clipped else ""
    )
    # Clipping can cut mid-token, which would leave highlight.js parsing an
    # unterminated string, so a clipped body is shown plain.
    language = ' class="language-json"' if is_json and not clipped else ""
    return (
        f'<pre class="bg-base-200 rounded-box p-3 text-xs overflow-x-auto max-h-96">'
        f"<code{language}>{e(shown)}</code></pre>{note}"
    )


def render_exchange(event, labeled):
    """One request/response pair, behind its two disclosures.

    `labeled` adds the method, path and status above them, which a step needs
    only when it made more than one request -- a When's own text already says
    what the single exchange under it was.
    """
    split = urlsplit(event["url"])
    query = parse_qsl(split.query, keep_blank_values=True)
    method, status = event["method"], event["status"]
    ms = event["seconds"] * 1000

    query_row = ""
    if query:
        rows = "".join(
            f'<tr><td class="font-mono text-xs whitespace-nowrap align-top opacity-70">{e(k)}</td>'
            f'<td class="font-mono text-xs break-all">{e(v)}</td></tr>'
            for k, v in query
        )
        query_row = (
            '<h4 class="text-xs uppercase tracking-wide opacity-60 mt-3 mb-1">Query</h4>'
            f'<table class="table table-xs"><tbody>{rows}</tbody></table>'
        )

    label = ""
    if labeled:
        label = f"""
  <div class="flex flex-wrap items-center gap-2 mb-1">
    <span class="badge badge-xs {METHOD_TONE.get(method, "badge-ghost")} font-mono">{e(method)}</span>
    <code class="text-xs break-all opacity-70">{e(split.path)}</code>
    <span class="badge badge-xs {status_tone(status)} font-mono">{status}</span>
    <span class="text-xs opacity-50 font-mono ml-auto">{ms:.0f} ms</span>
  </div>"""

    detail_summary = 'class="collapse-title text-sm font-medium min-h-0 py-2 px-3"'
    return f"""
<div class="mt-2">{label}
  <div class="flex flex-col gap-2">
    <details class="collapse collapse-arrow bg-base-200 rounded-box">
      <summary {detail_summary}>Request</summary>
      <div class="collapse-content text-sm">
        <h4 class="text-xs uppercase tracking-wide opacity-60 mb-1">URL</h4>
        <code class="text-xs break-all">{e(event["url"])}</code>
        {query_row}
        <h4 class="text-xs uppercase tracking-wide opacity-60 mt-3 mb-1">Headers</h4>
        {header_table(event["request_headers"])}
        <h4 class="text-xs uppercase tracking-wide opacity-60 mt-3 mb-1">Body</h4>
        {body_block(event["request_body"], "request")}
      </div>
    </details>
    <details class="collapse collapse-arrow bg-base-200 rounded-box">
      <summary {detail_summary}>Response</summary>
      <div class="collapse-content text-sm">
        <h4 class="text-xs uppercase tracking-wide opacity-60 mb-1">Status</h4>
        <span class="badge badge-sm {status_tone(status)} font-mono">{status}</span>
        <span class="text-xs opacity-60 ml-2">{ms:.0f} ms</span>
        <h4 class="text-xs uppercase tracking-wide opacity-60 mt-3 mb-1">Headers</h4>
        {header_table(event["response_headers"])}
        <h4 class="text-xs uppercase tracking-wide opacity-60 mt-3 mb-1">Body</h4>
        {body_block(event["response_body"], "response")}
      </div>
    </details>
  </div>
</div>"""


def render_step(step):
    """A step and its Thens, as one filterable block."""
    single = len(step.requests) == 1
    first = step.requests[0] if step.requests else None

    # The status of a single request lives on its Response disclosure, not up
    # here: a step line that carries only its own words reads as a step. A
    # Given names a fixture rather than a request, and the exchanges it took
    # are labeled -- status included -- individually underneath.
    trailing = ""
    if single and first is not None:
        ms = first["seconds"] * 1000
        trailing = f'<span class="text-xs opacity-50 font-mono ml-auto">{ms:.0f} ms</span>'
    elif step.requests:
        total_ms = sum(r["seconds"] for r in step.requests) * 1000
        trailing = (
            f'<span class="badge badge-sm badge-ghost">{len(step.requests)} requests</span>'
            f'<span class="text-xs opacity-50 font-mono ml-auto">{total_ms:.0f} ms</span>'
        )

    # The keyword is set as plain text in a fixed column, the way the terminal
    # prints it, rather than as a badge: a step reads as a sentence, and the
    # badges left on the line are the ones that carry data. The empty span
    # ahead of it is the outcome gutter, which a Given or a When has nothing to
    # put in -- it keeps every keyword in one column down the scenario.

    assertions = "".join(
        f'<li class="flex gap-2 items-start py-0.5">'
        f'<span class="{"text-success" if a["outcome"] == "pass" else "text-error"} '
        f'font-mono text-xs mt-0.5 w-9 shrink-0">'
        f'{"pass" if a["outcome"] == "pass" else "fail"}</span>'
        f'<span class="opacity-50 text-xs font-mono mt-0.5 w-10 shrink-0">'
        f'{e(a.get("keyword") or "Then")}</span>'
        f'<span class="text-sm">{e(a["description"])}</span></li>'
        + (
            f'<li><pre class="bg-error/10 text-error rounded-box p-3 text-xs '
            f'overflow-x-auto my-1 ml-20"><code>{e(a["detail"])}</code></pre></li>'
            if a["outcome"] == "fail" and a["detail"]
            else ""
        )
        for a in step.assertions
    )

    exchanges = "".join(render_exchange(r, not single) for r in step.requests)
    searchable = " ".join(
        [step.keyword, step.text]
        + [r["method"] + " " + urlsplit(r["url"]).path for r in step.requests]
        + [a["description"] for a in step.assertions]
    ).lower()

    return f"""
<div class="border-l-2 {"border-error" if step.failed else "border-base-300"} pl-4 py-2"
     data-failed="{"1" if step.failed else "0"}"
     data-text="{e(searchable)}">
  <div class="flex flex-wrap items-center gap-2">
    <span class="w-9 shrink-0"></span>
    <span class="text-xs font-mono opacity-50 w-10 shrink-0">{step.keyword}</span>
    <span class="text-sm">{e(step.text)}</span>
    {trailing}
  </div>
  <ul class="mt-2">{assertions}</ul>
  {exchanges}
</div>"""


def render_case(name, sections):
    case_passed = case_failed = 0
    blocks = []
    for section_name, steps in sections.items():
        for step in steps.values():
            for assertion in step.assertions:
                if assertion["outcome"] == "pass":
                    case_passed += 1
                else:
                    case_failed += 1

        heading = (
            f'<h3 class="text-sm font-semibold uppercase tracking-wide opacity-70 '
            f'mt-6 mb-2 first:mt-0">{e(scenario_label(section_name))}</h3>'
        )
        rendered = "".join(render_step(step) for step in steps.values())
        blocks.append(f'<div class="section-block">{heading}{rendered}</div>')

    tally = f'<span class="badge badge-sm badge-success badge-soft">{case_passed}</span>'
    if case_failed:
        tally += f'<span class="badge badge-sm badge-error ml-1">{case_failed}</span>'

    return f"""
<details class="collapse collapse-arrow bg-base-100 border border-base-300 rounded-box mb-3 case-block"
         data-failed="{"1" if case_failed else "0"}" {"open" if case_failed else ""}>
  <summary class="collapse-title font-semibold flex items-center gap-3">
    <span>{e(title_case(name))}</span>
    <span class="text-xs opacity-50 font-mono font-normal">{e(name)}.sh</span>
    <span class="ml-auto">{tally}</span>
  </summary>
  <div class="collapse-content">{"".join(blocks)}</div>
</details>"""


def render(cases, run_id, base_url, seconds, suite_name):
    passed, failed, requests = counts(cases)
    total = passed + failed
    ok = failed == 0
    generated = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

    verdict = (
        '<span class="badge badge-lg badge-success">all passing</span>'
        if ok
        else f'<span class="badge badge-lg badge-error">{failed} failing</span>'
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(suite_name)} &mdash; test report {e(run_id)}</title>
<script src="{TAILWIND_CDN}"></script>
<link href="{DAISYUI_CDN}" rel="stylesheet" type="text/css">
<link id="hljs-light" href="{HLJS_LIGHT_CSS}" rel="stylesheet">
<link id="hljs-dark" href="{HLJS_DARK_CSS}" rel="stylesheet" disabled>
<script>
// daisyUI's CDN build activates its dark theme through data-theme only -- it
// ships no prefers-color-scheme rule -- so the OS preference is applied here,
// before first paint, to avoid a flash of the light theme.
(function () {{
  var saved = null;
  try {{ saved = localStorage.getItem('report-theme'); }} catch (e) {{}}
  var dark = saved ? saved === 'dark'
                   : window.matchMedia('(prefers-color-scheme: dark)').matches;
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
}})();

// highlight.js ships one stylesheet per theme with no way to scope them, so
// both are linked and the unwanted one is disabled.
function syncHighlightTheme() {{
  var dark = document.documentElement.dataset.theme === 'dark';
  var light = document.getElementById('hljs-light');
  var darkSheet = document.getElementById('hljs-dark');
  if (light) light.disabled = dark;
  if (darkSheet) darkSheet.disabled = !dark;
}}
document.addEventListener('DOMContentLoaded', syncHighlightTheme);
</script>
</head>
<body class="bg-base-200 min-h-screen">
<div class="max-w-5xl mx-auto p-4 sm:p-8">

  <header class="mb-6">
    <div class="flex flex-wrap items-center gap-3 mb-2">
      <h1 class="text-2xl font-bold">{e(suite_name)}</h1>
      {verdict}
      <button id="theme" class="btn btn-sm btn-ghost ml-auto" title="Switch theme">
        <span class="theme-light">Dark</span><span class="theme-dark">Light</span>
      </button>
    </div>
    <p class="text-sm opacity-60 font-mono">{e(base_url)} &middot; run {e(run_id)} &middot; {e(generated)}</p>
  </header>

  <div class="stats stats-horizontal bg-base-100 border border-base-300 w-full mb-6 overflow-x-auto">
    <div class="stat"><div class="stat-title">Assertions</div><div class="stat-value text-3xl">{total}</div></div>
    <div class="stat"><div class="stat-title">Passed</div><div class="stat-value text-3xl text-success">{passed}</div></div>
    <div class="stat"><div class="stat-title">Failed</div>
      <div class="stat-value text-3xl {"text-error" if failed else "opacity-30"}">{failed}</div></div>
    <div class="stat"><div class="stat-title">Requests</div><div class="stat-value text-3xl">{requests}</div></div>
    <div class="stat"><div class="stat-title">Duration</div><div class="stat-value text-3xl">{seconds}s</div></div>
  </div>

  <div class="flex flex-wrap gap-3 items-center mb-4">
    <input id="q" type="search" placeholder="Filter by step, path or outcome"
           class="input input-bordered input-sm w-full sm:w-80">
    <label class="label cursor-pointer gap-2">
      <input id="only-failed" type="checkbox" class="toggle toggle-sm toggle-error">
      <span class="label-text text-sm">Failures only</span>
    </label>
    <button id="expand" class="btn btn-sm btn-ghost ml-auto">Expand all</button>
  </div>

  <main id="cases">
    {"".join(render_case(name, sections) for name, sections in cases.items())}
  </main>

  <footer class="text-xs opacity-50 mt-8 text-center">
    Generated by <code>bin/report.py</code> from the suite's event log.
    Each feature is a case file, each scenario a section within it.
    Authorization headers are redacted.
  </footer>
</div>

<style type="text/tailwindcss">
  :root[data-theme="dark"] .theme-light {{ display: none; }}
  :root[data-theme="light"] .theme-dark {{ display: none; }}
  /* highlight.js paints its own surface on the element it highlights, which
     would leave highlighted bodies a different color from plain ones. Only
     its token colors are wanted here. */
  pre code.hljs {{ background: transparent; padding: 0; }}
</style>

<script>
document.getElementById('theme').addEventListener('click', () => {{
  const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = next;
  syncHighlightTheme();
  try {{ localStorage.setItem('report-theme', next); }} catch (e) {{}}
}});

// Filtering hides rows rather than removing them, so clearing the box restores
// the page without a re-render.
const q = document.getElementById('q');
const onlyFailed = document.getElementById('only-failed');
const expand = document.getElementById('expand');

function apply() {{
  const needle = q.value.trim().toLowerCase();
  const failedOnly = onlyFailed.checked;
  document.querySelectorAll('.case-block').forEach(caseBlock => {{
    let visibleInCase = 0;
    caseBlock.querySelectorAll('[data-text]').forEach(row => {{
      const matches = (!needle || row.dataset.text.includes(needle))
                   && (!failedOnly || row.dataset.failed === '1');
      row.classList.toggle('hidden', !matches);
      if (matches) visibleInCase++;
    }});
    caseBlock.querySelectorAll('.section-block').forEach(section => {{
      const anyVisible = section.querySelector('[data-text]:not(.hidden)');
      section.classList.toggle('hidden', !anyVisible);
    }});
    caseBlock.classList.toggle('hidden', visibleInCase === 0);
    if (visibleInCase && (needle || failedOnly)) caseBlock.open = true;
  }});
}}

q.addEventListener('input', apply);
onlyFailed.addEventListener('change', apply);
expand.addEventListener('click', () => {{
  const open = expand.dataset.state !== 'open';
  document.querySelectorAll('details').forEach(d => {{ d.open = open; }});
  expand.dataset.state = open ? 'open' : 'closed';
  expand.textContent = open ? 'Collapse all' : 'Expand all';
}});
</script>

<script type="module">
import hljs from '{HLJS_CORE}';
import json from '{HLJS_JSON}';
hljs.registerLanguage('json', json);

// Highlighting every body up front would mean parsing well over a megabyte of
// JSON before the page could be used, for bodies almost all of which stay
// closed. Each one is highlighted the first time its disclosure opens instead.
function highlightWithin(root) {{
  root.querySelectorAll('code.language-json:not([data-highlighted])').forEach(el => {{
    // Opening a case opens nothing inside it, so a body still sitting behind a
    // closed Request or Response disclosure is left for its own toggle.
    if (el.closest('details:not([open])')) return;
    hljs.highlightElement(el);
    el.dataset.highlighted = 'yes';
  }});
}}

// The toggle event does not bubble, so this listens in the capture phase
// rather than attaching a listener to each of the several hundred disclosures.
document.addEventListener('toggle', event => {{
  const target = event.target;
  if (target instanceof HTMLDetailsElement && target.open) highlightWithin(target);
}}, true);
</script>
</body>
</html>"""


def render_junit(cases, seconds, suite_name):
    """The same results as JUnit XML, which every CI system already reads.

    A testcase is one Then: the feature and scenario become the classname, and
    the name carries the whole step -- the When that was made and the Then that
    read it -- so a CI failure list reads as the scenario did, without having
    to open the HTML report beside it.
    """
    suites = []
    for name, sections in cases.items():
        tests = []
        failures = 0
        for section_name, steps in sections.items():
            classname = f"{name}.{scenario_label(section_name)}"
            for step in steps.values():
                for assertion in step.assertions:
                    keyword = assertion.get("keyword") or "Then"
                    test_name = (
                        f"{step.keyword} {step.text} / {keyword} {assertion['description']}"
                    )
                    if assertion["outcome"] == "fail":
                        failures += 1
                        body = (
                            f'<failure message="{e(assertion["description"])}">'
                            f'{e(assertion["detail"])}</failure>'
                        )
                    else:
                        body = ""
                    tests.append(
                        f'    <testcase classname="{e(classname)}" '
                        f'name="{e(test_name)}">{body}</testcase>'
                    )
        suites.append(
            f'  <testsuite name="{e(name)}" tests="{len(tests)}" failures="{failures}">\n'
            + "\n".join(tests)
            + "\n  </testsuite>"
        )
    passed, failed, _ = counts(cases)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<testsuites name="{e(suite_name)}" tests="{passed + failed}" '
        f'failures="{failed}" time="{seconds}">\n'
        + "\n".join(suites)
        + "\n</testsuites>\n"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--junit", help="also write JUnit XML here")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--seconds", default="0")
    parser.add_argument("--suite-name", default="API")
    args = parser.parse_args()

    cases = load(args.events)
    if not cases:
        print("report: the event log is empty, nothing to render", file=sys.stderr)
        return 1

    with open(args.out, "w", encoding="utf-8") as handle:
        handle.write(render(cases, args.run_id, args.base_url, args.seconds, args.suite_name))

    if args.junit:
        with open(args.junit, "w", encoding="utf-8") as handle:
            handle.write(render_junit(cases, args.seconds, args.suite_name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
