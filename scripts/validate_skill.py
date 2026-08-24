#!/usr/bin/env python3
"""
Adaptive Orchestrator — Skill & Manifest Schema Validator
Validates manifest references, subagent descriptors, and template contracts.
"""

import os
import sys
import json

def validate() -> int:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    errors = []

    print("[1/5] Validating manifest.json...")
    manifest_path = os.path.join(repo_root, "manifest.json")
    if not os.path.exists(manifest_path):
        errors.append("manifest.json not found")
    else:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
            for sub in manifest.get("subagents", []):
                sub_path = os.path.join(repo_root, sub)
                if not os.path.exists(sub_path):
                    errors.append(f"manifest.json references missing subagent file: {sub}")
            for tmpl in manifest.get("templates", []):
                tmpl_path = os.path.join(repo_root, tmpl)
                if not os.path.exists(tmpl_path):
                    errors.append(f"manifest.json references missing template file: {tmpl}")

    print("[2/5] Validating plugin.json & skills.json...")
    for f_name in ["plugin.json", "skills.json"]:
        f_path = os.path.join(repo_root, f_name)
        if not os.path.exists(f_path):
            errors.append(f"{f_name} not found")
        else:
            with open(f_path, "r", encoding="utf-8") as f:
                json.load(f)

    print("[3/5] Validating subagents-definition.json...")
    subdefs_path = os.path.join(repo_root, "subagents", "subagents-definition.json")
    if not os.path.exists(subdefs_path):
        errors.append("subagents-definition.json not found")
    else:
        with open(subdefs_path, "r", encoding="utf-8") as f:
            subdefs = json.load(f)
            agents = subdefs.get("subagents", [])
            expected = {"explorer-researcher", "implementer", "reviewer-verifier", "challenger-auditor"}
            actual = {a.get("name") for a in agents}
            if expected != actual:
                errors.append(f"subagents-definition.json expected {expected}, got {actual}")

    print("[4/5] Validating SKILL.md content & gates...")
    skill_path = os.path.join(repo_root, "SKILL.md")
    with open(skill_path, "r", encoding="utf-8") as f:
        skill_content = f.read()
        required_phrases = [
            "DUAL MANDATORY DELEGATION GATES",
            "Phase 1: Pre-Planning Dispatch Gate",
            "Phase 2: Post-Approval Execution Dispatch Gate",
            "READ PARALLEL",
            "SPAWNED_TOTAL",
            "ACTIVE_TOTAL"
        ]
        for phrase in required_phrases:
            if phrase not in skill_content:
                errors.append(f"SKILL.md missing critical section: '{phrase}'")

    print("[5/5] Checking template formatting...")
    template_files = [
        "mission.md", "progress.md", "dead-ends.md",
        "gates.md", "final-audit.md", "handoff-report.md"
    ]
    for tmpl in template_files:
        p = os.path.join(repo_root, "templates", tmpl)
        if not os.path.exists(p):
            errors.append(f"Missing template: templates/{tmpl}")

    if errors:
        print("\nValidation FAILED with errors:")
        for err in errors:
            print(f"  [X] {err}")
        return 1

    print("\n[OK] All skill manifests, subagents, and templates validated successfully!")
    return 0

def main():
    sys.exit(validate())

if __name__ == "__main__":
    main()
