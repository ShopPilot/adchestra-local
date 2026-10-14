"""Pending changes persist across Claude restarts."""
import json
import sqlite3
from datetime import datetime, timezone, timedelta
from dataclasses import asdict
from adchestra_local.safety.preview import ChangePlan

class SQLitePlanStore:
    def __init__(self, path):
        self.path = path
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS plans (tenant TEXT, id TEXT, data TEXT, PRIMARY KEY(tenant,id))")
        path.chmod(0o600)

    def connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def store(self, tenant, plan):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO plans VALUES (?,?,?)", (tenant, plan.plan_id, json.dumps(asdict(plan))))

    def get(self, tenant, plan_id):
        with self.connect() as db:
            row = db.execute("SELECT data FROM plans WHERE tenant=? AND id=?", (tenant, plan_id)).fetchone()
        if not row:
            return None
        plan = ChangePlan(**json.loads(row[0]))
        if datetime.fromisoformat(plan.created_at) < datetime.now(timezone.utc) - timedelta(hours=24):
            self.remove(tenant, plan_id)
            return None
        return plan

    def remove(self, tenant, plan_id):
        with self.connect() as db:
            db.execute("DELETE FROM plans WHERE tenant=? AND id=?", (tenant, plan_id))
