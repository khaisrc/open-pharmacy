#!/usr/bin/env python3
"""Render one JUnit XML file as a short markdown block for a PR comment.

Works on both e2e/run.sh's JUnit (bin/report.py) and pytest's --junitxml:
both nest <testcase> under <testsuite>, which is all this reads. Kept to a
bold one-line count plus, only on failure, a collapsed list of names -- the
whole point is a summary a reader does not have to scroll past.
"""

import sys
import xml.etree.ElementTree as ET

MAX_LISTED = 15


def render(path, label):
    try:
        root = ET.parse(path).getroot()
    except (FileNotFoundError, ET.ParseError):
        return f"**{label}:** ⚠️ no results (the suite did not produce a report)"

    suites = root.findall(".//testsuite") if root.tag != "testsuite" else [root]

    # Some writers (e2e/run.sh's) only put time on the outer <testsuites>,
    # not on each <testsuite>; others (pytest's) put it on both. Prefer the
    # per-suite sum when it is actually there, the outer total otherwise.
    seconds = float(root.get("time", 0) or 0) if root.tag == "testsuites" else 0.0

    tests = failures = errors = skipped = 0
    suite_seconds = 0.0
    broken = []
    for suite in suites:
        tests += int(suite.get("tests", 0) or 0)
        failures += int(suite.get("failures", 0) or 0)
        errors += int(suite.get("errors", 0) or 0)
        skipped += int(suite.get("skipped", 0) or 0)
        suite_seconds += float(suite.get("time", 0) or 0)
        for case in suite.findall("testcase"):
            bad = case.find("failure")
            if bad is None:
                bad = case.find("error")
            if bad is not None:
                classname = case.get("classname", "")
                name = case.get("name", "")
                broken.append(f"{classname}: {name}" if classname else name)

    if suite_seconds:
        seconds = suite_seconds
    failed = failures + errors
    passed = tests - failed - skipped
    icon = "✅" if failed == 0 else "❌"
    line = f"**{label}:** {icon} {passed} passed, {failed} failed"
    if skipped:
        line += f", {skipped} skipped"
    line += f" ({seconds:.0f}s)"

    if not broken:
        return line

    listed = "\n".join(f"- {name}" for name in broken[:MAX_LISTED])
    if len(broken) > MAX_LISTED:
        listed += f"\n- …and {len(broken) - MAX_LISTED} more"
    return f"{line}\n\n<details><summary>Failures</summary>\n\n{listed}\n\n</details>"


if __name__ == "__main__":
    junit_path, label = sys.argv[1], sys.argv[2]
    print(render(junit_path, label))
