#!/usr/bin/env python3
"""
Adaptive Orchestrator â€” 1-Command Skill Installer
Installs the Adaptive Orchestrator skill into the local Gemini/Antigravity
skills directory: ~/.gemini/config/skills/adaptive-orchestrator
"""

import os
import sys
import shutil
import argparse

DEFAULT_INSTALL_PATH = os.path.expanduser(os.path.join("~", ".gemini", "config", "skills", "adaptive-orchestrator"))

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
    "orchestrator",
    "subagents",
    "templates",
    "scripts",
    "docs",
]

def install(target_dir: str, force: bool = False, dry_run: bool = False) -> int:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    print("=" * 65)
    print("       Adaptive Orchestrator v5.0.0 — Skill Installer")
    print("=" * 65)
    print(f"Source Directory: {repo_root}")
    print(f"Target Directory: {target_dir}")
    print(f"Dry Run:          {dry_run}")
    print("-" * 65)

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

    print("-" * 65)
    print(f"[OK] Adaptive Orchestrator successfully installed to: {target_dir}")
    print("To activate in Antigravity or Gemini CLI, ensure your config discovers this directory.")
    return 0

def main():
    parser = argparse.ArgumentParser(description="Install Adaptive Orchestrator into Gemini/Antigravity")
    parser.add_argument("--target", default=DEFAULT_INSTALL_PATH, help=f"Target directory (default: {DEFAULT_INSTALL_PATH})")
    parser.add_argument("--force", action="store_true", help="Overwrite existing installation")
    parser.add_argument("--dry-run", action="store_true", help="Simulate installation without writing files")
    args = parser.parse_args()

    sys.exit(install(args.target, force=args.force, dry_run=args.dry_run))

if __name__ == "__main__":
    main()
