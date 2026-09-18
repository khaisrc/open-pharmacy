#!/usr/bin/env python3
"""Render the suite's event log as a single HTML page.

Reads the JSONL that lib/http.sh appends to and groups it the way the run was
written: case file, then section, then request, then the assertions that read
that request's response. Every exchange keeps its full detail behind a
disclosure, so a passing run is a short page and a failing one can be opened
down to the header that caused it.

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


class Request:
    __slots__ = ("event", "assertions")

    def __init__(self, event):
        self.event = event
        self.assertions = []

    @property
    def failed(self):
        return any(a["outcome"] == "FAIL" for a in self.assertions)


def load(path):
    """Group events into cases -> sections -> requests, preserving run order."""
    cases = OrderedDict()
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

        if event["t"] == "request":
            section[event["seq"]] = Request(event)
        elif event["t"] == "assertion":
            request = section.get(event["seq"])
            if request is None:
                # A setup failure can record an assertion before any request in
                # its section; give it somewhere to live rather than dropping it.
                request = section.setdefault(event["seq"], Request(None))
            request.assertions.append(event)
    return cases


def counts(cases):
    passed = failed = requests = 0
    for sections in cases.values():
        for section in sections.values():
            for request in section.values():
                if request.event is not None:
                    requests += 1
                for assertion in request.assertions:
                    if assertion["outcome"] == "PASS":
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


def render_request(request):
    event = request.event
    if event is None:
        return ""

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

    assertions = "".join(
        f'<li class="flex gap-2 items-start py-0.5">'
        f'<span class="{"text-success" if a["outcome"] == "PASS" else "text-error"} '
        f'font-mono text-xs mt-0.5">{"PASS" if a["outcome"] == "PASS" else "FAIL"}</span>'
        f'<span class="text-sm">{e(a["description"])}</span></li>'
        + (
            f'<li><pre class="bg-error/10 text-error rounded-box p-3 text-xs '
            f'overflow-x-auto my-1 ml-12"><code>{e(a["detail"])}</code></pre></li>'
            if a["outcome"] == "FAIL" and a["detail"]
            else ""
        )
        for a in request.assertions
    )

    detail_summary = 'class="collapse-title text-sm font-medium min-h-0 py-2 px-3"'

    # A request with no assertions is a fixture being built, kept in the report
    # because it is often the thing that explains the request after it. It is
    # tagged with a badge rather than dimmed, so it stays as legible as the rest.
    is_setup = not request.assertions
    setup_tag = (
        '<span class="badge badge-xs badge-ghost">setup</span>'
        if is_setup else ""
    )
    return f"""
<div class="border-l-2 {"border-error" if request.failed else "border-base-300"} pl-4 py-2"
     data-failed="{"1" if request.failed else "0"}"
     data-text="{e((method + " " + split.path + " " + " ".join(a["description"] for a in request.assertions)).lower())}">
  <div class="flex flex-wrap items-center gap-2">
    <span class="badge badge-sm {METHOD_TONE.get(method, "badge-ghost")} font-mono">{e(method)}</span>
    <code class="text-sm break-all">{e(split.path)}</code>
    <span class="badge badge-sm {status_tone(status)} font-mono">{status}</span>
    {setup_tag}
    <span class="text-xs opacity-50 font-mono ml-auto">{ms:.0f} ms</span>
  </div>
  <ul class="mt-2">{assertions}</ul>
  <div class="flex flex-col gap-2 mt-2">
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


def render_case(name, sections):
    case_passed = case_failed = 0
    blocks = []
    setup_blocks = []
    for section_name, requests in sections.items():
        regular = {seq: r for seq, r in requests.items() if r.assertions}
        setup = {seq: r for seq, r in requests.items() if not r.assertions}

        for request in requests.values():
            for assertion in request.assertions:
                if assertion["outcome"] == "PASS":
                    case_passed += 1
                else:
                    case_failed += 1

        heading = (
            f'<h3 class="text-sm font-semibold uppercase tracking-wide opacity-70 '
            f'mt-6 mb-2 first:mt-0">{e(section_name)}</h3>'
            if section_name
            else ""
        )
        if regular:
            rendered = "".join(render_request(r) for r in regular.values())
            blocks.append(f'<div class="section-block">{heading}{rendered}</div>')
        if setup:
            rendered = "".join(render_request(r) for r in setup.values())
            setup_blocks.append(f'<div class="section-block">{heading}{rendered}</div>')

    tally = f'<span class="badge badge-sm badge-success badge-soft">{case_passed}</span>'
    if case_failed:
        tally += f'<span class="badge badge-sm badge-error ml-1">{case_failed}</span>'

    # Fixture-building requests carry no assertions of their own, so they are
    # pulled out into their own collapsible tucked under the case rather than
    # interleaved with the requests the case is actually asserting on.
    setup_section = ""
    if setup_blocks:
        setup_section = f"""
    <details class="collapse collapse-arrow bg-base-200 rounded-box mt-4">
      <summary class="collapse-title text-sm font-medium min-h-0 py-2 px-3">Setup requests</summary>
      <div class="collapse-content">{"".join(setup_blocks)}</div>
    </details>"""

    return f"""
<details class="collapse collapse-arrow bg-base-100 border border-base-300 rounded-box mb-3 case-block"
         data-failed="{"1" if case_failed else "0"}" {"open" if case_failed else ""}>
  <summary class="collapse-title font-semibold flex items-center gap-3">
    <span>{e(title_case(name))}</span>
    <span class="text-xs opacity-50 font-mono font-normal">{e(name)}.sh</span>
    <span class="ml-auto">{tally}</span>
  </summary>
  <div class="collapse-content">{"".join(blocks)}{setup_section}</div>
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
    <input id="q" type="search" placeholder="Filter by path or assertion"
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
    Authorization headers are redacted.
  </footer>
</div>

<style type="text/tailwindcss">
  :root[data-theme="dark"] .theme-light {{ display: none; }}
  :root[data-theme="light"] .theme-dark {{ display: none; }}
  /* highlight.js paints its own surface on the element it highlights, which
     would leave highlighted bodies a different colour from plain ones. Only
     its token colours are wanted here. */
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
    """The same results as JUnit XML, which every CI system already reads."""
    suites = []
    for name, sections in cases.items():
        tests = []
        failures = 0
        for section_name, requests in sections.items():
            for request in requests.values():
                for assertion in request.assertions:
                    classname = f"{name}.{section_name}" if section_name else name
                    if assertion["outcome"] == "FAIL":
                        failures += 1
                        body = (
                            f'<failure message="{e(assertion["description"])}">'
                            f'{e(assertion["detail"])}</failure>'
                        )
                    else:
                        body = ""
                    tests.append(
                        f'    <testcase classname="{e(classname)}" '
                        f'name="{e(assertion["description"])}">{body}</testcase>'
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
