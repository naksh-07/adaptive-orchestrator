#!/usr/bin/env python3
"""
Adaptive Orchestrator -- Skill, Manifest & Native Agent Schema Validator
Validates manifest references, internal subagent descriptors, canonical native
Antigravity agent definitions, and template contracts.
Strictly separates static file validation from runtime integration proof.
"""

import os
import sys
import json
from typing import Any, Dict, List, Set

VALID_NATIVE_MODELS = {"flash", "pro", "inherit", "flash_lite"}
VALID_NATIVE_TOOLS = {
    "view_file", "grep_search", "find_by_name", "list_dir",
    "read_url_content", "search_web", "write_to_file",
    "replace_file_content", "run_command", "manage_task",
    "schedule", "generate_image", "ask_question"
}

EXPECTED_NATIVE_AGENTS = {
    "explorer": {"model": "flash", "write": False},
    "implementer": {"model": "pro", "write": True},
    "reviewer-verifier": {"model": "flash", "write": False},
    "challenger-auditor": {"model": "pro", "write": False},
}


def parse_frontmatter(content: str) -> Dict[str, Any]:
    """Parses YAML frontmatter safely without third-party dependencies."""
    if not content.startswith("---"):
        return {}
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}
    raw_yaml = parts[1]
    data: Dict[str, Any] = {}
    current_list_key = None
    for line in raw_yaml.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("- ") and current_list_key:
            data[current_list_key].append(line[2:].strip().strip("\"'"))
            continue
        current_list_key = None
        if ":" in line:
            k, v = line.split(":", 1)
            k = k.strip()
            v = v.strip().strip("\"'")
            if not v:
                data[k] = []
                current_list_key = k
            elif v.lower() == "true":
                data[k] = True
            elif v.lower() == "false":
                data[k] = False
            else:
                data[k] = v
    return data


def validate() -> int:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    errors: List[str] = []

    print("[1/6] Validating manifest.json...")
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

    print("[2/6] Validating plugin.json & skills.json...")
    for f_name in ["plugin.json", "skills.json"]:
        f_path = os.path.join(repo_root, f_name)
        if not os.path.exists(f_path):
            errors.append(f"{f_name} not found")
        else:
            with open(f_path, "r", encoding="utf-8") as f:
                json.load(f)

    print("[3/6] Validating subagents-definition.json (Internal Registry)...")
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

    print("[4/6] Validating Canonical Native Agent Definitions (.agents/agents/)...")
    agents_dir = os.path.join(repo_root, ".agents", "agents")
    if not os.path.exists(agents_dir):
        errors.append(f"Canonical native agent directory missing: {agents_dir}")
    else:
        discovered_names: Set[str] = set()
        for expected_name, spec in EXPECTED_NATIVE_AGENTS.items():
            agent_md_path = os.path.join(agents_dir, expected_name, "agent.md")
            if not os.path.exists(agent_md_path):
                errors.append(f"Missing native agent definition: .agents/agents/{expected_name}/agent.md")
                continue

            with open(agent_md_path, "r", encoding="utf-8") as f:
                content = f.read()
            fm = parse_frontmatter(content)
            if not fm:
                errors.append(f"{expected_name}/agent.md missing valid YAML frontmatter")
                continue

            name = fm.get("name")
            if name != expected_name:
                errors.append(f"{expected_name}/agent.md has name '{name}', expected '{expected_name}'")
            if name in discovered_names:
                errors.append(f"Duplicate native agent name: '{name}'")
            discovered_names.add(name)

            if not fm.get("description"):
                errors.append(f"{expected_name}/agent.md missing 'description'")

            if fm.get("subagent") is not True:
                errors.append(f"{expected_name}/agent.md missing required 'subagent: true'")

            model = fm.get("model")
            if model not in VALID_NATIVE_MODELS:
                errors.append(f"{expected_name}/agent.md model '{model}' not in supported values: {VALID_NATIVE_MODELS}")

            tools = fm.get("tools", [])
            if not isinstance(tools, list) or not tools:
                errors.append(f"{expected_name}/agent.md tools must be a non-empty list")
            else:
                invalid_tools = set(tools) - VALID_NATIVE_TOOLS
                if invalid_tools:
                    errors.append(f"{expected_name}/agent.md declares invalid tools: {invalid_tools}")

    print("[5/6] Validating SKILL.md, AGENTS.md, & GEMINI.md content & delegation gates...")
    skill_path = os.path.join(repo_root, "SKILL.md")
    with open(skill_path, "r", encoding="utf-8") as f:
        skill_content = f.read()
        required_phrases = [
            "DUAL MANDATORY DELEGATION GATES",
            "Phase 1: Pre-Planning Dispatch Gate",
            "Phase 2: Post-Approval Execution Dispatch Gate",
            "READ PARALLEL",
            "Dynamic DAG",
            "Reusable Domain Workers",
            "AIMD",
            "4-Tier Verification Pyramid",
            "ZERO DIRECT TOOL EXECUTION",
            "MANDATORY"
        ]
        for phrase in required_phrases:
            if phrase not in skill_content:
                errors.append(f"SKILL.md missing critical section: '{phrase}'")
                
    for rule_file in ["AGENTS.md", "GEMINI.md"]:
        rule_path = os.path.join(repo_root, rule_file)
        if os.path.exists(rule_path):
            with open(rule_path, "r", encoding="utf-8") as f:
                content = f.read()
                if "ZERO DIRECT WORK INVARIANT" not in content:
                    errors.append(f"{rule_file} missing required phrase: 'ZERO DIRECT WORK INVARIANT'")
        else:
            errors.append(f"{rule_file} not found")

    print("[6/6] Checking coordination template formatting...")
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
            print(f"  [FAIL] {err}")
        return 1

    print("\n[OK] All skill manifests, internal descriptors, native agent definitions, and templates validated successfully!")
    print("NOTE: Static validation proves file structure and syntax. Native Antigravity discovery and invocation must be verified at runtime.")
    return 0


def main():
    sys.exit(validate())


if __name__ == "__main__":
    main()
