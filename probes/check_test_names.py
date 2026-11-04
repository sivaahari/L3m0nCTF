#!/usr/bin/env python3
"""Audits test names referenced in docs/handoff/instancer.md and docs/security/instancer-threat-model.md.
Ensures every test name referenced in the break table and threat model exists in pytest collection.
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def get_collected_tests() -> set[str]:
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "services/instancer/tests",
        "plugins/l3mon_instances/tests",
        "tools/tests",
        "tests/integration",
        "--collect-only",
        "-q",
    ]
    res = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    tests = set()
    for line in res.stdout.splitlines():
        line = line.strip()
        if "::" in line:
            test_part = line.split("::")[-1]
            # Strip parametrized brackets e.g. test_foo[param] -> test_foo
            test_name = test_part.split("[")[0]
            tests.add(test_name)
    return tests


def check_docs():
    collected = get_collected_tests()
    print(f"Collected {len(collected)} unique tests from pytest.")

    docs_to_check = [
        ROOT / "docs" / "handoff" / "instancer.md",
        ROOT / "docs" / "security" / "instancer-threat-model.md",
    ]

    # Match test_... with optional .py
    pattern = re.compile(r"\b(test_[a-zA-Z0-9_]+)(\.py)?\b")
    missing = {}

    for doc in docs_to_check:
        if not doc.is_file():
            continue
        text = doc.read_text(encoding="utf-8")
        doc_missing = []

        for m in pattern.finditer(text):
            name = m.group(1)
            is_file = bool(m.group(2))

            if is_file:
                # Must exist as a test file in repo
                matches = list(ROOT.glob(f"**/{name}.py"))
                if not matches:
                    doc_missing.append(f"{name}.py (file not found)")
            else:
                # Must exist as a collected pytest test function
                if name not in collected:
                    doc_missing.append(name)

        if doc_missing:
            # Deduplicate preserving order
            seen = set()
            dedup = []
            for item in doc_missing:
                if item not in seen:
                    seen.add(item)
                    dedup.append(item)
            missing[doc.name] = dedup

    if missing:
        print("\nERROR: Found test names referenced in documentation that do not exist in pytest:")
        total_missing = 0
        for doc_name, test_list in missing.items():
            print(f"\nIn {doc_name} ({len(test_list)} missing):")
            for t in test_list:
                print(f"  - {t}")
            total_missing += len(test_list)
        print(f"\nTotal non-existent test references: {total_missing}")
        return 1
    else:
        print("\nAll test names referenced in documentation exist in pytest!")
        return 0


if __name__ == "__main__":
    sys.exit(check_docs())
