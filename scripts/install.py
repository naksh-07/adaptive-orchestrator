#!/usr/bin/env python3
"""
Adaptive Orchestrator -- 1-Command Skill and Native Agent Installer
Installs the Adaptive Orchestrator skill and canonical native agents into:
  Skill:  ~/.gemini/config/skills/adaptive-orchestrator
  Agents: ~/.gemini/config/agents/<agent-name>/agent.md
"""

import os
import sys
import shutil
import argparse
import json

DEFAULT_INSTALL_PATH = os.path.expanduser(os.path.join("~", ".gemini", "config", "skills", "adaptive-orchestrator"))
DEFAULT_AGENTS_PATH = os.path.expanduser(os.path.join("~", ".gemini", "config", "agents"))
DEFAULT_PLUGINS_PATH = os.path.expanduser(os.path.join("~", ".gemini", "config", "plugins", "adaptive-orchestrator"))
DEFAULT_CONFIG_JSON = os.path.expanduser(os.path.join("~", ".gemini", "config", "config.json"))

FILES_TO_INSTALL = [
    "SKILL.md",
    "AGENTS.md",
    "GEMINI.md",
    "manifest.json",
    "plugin.json",
    "skills.json",
    "README.md",
    "LICENSE",
    "pyproject.toml",
]

DIRS_TO_INSTALL = [
    ".agents",
    "orchestrator",
    "subagents",
    "templates",
    "scripts",
    "docs",
]

CANONICAL_AGENTS = [
    "explorer",
    "implementer",
    "reviewer-verifier",
    "challenger-auditor",
]


