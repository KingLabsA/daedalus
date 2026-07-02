"""CalibrationTracker — records predicted confidence vs actual outcomes, so
Hermes learns whether its 90% actually means 90% *in this environment*.
"""
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional

BUCKETS = 10
MIN_SAMPLES = 5


class CalibrationTracker:
    def __init__(self, db_path: str = "hermes_ultimate.db"):
        self.db_path = db_path
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS calibration_events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    success INTEGER NOT NULL,
                    created_at TEXT
                )""")

    @staticmethod
    def _bucket(confidence: float) -> int:
        confidence = max(0.0, min(1.0, confidence))
        return min(BUCKETS - 1, int(confidence * BUCKETS))

    def record(self, kind: str, confidence: float, success: bool):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO calibration_events (kind, confidence, success, created_at) VALUES (?,?,?,?)",
                (kind, max(0.0, min(1.0, confidence)), 1 if success else 0,
                 datetime.now().isoformat(timespec="seconds")),
            )

    def calibrated(self, confidence: float, kind: Optional[str] = None) -> float:
        """Adjusted success probability for a stated confidence, from history.
        Laplace-smoothed; falls back to the raw confidence when data is thin."""
        bucket = self._bucket(confidence)
        low, high = bucket / BUCKETS, (bucket + 1) / BUCKETS
        query = "SELECT COUNT(*) AS n, SUM(success) AS wins FROM calibration_events WHERE confidence >= ? AND confidence < ?"
        params: List = [low, high if bucket < BUCKETS - 1 else 1.01]
        if kind:
            query += " AND kind = ?"
            params.append(kind)
        with self._conn() as conn:
            row = conn.execute(query, params).fetchone()
        n, wins = row["n"] or 0, row["wins"] or 0
        if n < MIN_SAMPLES:
            return confidence
        return (wins + 1) / (n + 2)  # Laplace

    def success_rate(self, kind: str) -> Optional[float]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n, SUM(success) AS wins FROM calibration_events WHERE kind = ?", (kind,)
            ).fetchone()
        n = row["n"] or 0
        if n < MIN_SAMPLES:
            return None
        return (row["wins"] + 1) / (n + 2)

    def report(self) -> Dict:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT confidence, success, kind FROM calibration_events"
            ).fetchall()
        buckets = [{"range": f"{b/BUCKETS:.1f}-{(b+1)/BUCKETS:.1f}", "n": 0, "wins": 0} for b in range(BUCKETS)]
        kinds: Dict[str, Dict] = {}
        for row in rows:
            b = self._bucket(row["confidence"])
            buckets[b]["n"] += 1
            buckets[b]["wins"] += row["success"]
            k = kinds.setdefault(row["kind"], {"n": 0, "wins": 0})
            k["n"] += 1
            k["wins"] += row["success"]
        for b in buckets:
            b["actual"] = round(b["wins"] / b["n"], 2) if b["n"] else None
        for k in kinds.values():
            k["rate"] = round(k["wins"] / k["n"], 2) if k["n"] else None
        return {"total_events": len(rows), "buckets": [b for b in buckets if b["n"]], "by_kind": kinds}
