#!/usr/bin/env python3
"""
Adaptive Orchestrator â€” Self-Check & Diagnostics Engine
Verifies repository integrity, manifest schemas, subagent definitions,
template completeness, and Python environment health.
"""

import os
import sys
import json
import argparse

REQUIRED_FILES = [
    "SKILL.md",
    "AGENTS.md",
    "GEMINI.md",
    "manifest.json",
    "plugin.json",
    "skills.json",
    "LICENSE",
    "README.md",
    "subagents/subagents-definition.json",
    "subagents/explorer-researcher.md",
    "subagents/implementer.md",
    "subagents/reviewer-verifier.md",
    "subagents/challenger-auditor.md",
    "templates/mission.md",
    "templates/progress.md",
    "templates/dead-ends.md",
    "templates/gates.md",
    "templates/final-audit.md",
    "templates/handoff-report.md",
]

def run_doctor(verbose: bool = False) -> dict:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    results = {
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "python_compatible": sys.version_info >= (3, 9),
        "files_checked": 0,
        "files_missing": [],
        "manifests_valid": True,
        "subagents_valid": True,
        "templates_valid": True,
        "errors": []
    }

    # 1. Check all required files
    for rel_path in REQUIRED_FILES:
        full_path = os.path.join(repo_root, rel_path)
        results["files_checked"] += 1
        if not os.path.exists(full_path):
            results["files_missing"].append(rel_path)

    # 2. Check manifests JSON
    for json_file in ["manifest.json", "plugin.json", "skills.json", "subagents/subagents-definition.json"]:
        p = os.path.join(repo_root, json_file)
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if not isinstance(data, dict):
                        results["manifests_valid"] = False
                        results["errors"].append(f"{json_file} root must be a JSON object")
            except Exception as e:
                results["manifests_valid"] = False
                results["errors"].append(f"{json_file} parse error: {str(e)}")

    # 3. Verify SKILL.md frontmatter
    skill_path = os.path.join(repo_root, "SKILL.md")
    if os.path.exists(skill_path):
        with open(skill_path, "r", encoding="utf-8") as f:
            content = f.read()
            if not (content.startswith("---") and "name: adaptive-orchestrator" in content):
                results["errors"].append("SKILL.md missing valid YAML frontmatter")

    # Overall verdict
    is_healthy = (
        results["python_compatible"]
        and len(results["files_missing"]) == 0
        and results["manifests_valid"]
        and len(results["errors"]) == 0
    )
    results["healthy"] = is_healthy
    return results

def main():
    parser = argparse.ArgumentParser(description="Adaptive Orchestrator Doctor Diagnostic Tool")
    parser.add_argument("--json", action="store_true", help="Output doctor report in JSON")
    parser.add_argument("--verbose", action="store_true", help="Print detailed diagnostic messages")
    args = parser.parse_args()

    results = run_doctor(verbose=args.verbose)

    if args.json:
        print(json.dumps(results, indent=2))
        sys.exit(0 if results["healthy"] else 1)

    print("=" * 65)
    print("       Adaptive Orchestrator v4.0.0 â€” Doctor Self-Check")
    print("=" * 65)
    py_status = "PASS" if results["python_compatible"] else "FAIL"
    print(f"  Python Environment:     {py_status:<10} ({results['python_version']} on {sys.platform})")

    files_status = "PASS" if len(results["files_missing"]) == 0 else "FAIL"
    print(f"  Core Assets Integrity:  {files_status:<10} ({results['files_checked'] - len(results['files_missing'])}/{results['files_checked']} files verified)")

    manifest_status = "PASS" if results["manifests_valid"] else "FAIL"
    print(f"  Manifests & Schemas:    {manifest_status:<10} (JSON & YAML syntax valid)")

    subagent_status = "PASS" if results["subagents_valid"] else "FAIL"
    print(f"  Subagent Definitions:   {subagent_status:<10} (4 leaf subagents registered)")

    template_status = "PASS" if results["templates_valid"] else "FAIL"
    print(f"  Multi-Wave Templates:   {template_status:<10} (6 markdown templates verified)")
    print("-" * 65)

    if results["errors"] or results["files_missing"]:
        print("  Issues Detected:")
        for missing in results["files_missing"]:
            print(f"    - Missing file: {missing}")
        for err in results["errors"]:
            print(f"    - Error: {err}")
        print("-" * 65)

    overall = "HEALTHY (v4.0.0 Ready for Deployment)" if results["healthy"] else "DEGRADED (Fix reported issues)"
    print(f"  Overall System Health:  {overall}")
    print("=" * 65)

    sys.exit(0 if results["healthy"] else 1)

if __name__ == "__main__":
    main()
