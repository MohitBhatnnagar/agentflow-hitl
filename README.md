# agentflow-hitl

A small, dependency-free workflow engine for **AI agents that must know when to stop and ask a human**.

Most agent frameworks answer "how do I chain LLM calls?". This one answers the operations question that comes next: *which steps may the agent run alone, which need approval, which must go to a person, and can I prove afterwards what happened?*

- **Autonomy policy** with three modes (auto / approve / escalate). Low model confidence or high cost can only move a step toward *more* human involvement, never less.
- **Approval gates that survive restarts.** Paused runs and approvals are stored in SQLite, so a reviewer can approve tomorrow from a different process.
- **Held outputs.** If a step runs but its confidence is too low to ship, the output is kept and held for review instead of being thrown away or sent on.
- **Retries with exponential backoff**, DAG ordering (Kahn's algorithm) with cycle and unknown-dependency detection.
- **Tamper-evident audit log.** A hash-chained JSONL file; `agentflow verify` pinpoints the first altered record.
- Pure Python 3.10+, standard library only. 20 unit tests.

## Quick start

```bash
pip install -e .
PYTHONPATH=src python examples/client_request.py
```

```python
from agentflow import AuditLog, Mode, Step, Store, Workflow

wf = (Workflow("client-request", Store(), AuditLog("audit.jsonl"))
      .add(Step("draft", draft_fn))                                   # runs unattended
      .add(Step("send", send_fn, deps=("draft",), mode=Mode.APPROVE)))  # waits for a human

run = wf.start("run-1")                    # status == "waiting", waiting_on == ["send"]
wf.approve("run-1", "send", approver="ops.lead")
run = wf.resume("run-1")                   # status == "completed"
```

A step function takes the results of its dependencies and returns `(result, confidence)`. The confidence comes from your model, a rubric, or a validator; the engine only decides what to do with it.

## The policy in one table

| Declared mode | Confidence ≥ 0.80 | 0.40 ≤ c < 0.80 | c < 0.40 |
|---|---|---|---|
| `AUTO` | runs | runs, **output held** for review | escalated |
| `APPROVE` | waits for approval | waits for approval | escalated |
| `ESCALATE` | handed to an owner, never executed by the agent | same | same |

Thresholds and the unattended cost ceiling are configurable via `Policy(...)`.

## CLI

```bash
agentflow verify audit.jsonl     # exit 0 if the chain is intact, 2 if tampered
agentflow tail audit.jsonl -n 20
agentflow pending runs.db        # approvals waiting on a human
```

## Tests

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

The suite covers policy edge cases, DAG ordering and cycles, retries, approval and rejection, held outputs, resuming from a *new* process object, and tamper detection.

## Design notes and limits

See [ARCHITECTURE.md](ARCHITECTURE.md). Honest limitations: single process, synchronous steps, results must be JSON-serialisable, and no concurrent execution of independent branches yet.

## License

MIT
