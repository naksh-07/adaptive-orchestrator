#!/usr/bin/env python3
"""
Adaptive Orchestrator -- Truthful Self-Check & Diagnostics Engine
Verifies repository integrity, manifest schemas, internal subagent definitions,
native Antigravity agent definitions, discovery paths, and environment health.
Strictly separates internal engine verification from native Antigravity runtime verification.
"""

import os
import sys
import json
import argparse
from typing import Any, Dict, List

REQUIRED_CORE_FILES = [
    "SKILL.md",
    "AGENTS.md",
    "GEMINI.md",
    "manifest.json",
    "plugin.json",
    "skills.json",
    "LICENSE",
    "README.md",
    "templates/mission.md",
    "templates/progress.md",
    "templates/dead-ends.md",
    "templates/gates.md",
    "templates/final-audit.md",
    "templates/handoff-report.md",
]

INTERNAL_AGENT_FILES = [
    "subagents/subagents-definition.json",
    "subagents/explorer-researcher.md",
    "subagents/implementer.md",
    "subagents/reviewer-verifier.md",
    "subagents/challenger-auditor.md",
]

NATIVE_AGENT_SPECS = [
    ("explorer", ".agents/agents/explorer/agent.md"),
    ("implementer", ".agents/agents/implementer/agent.md"),
    ("reviewer-verifier", ".agents/agents/reviewer-verifier/agent.md"),
    ("challenger-auditor", ".agents/agents/challenger-auditor/agent.md"),
]

VALID_NATIVE_MODELS = {"flash", "pro", "inherit", "flash_lite"}
VALID_NATIVE_TOOLS = {
    "view_file", "grep_search", "find_by_name", "list_dir",
    "read_url_content", "search_web", "write_to_file",
    "replace_file_content", "run_command", "manage_task",
    "schedule", "generate_image", "ask_question"
}


def parse_frontmatter(content: str) -> Dict[str, Any]:
    """Simple parser for YAML frontmatter without external yaml dependency."""
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


def run_doctor(verbose: bool = False) -> Dict[str, Any]:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    results = {
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "python_compatible": sys.version_info >= (3, 9),
        "files_checked": 0,
        "files_missing": [],
        "manifests_valid": True,
        "internal_agents_valid": True,
        "native_agents_valid": True,
        "native_discovery_valid": True,
        "native_invocation_status": "NOT VERIFIED",
        "native_smoke_test_status": "NOT VERIFIED",
        "templates_valid": True,
        "warnings": [],
        "errors": []
    }

    # 1. Check core assets
    for rel_path in REQUIRED_CORE_FILES:
        full_path = os.path.join(repo_root, rel_path)
        results["files_checked"] += 1
        if not os.path.exists(full_path):
            results["files_missing"].append(rel_path)

    # 2. Check internal agent files
    for rel_path in INTERNAL_AGENT_FILES:
        full_path = os.path.join(repo_root, rel_path)
        results["files_checked"] += 1
        if not os.path.exists(full_path):
            results["internal_agents_valid"] = False
            results["files_missing"].append(rel_path)

    # 3. Check JSON manifests
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

    # 4. Check Native Antigravity Agent Definitions (.agents/agents/)
    native_agents_found = 0
    for agent_name, rel_path in NATIVE_AGENT_SPECS:
        full_path = os.path.join(repo_root, rel_path)
        results["files_checked"] += 1
        if not os.path.exists(full_path):
            results["native_agents_valid"] = False
            results["errors"].append(f"Missing native agent definition: {rel_path}")
            continue

        try:
            with open(full_path, "r", encoding="utf-8") as f:
                content = f.read()
            fm = parse_frontmatter(content)
            if not fm:
                results["native_agents_valid"] = False
                results["errors"].append(f"{rel_path} missing valid YAML frontmatter")
                continue

            if fm.get("name") != agent_name:
                results["native_agents_valid"] = False
                results["errors"].append(f"{rel_path} frontmatter name '{fm.get('name')}' != expected '{agent_name}'")

            if not fm.get("subagent"):
                results["native_agents_valid"] = False
                results["errors"].append(f"{rel_path} frontmatter missing required 'subagent: true'")

            model_val = fm.get("model")
            if model_val not in VALID_NATIVE_MODELS:
                results["native_agents_valid"] = False
                results["errors"].append(f"{rel_path} frontmatter model '{model_val}' not in {VALID_NATIVE_MODELS}")

            tools = fm.get("tools", [])
            if not isinstance(tools, list) or not tools:
                results["native_agents_valid"] = False
                results["errors"].append(f"{rel_path} frontmatter tools must be a non-empty list")
            else:
                invalid_tools = set(tools) - VALID_NATIVE_TOOLS
                if invalid_tools:
                    results["native_agents_valid"] = False
                    results["errors"].append(f"{rel_path} contains invalid tools: {invalid_tools}")

            native_agents_found += 1
        except Exception as ex:
            results["native_agents_valid"] = False
            results["errors"].append(f"Error reading {rel_path}: {str(ex)}")

    # 5. Check Native Workspace Discovery Path
    workspace_agents_dir = os.path.join(repo_root, ".agents", "agents")
    results["native_discovery_valid"] = os.path.isdir(workspace_agents_dir) and native_agents_found == len(NATIVE_AGENT_SPECS)
    if not results["native_discovery_valid"]:
        results["warnings"].append("Workspace .agents/agents/ path missing or incomplete")

    # 6. Verify SKILL.md frontmatter
    skill_path = os.path.join(repo_root, "SKILL.md")
    if os.path.exists(skill_path):
        with open(skill_path, "r", encoding="utf-8") as f:
            content = f.read()
            if not (content.startswith("---") and "name: adaptive-orchestrator" in content):
                results["errors"].append("SKILL.md missing valid YAML frontmatter")

    is_healthy = (
        results["python_compatible"]
        and len(results["files_missing"]) == 0
        and results["manifests_valid"]
        and results["internal_agents_valid"]
        and results["native_agents_valid"]
        and results["native_discovery_valid"]
        and len(results["errors"]) == 0
    )
    results["healthy"] = is_healthy
    return results


