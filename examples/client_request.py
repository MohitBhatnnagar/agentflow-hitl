"""Example: a client request flows through intake -> draft -> quality check -> send.

Intake and quality check run unattended. Sending to a client needs a human.
Run:  PYTHONPATH=src python examples/client_request.py
"""
from agentflow import AuditLog, Mode, Step, Store, Workflow


def intake(_):
    return {"client": "Acme", "ask": "Redesign the Q3 board deck"}, 0.97


def draft(ctx):
    return f"Draft reply to {ctx['intake']['client']}: we can deliver in 3 days.", 0.92


def quality_check(ctx):
    ok = "deliver" in ctx["draft"]
    return {"passed": ok}, 0.9


def send(ctx):
    return f"SENT: {ctx['draft']}", 0.99


store, audit = Store(), AuditLog("audit.jsonl")
wf = (Workflow("client-request", store, audit)
      .add(Step("intake", intake))
      .add(Step("draft", draft, deps=("intake",)))
      .add(Step("quality_check", quality_check, deps=("draft",)))
      .add(Step("send", send, deps=("quality_check",), mode=Mode.APPROVE)))

run = wf.start("demo-1")
print("status:", run.status, "waiting on:", run.waiting_on)
wf.approve("demo-1", "send", approver="ops.lead")
run = wf.resume("demo-1")
print("status:", run.status, "->", run.results["send"])
print("audit intact:", audit.verify())
