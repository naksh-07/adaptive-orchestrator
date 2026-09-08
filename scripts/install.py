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

DEFAULT_INSTALL_PATH = os.path.expanduser(os.path.join("~", ".gemini", "config", "skills", "adaptive-orchestrator"))
DEFAULT_AGENTS_PATH = os.path.expanduser(os.path.join("~", ".gemini", "config", "agents"))

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
    force: bool = False,
    dry_run: bool = False,
    skip_agents: bool = False,
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

    print("-" * 70)
    print(f"[OK] Adaptive Orchestrator successfully installed to: {target_dir}")
    if not skip_agents:
        print(f"[OK] Native agents installed to: {agents_dir}")
    print("To activate in Antigravity or Gemini CLI, ensure your config discovers these directories.")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Install Adaptive Orchestrator into Gemini/Antigravity")
    parser.add_argument("--target", default=DEFAULT_INSTALL_PATH, help=f"Skill target directory (default: {DEFAULT_INSTALL_PATH})")
    parser.add_argument("--agents-dir", default=DEFAULT_AGENTS_PATH, help=f"Global agents directory (default: {DEFAULT_AGENTS_PATH})")
    parser.add_argument("--skip-agents", action="store_true", help="Skip global agent installation")
    parser.add_argument("--force", action="store_true", help="Overwrite existing installation")
    parser.add_argument("--dry-run", action="store_true", help="Simulate installation without writing files")
    args = parser.parse_args()

    sys.exit(install(
        args.target,
        agents_dir=args.agents_dir,
        force=args.force,
        dry_run=args.dry_run,
        skip_agents=args.skip_agents,
    ))


if __name__ == "__main__":
    main()
