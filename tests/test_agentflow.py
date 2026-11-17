import json
import os
import tempfile
import unittest

from agentflow import AuditLog, CycleError, Mode, Policy, Step, Store, Workflow


def make(policy=None):
    d = tempfile.mkdtemp()
    audit = AuditLog(os.path.join(d, "a.jsonl"))
    return Workflow("t", Store(), audit, policy, sleep=lambda s: None), audit


class PolicyTests(unittest.TestCase):
    def test_high_confidence_auto_stays_auto(self):
        self.assertEqual(Policy().decide(Mode.AUTO, 0.95).mode, Mode.AUTO)

    def test_low_confidence_moves_auto_to_approve(self):
        self.assertEqual(Policy().decide(Mode.AUTO, 0.6).mode, Mode.APPROVE)

    def test_very_low_confidence_escalates_any_mode(self):
        self.assertEqual(Policy().decide(Mode.APPROVE, 0.2).mode, Mode.ESCALATE)

    def test_cost_over_ceiling_needs_approval(self):
        self.assertEqual(Policy(max_auto_cost=10).decide(Mode.AUTO, 0.99, cost=11).mode, Mode.APPROVE)

    def test_declared_approve_is_never_downgraded(self):
        self.assertEqual(Policy().decide(Mode.APPROVE, 1.0).mode, Mode.APPROVE)

    def test_invalid_confidence_rejected(self):
        with self.assertRaises(ValueError):
            Policy().decide(Mode.AUTO, 1.5)


class DagTests(unittest.TestCase):
    def test_topological_order(self):
        wf, _ = make()
        wf.add(Step("c", lambda c: (1, 1), deps=("a", "b")))
        wf.add(Step("b", lambda c: (1, 1), deps=("a",)))
        wf.add(Step("a", lambda c: (1, 1)))
        self.assertEqual(wf.order(), ["a", "b", "c"])

    def test_cycle_detected(self):
        wf, _ = make()
        wf.add(Step("a", lambda c: (1, 1), deps=("b",)))
        wf.add(Step("b", lambda c: (1, 1), deps=("a",)))
        with self.assertRaises(CycleError):
            wf.order()

    def test_unknown_dependency(self):
        wf, _ = make()
        wf.add(Step("a", lambda c: (1, 1), deps=("zzz",)))
        with self.assertRaises(ValueError):
            wf.order()

    def test_duplicate_step_rejected(self):
        wf, _ = make()
        wf.add(Step("a", lambda c: (1, 1)))
        with self.assertRaises(ValueError):
            wf.add(Step("a", lambda c: (1, 1)))


class RunTests(unittest.TestCase):
    def test_happy_path_passes_results_downstream(self):
        wf, _ = make()
        wf.add(Step("a", lambda c: (2, 0.95)))
        wf.add(Step("b", lambda c: (c["a"] * 10, 0.95), deps=("a",)))
        r = wf.start("r1")
        self.assertEqual((r.status, r.results["b"]), ("completed", 20))

    def test_retry_then_success(self):
        calls = {"n": 0}

        def flaky(_):
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("boom")
            return "ok", 0.95

        wf, _ = make()
        wf.add(Step("a", flaky, retries=2))
        self.assertEqual(wf.start("r2").status, "completed")
        self.assertEqual(calls["n"], 3)

    def test_failure_after_retries(self):
        wf, _ = make()
        wf.add(Step("a", lambda c: 1 / 0, retries=1))
        r = wf.start("r3")
        self.assertEqual(r.status, "failed")
        self.assertIn("a", r.error)

    def test_approval_gate_pauses_then_resumes(self):
        wf, _ = make()
        wf.add(Step("a", lambda c: ("draft", 0.95)))
        wf.add(Step("send", lambda c: ("sent " + c["a"], 0.99), deps=("a",), mode=Mode.APPROVE))
        r = wf.start("r4")
        self.assertEqual((r.status, r.waiting_on), ("waiting", ["send"]))
        wf.approve("r4", "send", "ops.lead")
        r = wf.resume("r4")
        self.assertEqual((r.status, r.results["send"]), ("completed", "sent draft"))

    def test_rejection_fails_run(self):
        wf, _ = make()
        wf.add(Step("send", lambda c: ("x", 1.0), mode=Mode.APPROVE))
        wf.start("r5")
        wf.approve("r5", "send", "ops.lead", approved=False)
        self.assertEqual(wf.resume("r5").status, "failed")

    def test_low_confidence_output_is_held_not_shipped(self):
        wf, _ = make()
        wf.add(Step("a", lambda c: ("shaky", 0.5)))
        wf.add(Step("b", lambda c: ("after", 0.99), deps=("a",)))
        r = wf.start("r6")
        self.assertEqual((r.status, r.waiting_on), ("waiting", ["a"]))
        self.assertNotIn("b", r.results)
        wf.approve("r6", "a", "reviewer")
        r = wf.resume("r6")
        self.assertEqual((r.status, r.results["a"], r.results["b"]), ("completed", "shaky", "after"))

    def test_escalated_step_never_executes(self):
        ran = []
        wf, _ = make()
        wf.add(Step("legal", lambda c: ran.append(1) or ("x", 1.0), mode=Mode.ESCALATE))
        r = wf.start("r7")
        self.assertEqual(r.status, "waiting")
        self.assertEqual(ran, [])

    def test_pending_survives_new_workflow_object(self):
        store = Store()
        audit = AuditLog(os.path.join(tempfile.mkdtemp(), "a.jsonl"))
        build = lambda: Workflow("t", store, audit, sleep=lambda s: None).add(
            Step("send", lambda c: ("ok", 1.0), mode=Mode.APPROVE))
        build().start("r8")
        build().approve("r8", "send", "ops")
        self.assertEqual(build().resume("r8").status, "completed")
        self.assertEqual(store.pending(), [])


class AuditTests(unittest.TestCase):
    def test_chain_verifies(self):
        wf, audit = make()
        wf.add(Step("a", lambda c: (1, 0.95)))
        wf.start("r9")
        self.assertEqual(audit.verify(), (True, None))

    def test_tampering_is_detected(self):
        wf, audit = make()
        wf.add(Step("a", lambda c: (1, 0.95)))
        wf.start("r10")
        lines = audit.path.read_text().splitlines()
        rec = json.loads(lines[1])
        rec["event"] = "step_forged"
        lines[1] = json.dumps(rec, sort_keys=True)
        audit.path.write_text("\n".join(lines) + "\n")
        ok, bad = audit.verify()
        self.assertFalse(ok)
        self.assertEqual(bad, 1)


if __name__ == "__main__":
    unittest.main()