def install(
    target_dir: str,
    agents_dir: str,
    plugin_dir: str = DEFAULT_PLUGINS_PATH,
    config_json_path: str = DEFAULT_CONFIG_JSON,
    force: bool = False,
    dry_run: bool = False,
    skip_agents: bool = False,
    skip_plugin: bool = False,
) -> int:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    print("=" * 70)
    print("       Adaptive Orchestrator v5.0.0 -- Skill & Native Agent Installer")
    print("=" * 70)
    print(f"Source Directory: {repo_root}")
    print(f"Skill Target:     {target_dir}")
    print(f"Agents Target:    {agents_dir if not skip_agents else '(skipped)'}")
    print(f"Dry Run:          {dry_run}")
    print("-" * 70)

    # 1. Install Skill
    if os.path.exists(target_dir):
        if not force:
            print(f"[!] Target directory '{target_dir}' already exists. Use --force to overwrite.")
            return 1
        print(f"Overwriting existing installation at {target_dir}...")
        if not dry_run:
            shutil.rmtree(target_dir)

    if not dry_run:
        os.makedirs(target_dir, exist_ok=True)

    # Copy files
    for fname in FILES_TO_INSTALL:
        src = os.path.join(repo_root, fname)
        dst = os.path.join(target_dir, fname)
        if os.path.exists(src):
            print(f"Installing file: {fname}")
            if not dry_run:
                shutil.copy2(src, dst)

    # Copy directories
    for dname in DIRS_TO_INSTALL:
        src = os.path.join(repo_root, dname)
        dst = os.path.join(target_dir, dname)
        if os.path.exists(src):
            print(f"Installing directory: {dname}/")
            if not dry_run:
                shutil.copytree(
                    src,
                    dst,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache")
                )

    # 2. Install Canonical Native Agents globally if requested
    if not skip_agents:
        print("-" * 70)
        print("Installing Canonical Native Agents for global Antigravity discovery...")
        for agent_name in CANONICAL_AGENTS:
            src_agent = os.path.join(repo_root, ".agents", "agents", agent_name, "agent.md")
            dst_agent_dir = os.path.join(agents_dir, agent_name)
            dst_agent_file = os.path.join(dst_agent_dir, "agent.md")

            if os.path.exists(src_agent):
                print(f"  Installing native agent '{agent_name}' -> {dst_agent_file}")
                if not dry_run:
                    os.makedirs(dst_agent_dir, exist_ok=True)
                    shutil.copy2(src_agent, dst_agent_file)
            else:
                print(f"  [WARN] Source native agent not found: {src_agent}")

    # 3. Install as Global Antigravity Plugin (~/.gemini/config/plugins/adaptive-orchestrator/)
    if not skip_plugin:
        print("-" * 70)
        print(f"Packaging and installing full Antigravity Plugin -> {plugin_dir}...")
        if os.path.exists(plugin_dir) and not dry_run:
            shutil.rmtree(plugin_dir)

        if not dry_run:
            os.makedirs(plugin_dir, exist_ok=True)
            plugin_skills_dir = os.path.join(plugin_dir, "skills", "adaptive-orchestrator")
            plugin_agents_dir = os.path.join(plugin_dir, "agents")
            plugin_rules_dir = os.path.join(plugin_dir, "rules")

            os.makedirs(plugin_skills_dir, exist_ok=True)
            os.makedirs(plugin_agents_dir, exist_ok=True)
            os.makedirs(plugin_rules_dir, exist_ok=True)

            # Copy plugin.json
            src_plugin_json = os.path.join(repo_root, "plugin.json")
            if os.path.exists(src_plugin_json):
                shutil.copy2(src_plugin_json, os.path.join(plugin_dir, "plugin.json"))

            # Copy rules (AGENTS.md)
            src_agents_md = os.path.join(repo_root, "AGENTS.md")
            if os.path.exists(src_agents_md):
                shutil.copy2(src_agents_md, os.path.join(plugin_rules_dir, "AGENTS.md"))

            # Copy skill files into plugin skills
            shutil.copy2(os.path.join(repo_root, "SKILL.md"), os.path.join(plugin_skills_dir, "SKILL.md"))
            templates_src = os.path.join(repo_root, "templates")
            if os.path.exists(templates_src):
                shutil.copytree(templates_src, os.path.join(plugin_skills_dir, "templates"), dirs_exist_ok=True)

            # Copy canonical agents into plugin agents
            for agent_name in CANONICAL_AGENTS:
                src_agent = os.path.join(repo_root, ".agents", "agents", agent_name, "agent.md")
                dst_agent_dir = os.path.join(plugin_agents_dir, agent_name)
                os.makedirs(dst_agent_dir, exist_ok=True)
                if os.path.exists(src_agent):
                    shutil.copy2(src_agent, os.path.join(dst_agent_dir, "agent.md"))

        print("  [PASS] Plugin packaged with skills, agents, rules, and plugin.json")

        # 4. Enable plugin in config.json
        if os.path.exists(config_json_path):
            print(f"Updating global plugin registry in {config_json_path}...")
            try:
                with open(config_json_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                if "plugins" not in cfg:
                    cfg["plugins"] = {}
                cfg["plugins"]["adaptive-orchestrator"] = {"enabled": True}
                if not dry_run:
                    with open(config_json_path, "w", encoding="utf-8") as f:
                        json.dump(cfg, f, indent=2)
                print("  [PASS] 'adaptive-orchestrator' enabled in config.json plugins registry.")
            except Exception as e:
                print(f"  [WARN] Failed to update config.json: {e}")

    print("-" * 70)
    print(f"[OK] Adaptive Orchestrator installed successfully!")
    print(f"     Skill:  {target_dir}")
    if not skip_agents:
        print(f"     Agents: {agents_dir}")
    if not skip_plugin:
        print(f"     Plugin: {plugin_dir}")
    print("To activate in Antigravity, ensure your config discovers these directories.")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Install Adaptive Orchestrator into Gemini/Antigravity")
    parser.add_argument("--target", default=DEFAULT_INSTALL_PATH, help=f"Skill target directory (default: {DEFAULT_INSTALL_PATH})")
    parser.add_argument("--agents-dir", default=DEFAULT_AGENTS_PATH, help=f"Global agents directory (default: {DEFAULT_AGENTS_PATH})")
    parser.add_argument("--plugin-dir", default=DEFAULT_PLUGINS_PATH, help=f"Global plugins directory (default: {DEFAULT_PLUGINS_PATH})")
    parser.add_argument("--config-json", default=DEFAULT_CONFIG_JSON, help=f"config.json path (default: {DEFAULT_CONFIG_JSON})")
    parser.add_argument("--skip-agents", action="store_true", help="Skip global agent installation")
    parser.add_argument("--skip-plugin", action="store_true", help="Skip global plugin packaging")
    parser.add_argument("--force", action="store_true", help="Overwrite existing installation")
    parser.add_argument("--dry-run", action="store_true", help="Simulate installation without writing files")
    args = parser.parse_args()

    sys.exit(install(
        args.target,
        agents_dir=args.agents_dir,
        plugin_dir=args.plugin_dir,
        config_json_path=args.config_json,
        force=args.force,
        dry_run=args.dry_run,
        skip_agents=args.skip_agents,
        skip_plugin=args.skip_plugin,
    ))


if __name__ == "__main__":
    main()
