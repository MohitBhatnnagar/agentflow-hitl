# Architecture

## Components

```
            +-----------+      +--------------+
 step fn -->|  Workflow |----->|    Policy    |  decide(mode, confidence, cost)
 (result,   |  (engine) |      +--------------+
 confidence)|           |----->|    Store     |  SQLite: runs + approvals
            |           |      +--------------+
            |           |----->|   AuditLog   |  append-only, hash-chained JSONL
            +-----------+      +--------------+
```

| Module | Responsibility |
|---|---|
| `engine.py` | Orders steps, runs them with retries, applies the policy, pauses and resumes. |
| `policy.py` | Pure function from (declared mode, confidence, cost) to a decision. No I/O, so it is trivially testable. |
| `store.py` | Durable run state and approval records. |
| `audit.py` | Evidence: who/what/when, tamper-evident. |
| `cli.py` | Operator tooling around the store and the log. |

## Run lifecycle

1. `start(run_id)` records `run_started`, then walks steps in topological order.
2. For each step the engine consults the **gate**: declared mode plus cost ceiling. `APPROVE` creates a pending approval and the step is skipped for now. `ESCALATE` does the same but is labelled so an owner picks it up.
3. An unattended step runs with retries. Its returned confidence is checked **after** the run: if too low, the output is stored as `{"held": true, ...}` and an approval is requested. Downstream steps do not see held output.
4. `approve(...)` records the decision in the store and in the audit log. `resume(run_id)` reloads saved results and continues. A held output is released on approval, or the run fails on rejection.
5. The final status is `completed`, `waiting` or `failed`, persisted with the results.

## Key decisions

- **Autonomy only ratchets toward humans.** The policy can raise a step to `APPROVE` or `ESCALATE` but never lowers a declared mode. That makes the declared mode a safe ceiling that reviewers can reason about.
- **Hold, don't discard.** Throwing away low-confidence work wastes it, and shipping it is unsafe. Holding it lets a reviewer approve in seconds.
- **Confidence is an input, not a feature of the engine.** The engine does not try to compute it. This keeps it model-agnostic, and lets teams plug in validators, rubrics or judge models.
- **Hash-chained log instead of a plain log.** Auditors care less about volume than about whether records could have been edited. A chain gives cheap, verifiable integrity without a database.
- **Time is injectable** (`sleep` parameter), so retry tests run instantly.

## Known limitations and next steps

- Steps run sequentially in one process. Independent branches could run concurrently (thread pool or asyncio) once results are keyed safely.
- The audit chain proves *integrity*, not *authenticity*: someone able to rewrite the whole file could rebuild a valid chain. Anchoring the head hash externally (or signing records) would close that.
- Approvals are identified by a free-text approver name. A real deployment would bind this to an identity provider and role checks.
- No timeout enforcement per step yet.
