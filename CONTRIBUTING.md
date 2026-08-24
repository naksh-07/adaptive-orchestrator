# Contributing to Adaptive Orchestrator

Thank you for your interest in contributing to **Adaptive Orchestrator (v4 Foundation)**!

## Principles
1. **Efficiency First**: We prioritize useful progress per credit/token over inflating subagent counts.
2. **Zero Workspace Pollution**: All coordination logs, memory tables, and ledgers belong in `<appDataDir>/brain/<conversation-id>/`, never in the user workspace.
3. **Single Writer Default**: Concurrency is for reading and testing; writing is controlled and scoped.
4. **Independent Verification**: Implementations must be verified by distinct verifiers or test suites.

## Development Setup

```bash
# Clone repository
git clone https://github.com/naksh-07/adaptive-orchestrator.git
cd adaptive-orchestrator

# Run diagnostics
python scripts/doctor.py

# Run test suite
python -m unittest discover -s tests -p "test_*.py" -v

# Run schema validation
python scripts/validate_skill.py
```

## Submitting Pull Requests
1. Fork the repo and create a feature branch (`git checkout -b feature/my-feature`).
2. Ensure all tests pass (`python -m unittest discover -s tests -v`).
3. Commit with clear conventional commit messages (`feat: ...`, `fix: ...`, `docs: ...`).
4. Submit a Pull Request.
