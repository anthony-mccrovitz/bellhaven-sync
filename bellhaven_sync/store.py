"""SQLite ledger of runs, proposals and decisions.

This is what makes re-runs safe: every proposal has a deterministic fingerprint, and a
fingerprint that was already approved/rejected/applied is never re-queued.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  dry_run INTEGER NOT NULL DEFAULT 0,
  summary_json TEXT
);
CREATE TABLE IF NOT EXISTS proposals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  fingerprint TEXT UNIQUE NOT NULL,
  kind TEXT NOT NULL,
  subject TEXT NOT NULL,
  title TEXT NOT NULL,
  summary TEXT NOT NULL,
  confidence TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  actions_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  first_seen_run INTEGER,
  last_seen_run INTEGER,
  created_at TEXT NOT NULL,
  decided_at TEXT,
  decided_by TEXT,
  decision_note TEXT,
  applied_at TEXT,
  result_json TEXT
);
CREATE TABLE IF NOT EXISTS matches (
  run_id INTEGER NOT NULL,
  slug TEXT NOT NULL,
  location_json TEXT NOT NULL,
  account_id TEXT,
  tier TEXT,
  signals_json TEXT,
  note TEXT
);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL,
  proposal_id INTEGER,
  event TEXT NOT NULL,
  detail_json TEXT
);
"""

DECIDED = ("approved", "rejected", "applied", "failed")


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


class Store:
    def __init__(self, path=None):
        self.path = str(path or DB_PATH)
        import os
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # ---- runs -------------------------------------------------------------
    def start_run(self, dry_run: bool = False) -> int:
        cur = self.db.execute("INSERT INTO runs(started_at, dry_run) VALUES (?,?)", (now(), int(dry_run)))
        self.db.commit()
        return cur.lastrowid

    def finish_run(self, run_id: int, summary: dict):
        self.db.execute("UPDATE runs SET finished_at=?, summary_json=? WHERE id=?",
                        (now(), json.dumps(summary), run_id))
        self.db.commit()

    def runs(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM runs ORDER BY id DESC")]

    # ---- proposals --------------------------------------------------------
    def upsert_proposals(self, run_id: int, proposals: list) -> dict:
        """Returns counts. Decided proposals are never re-opened."""
        counts = {"new": 0, "refreshed": 0, "already_decided": 0, "withdrawn": 0, "reappeared_after_apply": []}
        seen = set()
        for p in proposals:
            seen.add(p.fingerprint)
            row = self.db.execute("SELECT id, status FROM proposals WHERE fingerprint=?", (p.fingerprint,)).fetchone()
            if row is None:
                self.db.execute(
                    "INSERT INTO proposals(fingerprint, kind, subject, title, summary, confidence, evidence_json, "
                    "actions_json, status, first_seen_run, last_seen_run, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (p.fingerprint, p.kind, p.subject, p.title, p.summary, p.confidence,
                     json.dumps(p.evidence), json.dumps(p.actions), "pending", run_id, run_id, now()))
                counts["new"] += 1
            elif row["status"] in ("pending", "withdrawn"):
                self.db.execute(
                    "UPDATE proposals SET title=?, summary=?, confidence=?, evidence_json=?, actions_json=?, "
                    "status='pending', last_seen_run=? WHERE id=?",
                    (p.title, p.summary, p.confidence, json.dumps(p.evidence), json.dumps(p.actions), run_id, row["id"]))
                counts["refreshed"] += 1
            else:
                self.db.execute("UPDATE proposals SET last_seen_run=? WHERE id=?", (run_id, row["id"]))
                counts["already_decided"] += 1
                if row["status"] == "applied":
                    counts["reappeared_after_apply"].append(row["id"])
        # anything still pending that this run did not regenerate is stale
        for r in self.db.execute("SELECT id, fingerprint FROM proposals WHERE status='pending'"):
            if r["fingerprint"] not in seen:
                self.db.execute("UPDATE proposals SET status='withdrawn', last_seen_run=? WHERE id=?", (run_id, r["id"]))
                counts["withdrawn"] += 1
        self.db.commit()
        return counts

    def replace_matches(self, run_id: int, rows: list[dict]):
        self.db.execute("DELETE FROM matches")
        self.db.executemany(
            "INSERT INTO matches(run_id, slug, location_json, account_id, tier, signals_json, note) VALUES (?,?,?,?,?,?,?)",
            [(run_id, r["slug"], json.dumps(r["location"]), r.get("account_id"), r.get("tier"),
              json.dumps(r.get("signals", {})), r.get("note", "")) for r in rows])
        self.db.commit()

    def matches(self) -> list[dict]:
        out = []
        for r in self.db.execute("SELECT * FROM matches ORDER BY slug"):
            d = dict(r)
            d["location"] = json.loads(d.pop("location_json"))
            d["signals"] = json.loads(d.pop("signals_json") or "{}")
            out.append(d)
        return out

    def _row(self, r) -> dict:
        d = dict(r)
        d["evidence"] = json.loads(d.pop("evidence_json"))
        d["actions"] = json.loads(d.pop("actions_json"))
        d["result"] = json.loads(d["result_json"]) if d.get("result_json") else None
        return d

    def list_proposals(self, status: str | None = None) -> list[dict]:
        q = "SELECT * FROM proposals"
        args: tuple = ()
        if status:
            q += " WHERE status=?"
            args = (status,)
        q += " ORDER BY CASE confidence WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, kind, subject"
        return [self._row(r) for r in self.db.execute(q, args)]

    def get_proposal(self, pid: int) -> dict | None:
        r = self.db.execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone()
        return self._row(r) if r else None

    def counts(self) -> dict:
        return {r["status"]: r["n"] for r in self.db.execute("SELECT status, COUNT(*) n FROM proposals GROUP BY status")}

    def decide(self, pid: int, status: str, who: str, note: str = ""):
        assert status in ("approved", "rejected")
        self.db.execute("UPDATE proposals SET status=?, decided_at=?, decided_by=?, decision_note=? WHERE id=?",
                        (status, now(), who, note, pid))
        self.audit(pid, status, {"by": who, "note": note})
        self.db.commit()

    def mark_applied(self, pid: int, result: dict, ok: bool):
        self.db.execute("UPDATE proposals SET status=?, applied_at=?, result_json=? WHERE id=?",
                        ("applied" if ok else "failed", now(), json.dumps(result), pid))
        self.audit(pid, "applied" if ok else "failed", result)
        self.db.commit()

    def reopen(self, pid: int, who: str):
        self.db.execute("UPDATE proposals SET status='pending', decided_at=NULL, decided_by=NULL, "
                        "decision_note=NULL, result_json=NULL WHERE id=?", (pid,))
        self.audit(pid, "reopened", {"by": who})
        self.db.commit()

    def audit(self, pid, event: str, detail: dict | None = None):
        self.db.execute("INSERT INTO audit(at, proposal_id, event, detail_json) VALUES (?,?,?,?)",
                        (now(), pid, event, json.dumps(detail or {})))

    def audit_log(self, limit=200) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))]
