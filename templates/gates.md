# Stage-Gate Verification Matrix

## Gate 1: Discovery-to-Implementation Gate
- [ ] Root cause / architectural requirement verified with hard evidence.
- [ ] No unaddressed assumptions remain in `CAVEATS`.
- [ ] Explorers terminated (`manage_subagents(Action='kill')`).
- [ ] Implementation scope defined with single writer boundaries.

## Gate 2: Implementation-to-Verification Gate
- [ ] Single writer finished scoped changes.
- [ ] Code builds without errors.
- [ ] Local linters / formatters pass.
- [ ] Git diff contains only relevant files.

## Gate 3: Verification-to-Victory Gate
- [ ] Automated test suite passed.
- [ ] Edge cases and boundary values audited by Challenger.
- [ ] User prompt acceptance criteria completely met.
- [ ] All subagents terminated (Active count = 0).
- [ ] Final orchestration summary prepared.
