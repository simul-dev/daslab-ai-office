import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4


def now():
    return datetime.now(timezone.utc).isoformat()


def ident():
    return uuid4().hex


def json_text(value):
    return json.dumps(value, ensure_ascii=False, indent=2)


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, task_id TEXT NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS decisions(id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, data TEXT NOT NULL);
            """)

    @contextmanager
    def connect(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=15)
            try:
                db.execute("PRAGMA synchronous=FULL")
                db.execute("BEGIN IMMEDIATE")
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()

    def read(self, db, table, key):
        row = db.execute(f"SELECT data FROM {table} WHERE id=?", (key,)).fetchone()
        if not row:
            raise KeyError(key)
        return json.loads(row[0])

    def save(self, db, table, value):
        if table == "tasks":
            db.execute("INSERT OR REPLACE INTO tasks VALUES(?,?)", (value["id"], json_text(value)))
        else:
            db.execute("INSERT OR REPLACE INTO runs VALUES(?,?,?)", (value["id"], value["task_id"], json_text(value)))

    def event(self, db, task, kind, message, source="office", status=None):
        data = {"type": kind, "message": message, "source": source, "created_at": now(),
                "project_id": task["project_id"], "verification_status": status or task["verification_status"]}
        db.execute("INSERT INTO events(task_id,data) VALUES(?,?)", (task["id"], json_text(data)))

    def create(self, task):
        with self.connect() as db:
            self.save(db, "tasks", task)
            self.event(db, task, "created", "대표가 업무를 등록했습니다.", "owner")
            self.event(db, task, "planned", "Planner가 명세와 완료 기준을 정리했습니다.", "planner:" + task["pm_spec"]["provider"])
        return self.detail(task["id"])

    def tasks(self):
        with self.connect() as db:
            rows = [json.loads(r[0]) for r in db.execute("SELECT data FROM tasks")]
        return sorted(rows, key=lambda t: t["created_at"], reverse=True)

    def detail(self, task_id):
        with self.connect() as db:
            task = self.read(db, "tasks", task_id)
            out = {"task": task}
            for table in ("runs", "events", "decisions"):
                out[table] = [json.loads(r[0]) for r in db.execute(f"SELECT data FROM {table} WHERE task_id=? ORDER BY rowid", (task_id,))]
            return out

    def usage_from(self, db):
        runs = [json.loads(r[0]) for r in db.execute("SELECT data FROM runs")]
        today = datetime.now(timezone(timedelta(hours=9))).date()
        return {"runs_today": sum(datetime.fromisoformat(r["started_at"]).astimezone(timezone(timedelta(hours=9))).date() == today for r in runs),
                "active_runs": sum(r["status"] == "running" for r in runs)}

    def usage(self):
        with self.connect() as db:
            return self.usage_from(db)

    def claim(self, task_id, config, run_root):
        with self.connect() as db:
            task = self.read(db, "tasks", task_id)
            if task["status"] not in ("queued", "failed", "cancelled"):
                raise Conflict("대기·실패·취소 업무만 명시적으로 실행할 수 있습니다.")
            usage = self.usage_from(db)
            attempts = db.execute("SELECT COUNT(*) FROM runs WHERE task_id=?", (task_id,)).fetchone()[0]
            if usage["active_runs"]:
                raise Conflict("작업자 1개가 실행 중입니다. 종료 후 실행해 주세요.")
            if attempts >= config["max_attempts"]:
                raise Conflict("이 업무의 최대 실행 횟수에 도달했습니다.")
            if usage["runs_today"] >= config["daily_runs"]:
                raise Conflict("오늘의 실행 횟수 제한에 도달했습니다. (한국 시간 기준)")
            run_id = ident()
            run = {"id": run_id, "task_id": task_id, "attempt": attempts + 1, "status": "running",
                   "started_at": now(), "finished_at": None, "error": None, "provider": config["worker_provider"],
                   "run_dir": str(run_root / task_id / run_id), "artifacts": [], "verification": None,
                   "source": "task:" + task_id, "created_at": now(), "project_id": task["project_id"], "verification_status": "pending"}
            task.update(status="running", error=None, updated_at=now(), verification_status="pending")
            self.save(db, "tasks", task)
            self.save(db, "runs", run)
            self.event(db, task, "started", f"실제 {run['provider']} 작업자 실행 요청 · {attempts + 1}회차", "run:" + run_id)
            return task, run

    def finish(self, task_id, run_id, status, error, verification, artifacts):
        with self.connect() as db:
            task = self.read(db, "tasks", task_id)
            run = self.read(db, "runs", run_id)
            if run["task_id"] != task_id or run["status"] != "running" or task["status"] != "running":
                raise Conflict("현재 실행에 해당하는 결과만 저장할 수 있습니다.")
            if status not in ("review", "failed", "cancelled"):
                raise Conflict("허용되지 않는 실행 종료 상태입니다.")
            if status == "review" and not (verification or {}).get("passed"):
                raise Conflict("검증 통과 근거가 있어야 검토를 요청할 수 있습니다.")
            vstatus = "basic_checks_passed_human_review_required" if status == "review" else "failed"
            run.update(status=status, error=error, finished_at=now(), verification=verification,
                       artifacts=artifacts, verification_status=vstatus)
            task.update(status=status, error=error, updated_at=now(), verification_status=vstatus)
            self.save(db, "runs", run)
            self.save(db, "tasks", task)
            self.event(db, task, status, "산출물과 기본 검증 근거가 저장되었습니다. 대표 검토가 필요합니다." if status == "review" else error or "실행 취소", "run:" + run_id)

    def cancel_idle(self, task_id):
        with self.connect() as db:
            task = self.read(db, "tasks", task_id)
            if task["status"] not in ("queued", "failed", "review"):
                raise Conflict("현재 상태에서는 취소할 수 없습니다.")
            task.update(status="cancelled", updated_at=now(), verification_status="cancelled")
            self.save(db, "tasks", task)
            self.event(db, task, "cancelled", "대표가 업무를 취소했습니다.", "owner")

    def review(self, task_id, decision, note):
        if decision not in ("approve", "reject"):
            raise ValueError("검토 결정이 올바르지 않습니다.")
        with self.connect() as db:
            task = self.read(db, "tasks", task_id)
            if task["status"] != "review":
                raise Conflict("검토 필요 상태의 업무만 검토할 수 있습니다.")
            status = "completed" if decision == "approve" else "failed"
            task.update(status=status, error=None if decision == "approve" else "대표 반려: " + note,
                        updated_at=now(), verification_status="human_approved" if decision == "approve" else "human_rejected")
            self.save(db, "tasks", task)
            d = {"decision": decision, "note": note, "source": "owner", "created_at": now(),
                 "project_id": task["project_id"], "verification_status": task["verification_status"]}
            db.execute("INSERT INTO decisions(task_id,data) VALUES(?,?)", (task_id, json_text(d)))
            self.event(db, task, status, "대표 승인" if decision == "approve" else "대표 반려: " + note, "owner")

    def recover(self):
        recovered = []
        with self.connect() as db:
            for (raw,) in db.execute("SELECT data FROM runs").fetchall():
                run = json.loads(raw)
                if run["status"] == "running":
                    reason = "서비스가 중단되어 실행이 종료되었습니다. 기록을 확인한 뒤 명시적으로 재실행하세요."
                    run.update(status="failed", error=reason, finished_at=now(), verification_status="interrupted")
                    self.save(db, "runs", run)
                    recovered.append(run)
                    task = self.read(db, "tasks", run["task_id"])
                    task.update(status="failed", error=reason, updated_at=now(), verification_status="interrupted")
                    self.save(db, "tasks", task)
                    self.event(db, task, "recovered", reason, "startup")
        return recovered

    def run(self, run_id):
        with self.connect() as db:
            return self.read(db, "runs", run_id)