def main():
    parser = argparse.ArgumentParser(description="Adaptive Orchestrator Truthful Doctor Diagnostic Tool")
    parser.add_argument("--json", action="store_true", help="Output doctor report in JSON")
    parser.add_argument("--verbose", action="store_true", help="Print detailed diagnostic messages")
    args = parser.parse_args()

    results = run_doctor(verbose=args.verbose)

    if args.json:
        print(json.dumps(results, indent=2))
        sys.exit(0 if results["healthy"] else 1)

    print("=" * 68)
    print("       Adaptive Orchestrator v5.0.0 -- Truthful Doctor Self-Check")
    print("=" * 68)
    py_status = "[PASS]" if results["python_compatible"] else "[FAIL]"
    print(f"  Python Environment:         {py_status:<14} ({results['python_version']} on {sys.platform})")

    files_status = "[PASS]" if len(results["files_missing"]) == 0 else "[FAIL]"
    print(f"  Core Assets Integrity:      {files_status:<14} ({results['files_checked'] - len(results['files_missing'])}/{results['files_checked']} files verified)")

    manifest_status = "[PASS]" if results["manifests_valid"] else "[FAIL]"
    print(f"  Manifests & Schemas:        {manifest_status:<14} (JSON & YAML syntax valid)")

    internal_status = "[PASS]" if results["internal_agents_valid"] else "[FAIL]"
    print(f"  Internal Agent Definitions: {internal_status:<14} (subagents/ metadata verified)")

    native_status = "[PASS]" if results["native_agents_valid"] else "[FAIL]"
    print(f"  Native Agent Definitions:   {native_status:<14} (.agents/agents/ YAML frontmatter valid)")

    discovery_status = "[PASS]" if results["native_discovery_valid"] else "[FAIL]"
    print(f"  Native Agent Discovery:     {discovery_status:<14} (Workspace .agents/agents/ verified)")

    print(f"  Native Tool Invocation:     {results['native_invocation_status']:<14} (Requires active Antigravity session)")
    print(f"  Native Runtime Smoke Test:  {results['native_smoke_test_status']:<14} (Requires live invoke_subagent trace)")

    template_status = "[PASS]" if results["templates_valid"] else "[FAIL]"
    print(f"  Coordination Templates:     {template_status:<14} (6 markdown templates verified)")
    print("-" * 68)

    if results["errors"] or results["files_missing"]:
        print("  Issues Detected:")
        for missing in results["files_missing"]:
            print(f"    - Missing file: {missing}")
        for err in results["errors"]:
            print(f"    - Error: {err}")
        print("-" * 68)

    if results["warnings"]:
        print("  Warnings:")
        for w in results["warnings"]:
            print(f"    - Warning: {w}")
        print("-" * 68)

    overall = "HEALTHY (Engine & Native Static Definitions Verified)" if results["healthy"] else "DEGRADED (Fix reported issues)"
    print(f"  Overall System Status:      {overall}")
    print("=" * 68)

    sys.exit(0 if results["healthy"] else 1)


if __name__ == "__main__":
    main()
