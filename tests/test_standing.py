"""Standing work orchestration tests use SQLite and synthetic workers only."""
import concurrent.futures
import copy
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from office.standing import StandingOperations, STATE_KEY


ROOT = Path(__file__).resolve().parents[1]
DAY = "2026-09-30"


class FakeEngine:
    def __init__(self, root, database):
        self.root = root
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        self.db = sqlite3.connect(database, check_same_thread=False)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
            CREATE TABLE IF NOT EXISTS missions(id TEXT PRIMARY KEY,request_id TEXT UNIQUE,data TEXT);
        """)
        self.config = {"daily_runs": 10}
        self._quota_blocked = False
        self.closed = False
        self.used = 0
        self.calls = []
        self.events = []
        self.after_commit_failure = False

    def _all(self, table):
        return [json.loads(row[0]) for row in self.db.execute(f"SELECT data FROM {table}")]

    def _get(self, table, record_id):
        row = self.db.execute(f"SELECT data FROM {table} WHERE id=?", (record_id,)).fetchone()
        if not row:
            raise KeyError(record_id)
        return json.loads(row[0])

    def _daily_used(self):
        return self.used

    def _event(self, *args):
        self.events.append(args)
        self.changed.notify_all()

    def submit(self, payload):
        with self.changed, self.db:
            row = self.db.execute("SELECT data FROM missions WHERE request_id=?", (payload["request_id"],)).fetchone()
            if row:
                mission = json.loads(row[0])
                if mission["text"] != payload["text"]:
                    raise ValueError("Idempotence payload mismatch")
                return {"mission": mission, "duplicate": True}
            self.calls.append(copy.deepcopy(payload))
            mission = {**payload, "id": payload["request_id"], "status": "queued", "result": None}
            self.db.execute("INSERT INTO missions VALUES(?,?,?)", (mission["id"], mission["request_id"], json.dumps(mission)))
        if self.after_commit_failure:
            self.after_commit_failure = False
            raise RuntimeError("Synthetic interruption after committed submit")
        return {"mission": mission, "duplicate": False}

    def finish(self, mission_id, status="review", result=None):
        with self.changed, self.db:
            mission = self._get("missions", mission_id)
            mission.update(status=status, result=result or {"summary": "Synthetic report; not a business result."})
            self.db.execute("UPDATE missions SET data=? WHERE id=?", (json.dumps(mission), mission_id))

    def set_prototype_receipt(self, mission_id, receipt):
        with self.changed, self.db:
            mission = self._get("missions", mission_id)
            mission["prototype"] = copy.deepcopy(receipt)
            self.db.execute("UPDATE missions SET data=? WHERE id=?", (json.dumps(mission), mission_id))


class StandingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "config").mkdir()
        self.policy = json.loads((ROOT / "config/standing.json").read_text(encoding="utf-8"))
        self.write_policy()
        self.database = self.root / "org.sqlite3"
        self.engine = FakeEngine(self.root, self.database)
        self.addCleanup(lambda: self.engine.db.close())
        self.standing = StandingOperations(self.engine)

    def write_policy(self):
        (self.root / "config/standing.json").write_text(json.dumps(self.policy), encoding="utf-8")

    def finish_specialists(self, snapshot, statuses=None):
        statuses = statuses or ["review"] * 3
        for slot, status in zip(snapshot["current_cycle"]["duties"].values(), statuses):
            self.engine.finish(slot["mission_id"], status)

    def finish_cycle(self, day=DAY):
        snapshot = self.standing.tick(day)
        self.finish_specialists(snapshot)
        snapshot = self.standing.reconcile(day)
        self.engine.finish(snapshot["current_cycle"]["pm"]["mission_id"])
        return self.standing.reconcile(day)

    def test_duties_have_explicit_scope_and_no_publication(self):
        snapshot = self.standing.tick(DAY)
        self.assertEqual(len(snapshot["submitted"]), 3)
        self.assertEqual({p["employee_id"] for p in self.engine.calls}, {"das-rd", "das-mkt", "das-sales"})
        for payload in self.engine.calls:
            self.assertEqual(payload["context"], {"project_id": "daslab-growth"})
            self.assertEqual(payload["execution_mode"], "research")
            self.assertLessEqual(len(payload["text"]), 6000)
            self.assertNotIn("source_attempt_id", payload)
            self.assertNotIn("delivery_operation", payload)
        self.assertFalse(snapshot["publication_enabled"])
        self.assertFalse(snapshot["outreach_enabled"])
        self.assertIn("DES", self.engine.calls[0]["text"])
        self.assertTrue(snapshot["project"]["backlog"])

    def test_resumed_pm_does_not_display_previous_failure_reason(self):
        snapshot = self.standing.tick(DAY)
        self.finish_specialists(snapshot)
        snapshot = self.standing.reconcile(DAY)
        pm_id = snapshot["current_cycle"]["pm"]["mission_id"]
        self.engine.finish(pm_id, "failed")
        self.assertEqual(self.standing.reconcile(DAY)["current_cycle"]["stage"], "needs_attention")
        self.engine.finish(pm_id, "running")
        live = self.standing.snapshot()
        self.assertEqual(live["current_cycle"]["stage"], "pm_review")
        self.assertIsNone(live["current_cycle"]["reason"])
        self.assertNotIn("실패", live["reason"])
        self.engine.finish(pm_id)
        self.assertIsNone(self.standing.snapshot()["reason"])

    def test_concurrent_ticks_are_idempotent(self):
        second = StandingOperations(self.engine)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            snapshots = list(pool.map(lambda n: (self.standing if n % 2 else second).tick(DAY), range(24)))
        self.assertEqual(len(self.engine.calls), 3)
        self.assertEqual(sum(len(s["submitted"]) for s in snapshots), 3)
        self.assertEqual(len(self.engine._all("missions")), 3)

    def test_restart_preserves_links_and_frozen_requests(self):
        first = self.standing.tick(DAY)
        self.engine.db.close()
        self.engine = FakeEngine(self.root, self.database)
        self.policy["duties"][0]["instruction"] = "Changed future policy"
        self.write_policy()
        self.standing = StandingOperations(self.engine)
        second = self.standing.tick("2026-10-01")
        self.assertEqual(first["current_cycle"]["duties"], second["current_cycle"]["duties"])
        self.assertEqual(self.engine.calls, [])
        self.assertEqual(len(second["cycles"]), 1)

    def test_crash_after_submit_commit_recovers_request_without_duplicate(self):
        self.engine.after_commit_failure = True
        first = self.standing.tick(DAY)
        self.assertEqual(len(self.engine._all("missions")), 1)
        self.assertIn("Synthetic interruption", first["reason"])
        self.standing = StandingOperations(self.engine)
        second = self.standing.tick(DAY)
        self.assertEqual(len(self.engine.calls), 3)
        self.assertEqual(len(self.engine._all("missions")), 3)
        self.assertEqual(second["current_cycle"]["stage"], "working")

    def test_pm_waits_for_all_three_outcomes_and_receives_reports(self):
        initial = self.standing.tick(DAY)
        slots = list(initial["current_cycle"]["duties"].values())
        self.engine.finish(slots[0]["mission_id"], result={"summary": "Specific R&D evidence"})
        self.engine.finish(slots[1]["mission_id"])
        self.assertEqual(self.standing.reconcile(DAY)["submitted"], [])
        self.engine.finish(slots[2]["mission_id"])
        result = self.standing.reconcile(DAY)
        self.assertEqual(len(result["submitted"]), 1)
        pm = self.engine.calls[-1]
        self.assertEqual(pm["employee_id"], "das-pm")
        self.assertEqual(pm["execution_mode"], "analysis")
        self.assertIn("Specific R&D evidence", pm["text"])
        self.assertLess(len(pm["text"]), 6000)
        self.assertEqual(result["current_cycle"]["stage"], "pm_review")

    def test_reconcile_never_creates_new_cycle(self):
        self.assertIsNone(self.standing.reconcile(DAY)["current_cycle"])
        self.finish_cycle()
        snapshot = self.standing.reconcile("2026-10-01")
        self.assertEqual(len(snapshot["cycles"]), 1)
        self.assertEqual(len(self.engine.calls), 4)
        self.assertEqual(snapshot["current_cycle"]["stage"], "completed")

    def test_blocked_and_deferred_are_honestly_summarized_without_new_cycle(self):
        initial = self.standing.tick(DAY)
        self.finish_specialists(initial, ["blocked", "deferred", "review"])
        report = self.standing.reconcile(DAY)
        self.assertIn('"status": "blocked"', self.engine.calls[-1]["text"])
        self.assertIn('"status": "deferred"', self.engine.calls[-1]["text"])
        self.engine.finish(report["current_cycle"]["pm"]["mission_id"])
        later = self.standing.tick("2026-10-01")
        self.assertEqual(later["current_cycle"]["stage"], "needs_attention")
        self.assertEqual(len(self.engine.calls), 4)
        self.assertEqual(len(later["cycles"]), 1)

    def test_prior_results_are_inputs_not_overwritten(self):
        first = self.finish_cycle()
        old_ids = [d["mission_id"] for d in first["current_cycle"]["duties"].values()]
        before = [self.engine._get("missions", mid) for mid in old_ids]
        second = self.standing.tick("2026-10-01")
        self.assertEqual(len(second["cycles"]), 2)
        self.assertEqual(len(second["submitted"]), 3)
        for payload in self.engine.calls[-3:]:
            self.assertIn("Synthetic report", payload["text"])
            self.assertIn("이전 업무 참고 자료", payload["text"])
        self.assertEqual(before, [self.engine._get("missions", mid) for mid in old_ids])

    def test_rd_moves_from_first_research_to_weekday_prototype_and_monday_research(self):
        self.engine.prototype_policy = {"enabled": True}
        self.finish_cycle()
        self.assertEqual(self.engine.calls[0]["execution_mode"], "research")
        second = self.standing.tick("2026-10-01")
        rd = next(p for p in self.engine.calls[-3:] if p["employee_id"] == "das-rd")
        self.assertEqual(rd["execution_mode"], "prototype")
        self.assertIn("실제 파일로 구현", rd["text"])
        self.assertEqual({p["execution_mode"] for p in self.engine.calls[-3:] if p["employee_id"] != "das-rd"}, {"research"})
        self.assertEqual(self.standing.tick("2026-10-02")["submitted"], [])
        self.finish_specialists(second)
        reviewed = self.standing.reconcile("2026-10-01")
        self.engine.finish(reviewed["current_cycle"]["pm"]["mission_id"])
        monday = self.standing.tick("2026-10-05")
        self.assertEqual(len(monday["submitted"]), 3)
        rd = next(p for p in self.engine.calls[-3:] if p["employee_id"] == "das-rd")
        self.assertEqual(rd["execution_mode"], "research")

    def test_changed_specialist_result_requires_fresh_pm_review(self):
        initial = self.standing.tick(DAY)
        self.finish_specialists(initial, ["blocked", "review", "review"])
        reviewed = self.standing.reconcile(DAY)
        pm_id = reviewed["current_cycle"]["pm"]["mission_id"]
        self.engine.finish(pm_id)
        rd_id = initial["current_cycle"]["duties"]["simulation-research"]["mission_id"]
        self.engine.finish(rd_id, "review", {"summary": "New corrected research"})
        stale = self.standing.reconcile(DAY)
        self.assertEqual(stale["current_cycle"]["stage"], "needs_attention")
        self.assertIn("PM 검토 이후", stale["reason"])
        # Reading context of a completed PM cannot stamp a fresh review.
        self.standing.review_context(pm_id)
        self.assertEqual(self.standing.snapshot()["current_cycle"]["stage"], "needs_attention")
        self.engine.finish(pm_id, "queued")
        context = self.standing.review_context(pm_id)
        self.assertEqual(context["sources"][0]["result"]["summary"], "New corrected research")
        self.engine.finish(pm_id)
        self.assertEqual(self.standing.reconcile(DAY)["current_cycle"]["stage"], "completed")
        self.assertEqual(len(self.engine.calls), 4)
        self.assertIsNone(self.standing.review_context(rd_id))

    def test_server_prototype_receipt_is_separate_and_invalidates_stale_pm_basis(self):
        initial = self.standing.tick(DAY)
        self.finish_specialists(initial)
        rd_id = initial["current_cycle"]["duties"]["simulation-research"]["mission_id"]
        report = self.engine._get("missions", rd_id)["result"]
        receipt = {"ready": True, "artifacts": {"model.js": "a" * 64}, "changed_files": ["model.js"],
                   "browser_verified": True, "model_verified": True,
                   "browser": {"status": "passed", "business_acceptance": False,
                               "scope": "Synthetic browser checks only", "checks": [{"name": "conservation", "status": "passed"}]}}
        self.engine.set_prototype_receipt(rd_id, receipt)
        waiting = self.standing.reconcile(DAY)
        pm_id = waiting["current_cycle"]["pm"]["mission_id"]
        context = self.standing.review_context(pm_id)
        source = context["sources"][0]
        self.assertEqual(source["result"], report)
        self.assertEqual(source["server_prototype_verification"]["receipt"], receipt)
        self.assertIn("고객 효과", source["server_prototype_verification"]["scope"])
        self.assertFalse(source["server_prototype_verification"]["receipt"]["browser"]["business_acceptance"])
        self.engine.finish(pm_id)
        self.assertEqual(self.standing.reconcile(DAY)["current_cycle"]["stage"], "completed")
        # Only trusted server evidence changes. The model-authored report does not.
        receipt["artifacts"]["model.js"] = "b" * 64
        receipt["browser"]["checks"].append({"name": "new edge condition", "status": "passed"})
        self.engine.set_prototype_receipt(rd_id, receipt)
        stale = self.standing.reconcile(DAY)
        self.assertEqual(stale["current_cycle"]["stage"], "needs_attention")
        self.assertEqual(self.engine._get("missions", rd_id)["result"], report)
        self.engine.finish(pm_id, "queued")
        self.assertEqual(self.standing.review_context(pm_id)["sources"][0]["server_prototype_verification"]["receipt"], receipt)
        self.engine.finish(pm_id)
        self.assertEqual(self.standing.reconcile(DAY)["current_cycle"]["stage"], "completed")
        next_cycle = self.standing.tick("2026-10-01")
        next_rd = next_cycle["current_cycle"]["duties"]["simulation-research"]["mission_id"]
        carried = self.standing.work_context(next_rd)
        self.assertEqual(carried["employee_result"], report)
        self.assertEqual(carried["employee_server_prototype_verification"]["receipt"], receipt)

    def test_pause_and_config_disabled_prevent_all_new_assignments(self):
        self.standing.set_paused(True)
        self.assertEqual(self.standing.tick(DAY)["submitted"], [])
        self.standing = StandingOperations(self.engine)
        self.assertTrue(self.standing.snapshot()["paused"])
        self.standing.set_paused(False)
        self.policy["enabled"] = False
        self.write_policy()
        self.standing = StandingOperations(self.engine)
        self.assertEqual(self.standing.tick(DAY)["submitted"], [])
        self.assertEqual(self.engine.calls, [])

    def test_pause_prevents_pm_dispatch_but_preserves_outcomes(self):
        snapshot = self.standing.tick(DAY)
        self.standing.set_paused(True)
        self.finish_specialists(snapshot)
        stopped = self.standing.reconcile(DAY)
        self.assertEqual(stopped["submitted"], [])
        self.assertEqual(stopped["current_cycle"]["stage"], "awaiting_pm")
        self.standing.set_paused(False)
        self.assertEqual(len(self.standing.reconcile(DAY)["submitted"]), 1)

    def test_weekends_do_not_create_cycles(self):
        snapshot = self.standing.tick("2026-10-03")
        self.assertIsNone(snapshot["current_cycle"])
        self.assertEqual(self.engine.calls, [])

    def test_exhausted_quota_does_not_enqueue(self):
        self.engine.used = 10
        snapshot = self.standing.tick(DAY)
        self.assertEqual(snapshot["submitted"], [])
        self.assertIn("한도", snapshot["reason"])
        self.assertEqual(self.engine.calls, [])
        self.engine.used = 0
        self.assertEqual(len(self.standing.tick(DAY)["submitted"]), 3)

    def test_provider_quota_does_not_enqueue_or_auto_clear(self):
        self.engine._quota_blocked = True
        self.assertEqual(self.standing.tick(DAY)["submitted"], [])
        self.assertTrue(self.engine._quota_blocked)
        self.assertEqual(self.engine.calls, [])

    def test_queue_reservations_prevent_partial_capacity_overbooking(self):
        self.engine.used = 8
        first = self.standing.tick(DAY)
        self.assertEqual(len(first["submitted"]), 2)
        self.assertEqual(self.standing.tick(DAY)["submitted"], [])
        self.engine.used = 0
        self.assertEqual(len(self.standing.tick("2026-10-01")["submitted"]), 1)
        self.assertEqual(len(self.engine.calls), 3)

    def test_standing_daily_cap_applies_across_cycle_boundaries(self):
        previous = self.standing.tick(DAY)
        self.finish_specialists(previous)
        previous = self.standing.reconcile("2026-10-01")
        self.engine.finish(previous["current_cycle"]["pm"]["mission_id"])
        current = self.standing.tick("2026-10-01")
        self.finish_specialists(current)
        capped = self.standing.reconcile("2026-10-01")
        self.assertEqual(capped["submitted"], [])
        self.assertIn("정기 배정 한도", capped["reason"])
        self.assertEqual(len(self.engine.calls), 7)
        self.assertEqual(len(self.standing.reconcile("2026-10-02")["submitted"]), 1)

    def test_snapshot_is_read_only_and_has_no_scheduler_claim(self):
        snapshot = self.standing.snapshot()
        self.assertIsNone(self.engine.db.execute("SELECT value FROM settings WHERE key=?", (STATE_KEY,)).fetchone())
        self.assertEqual(snapshot["scheduler"], "external_heartbeat")
        self.assertFalse(snapshot["schedule_registration"]["enabled"])
        self.assertFalse(snapshot["recurring_enabled"])
        self.assertEqual(self.engine.calls, [])

    def test_next_run_receives_prior_work_and_pm_review_without_new_permissions(self):
        first = self.finish_cycle()
        rd_id = first["current_cycle"]["duties"]["simulation-research"]["mission_id"]
        self.assertIsNone(self.standing.work_context(rd_id))
        pm_id = first["current_cycle"]["pm"]["mission_id"]
        self.engine.finish(pm_id, result={"summary": "Compare stock shortage before expanding visual work."})
        next_cycle = self.standing.tick("2026-10-01")
        next_rd = next_cycle["current_cycle"]["duties"]["simulation-research"]["mission_id"]
        context = self.standing.work_context(next_rd)
        self.assertEqual(context["prior_cycle"], DAY)
        self.assertIn("Synthetic", context["employee_result"]["summary"])
        self.assertIn("stock shortage", context["pm_review"]["summary"])
        self.assertIn("권한", context["instruction"])
        self.assertIsNone(self.standing.work_context("unrelated"))

    def test_registered_schedule_receipt_persists_without_starting_work(self):
        self.standing.set_paused(True)
        first = self.standing.register_schedule("test-confirmed-automation")
        receipt = first["schedule_registration"]
        self.assertTrue(receipt["enabled"])
        self.assertTrue(first["recurring_enabled"])
        self.assertEqual(receipt["id"], "test-confirmed-automation")
        self.assertEqual(receipt["host"], "local")
        self.assertTrue(receipt["registered_at"])
        self.assertTrue(first["paused"])
        self.assertEqual(self.engine.calls, [])
        events = len(self.engine.events)
        self.assertEqual(self.standing.register_schedule("test-confirmed-automation")["schedule_registration"], receipt)
        self.assertEqual(len(self.engine.events), events)
        self.engine.db.close()
        self.engine = FakeEngine(self.root, self.database)
        self.standing = StandingOperations(self.engine)
        self.assertEqual(self.standing.snapshot()["schedule_registration"], receipt)
        self.assertEqual(self.engine.calls, [])

    def test_schedule_registration_rejects_invalid_ids(self):
        for invalid in (None, True, 1, [], {}, "", "  ", "x" * 201, "embedded\nnewline"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    self.standing.register_schedule(invalid)
        self.assertFalse(self.standing.snapshot()["recurring_enabled"])

    def test_config_cannot_grant_scope_or_external_actions(self):
        for patch in ({"publication_enabled": True}, {"outreach_enabled": True},
                      {"project_id": "other-project"}, {"max_open_cycles": 2},
                      {"daily_specialist_limit": 4}, {"duties": ["invalid"]}):
            with self.subTest(patch=patch):
                policy = {**self.policy, **patch}
                (self.root / "config/standing.json").write_text(json.dumps(policy), encoding="utf-8")
                with self.assertRaises(ValueError):
                    StandingOperations(self.engine)


if __name__ == "__main__":
    unittest.main()
