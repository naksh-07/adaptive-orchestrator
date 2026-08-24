# Dead-End Memory Log

> **Purpose**: Maintain an append-only record of falsified hypotheses, non-viable solutions, and failed approaches to prevent subagents from looping or repeating mistakes across waves.

| # | Hypothesis / Approach | Result | Reason for Rejection | Evidence / Error Log | Wave |
|:---:|:---|:---:|:---|:---|:---:|
| 1 | Attempted direct state mutation in component X | Falsified | React immutable state violation | `Error: Cannot assign to read-only property` | Wave 1 |
| 2 | Using legacy endpoint `/api/v1/auth` | Falsified | Endpoint deprecated in v3 | HTTP 404 response | Wave 1 |
| 3 | Patching file Y directly without lockfile update | Falsified | Integrity checksum failure | `yarn install --check-cache` failed | Wave 3 |
