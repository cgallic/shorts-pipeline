"""Per-channel history DB + cross-channel cuts registry.

The mascot_compositions table and `format`/`character`/`pose_id` columns are
reserved for the v0.3 animated path. They're harmless in v0.1 (always default
to 'forehead' / NULL) and let the v0.3 port reuse the same DB.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass
class UsedCut:
    source_path: str
    start_s: float
    end_s: float
    hook: str
    title: str
    topic_seed: str
    music_track: str
    output_path: str


class ChannelHistory:
    def __init__(self, db_path: str | Path, channel: str) -> None:
        self.db_path = Path(db_path)
        self.channel = channel

    def _conn(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(self.db_path)

    def init(self) -> None:
        with self._conn() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS used_cuts (
                  id INTEGER PRIMARY KEY,
                  source_path TEXT NOT NULL,
                  start_s REAL NOT NULL,
                  end_s REAL NOT NULL,
                  hook TEXT NOT NULL,
                  title TEXT NOT NULL,
                  topic_seed TEXT NOT NULL,
                  music_track TEXT NOT NULL,
                  created_at TEXT DEFAULT (datetime('now')),
                  output_path TEXT NOT NULL,
                  format TEXT NOT NULL DEFAULT 'forehead',
                  character TEXT,
                  pose_id TEXT
                );

                CREATE TABLE IF NOT EXISTS mascot_compositions (
                  id INTEGER PRIMARY KEY,
                  character TEXT NOT NULL,
                  pose_id TEXT NOT NULL,
                  background_id TEXT NOT NULL,
                  created_at TEXT DEFAULT (datetime('now'))
                );
                CREATE INDEX IF NOT EXISTS idx_composition_lookup
                  ON mascot_compositions(character, pose_id, background_id, created_at);
            """)
        self._migrate()
        with self._conn() as c:
            c.executescript("""
                CREATE INDEX IF NOT EXISTS idx_source_path ON used_cuts(source_path);
                CREATE INDEX IF NOT EXISTS idx_topic_seed ON used_cuts(topic_seed);
                CREATE INDEX IF NOT EXISTS idx_window ON used_cuts(source_path, start_s, end_s);
                CREATE INDEX IF NOT EXISTS idx_hook ON used_cuts(hook);
                CREATE INDEX IF NOT EXISTS idx_format ON used_cuts(format);
            """)

    def _migrate(self) -> None:
        """Idempotent ALTER TABLE for DBs predating the v0.3-reserved columns."""
        for col_def in (
            "format TEXT NOT NULL DEFAULT 'forehead'",
            "character TEXT",
            "pose_id TEXT",
        ):
            with self._conn() as c:
                try:
                    c.execute(f"ALTER TABLE used_cuts ADD COLUMN {col_def}")
                except sqlite3.OperationalError:
                    pass

    def record(self, cut: UsedCut) -> None:
        with self._conn() as c:
            c.execute(
                """INSERT INTO used_cuts
                   (source_path, start_s, end_s, hook, title, topic_seed, music_track, output_path, format)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'forehead')""",
                (cut.source_path, cut.start_s, cut.end_s, cut.hook, cut.title,
                 cut.topic_seed, cut.music_track, cut.output_path),
            )

    def record_mascot(
        self,
        hook: str,
        title: str,
        topic_seed: str,
        music_track: str,
        output_path: str,
        character: str,
        pose_id: str,
        background_id: str,
    ) -> None:
        with self._conn() as c:
            c.execute(
                """INSERT INTO used_cuts
                   (source_path, start_s, end_s, hook, title, topic_seed,
                    music_track, output_path, format, character, pose_id)
                   VALUES ('', 0, 0, ?, ?, ?, ?, ?, 'mascot', ?, ?)""",
                (hook, title, topic_seed, music_track, output_path,
                 character, pose_id),
            )
            c.execute(
                """INSERT INTO mascot_compositions
                   (character, pose_id, background_id) VALUES (?, ?, ?)""",
                (character, pose_id, background_id),
            )

    def has_hook(self, hook: str) -> bool:
        with self._conn() as c:
            row = c.execute(
                "SELECT 1 FROM used_cuts WHERE hook = ? LIMIT 1", (hook,)
            ).fetchone()
            return row is not None

    def is_topic_seed_in_cooldown(self, topic_seed: str, days: int) -> bool:
        with self._conn() as c:
            row = c.execute(
                """SELECT 1 FROM used_cuts
                   WHERE topic_seed = ?
                     AND created_at > datetime('now', ?)
                   LIMIT 1""",
                (topic_seed, f"-{days} days"),
            ).fetchone()
            return row is not None

    def is_window_in_cooldown(
        self, source_path: str, start_s: float, end_s: float, days: int
    ) -> bool:
        with self._conn() as c:
            row = c.execute(
                """SELECT 1 FROM used_cuts
                   WHERE source_path = ? AND start_s = ? AND end_s = ?
                     AND created_at > datetime('now', ?)
                   LIMIT 1""",
                (source_path, start_s, end_s, f"-{days} days"),
            ).fetchone()
            return row is not None

    def is_composition_in_cooldown(
        self, character: str, pose_id: str, background_id: str, days: int = 7
    ) -> bool:
        with self._conn() as c:
            row = c.execute(
                """SELECT 1 FROM mascot_compositions
                   WHERE character = ? AND pose_id = ? AND background_id = ?
                     AND created_at > datetime('now', ?)
                   LIMIT 1""",
                (character, pose_id, background_id, f"-{days} days"),
            ).fetchone()
            return row is not None


class CutsRegistry:
    """Cross-channel cut registry — same window never goes to two channels."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def _conn(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(self.db_path)

    def init(self) -> None:
        with self._conn() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS channel_cuts (
                  source_path TEXT NOT NULL,
                  start_s REAL NOT NULL,
                  end_s REAL NOT NULL,
                  channel TEXT NOT NULL,
                  created_at TEXT DEFAULT (datetime('now')),
                  PRIMARY KEY (source_path, start_s, end_s)
                );
            """)

    def register(self, source_path: str, start_s: float, end_s: float, channel: str) -> None:
        with self._conn() as c:
            c.execute(
                """INSERT OR IGNORE INTO channel_cuts
                   (source_path, start_s, end_s, channel) VALUES (?, ?, ?, ?)""",
                (source_path, start_s, end_s, channel),
            )

    def is_used(self, source_path: str, start_s: float, end_s: float) -> bool:
        with self._conn() as c:
            row = c.execute(
                """SELECT 1 FROM channel_cuts
                   WHERE source_path = ? AND start_s = ? AND end_s = ?
                   LIMIT 1""",
                (source_path, start_s, end_s),
            ).fetchone()
            return row is not None

    def used_windows_for_source(
        self, source_path: str, exclude_channel: str | None = None
    ) -> list[tuple[float, float]]:
        """Return all (start_s, end_s) windows used for this source.

        If `exclude_channel` is given, exclude that channel's own usage — the
        orchestrator passes its own channel so a channel can re-use its own
        old windows (subject to the per-channel 14-day window cooldown), while
        still being permanently blocked from windows any OTHER channel has
        claimed.
        """
        with self._conn() as c:
            if exclude_channel is None:
                rows = c.execute(
                    "SELECT start_s, end_s FROM channel_cuts WHERE source_path = ?",
                    (source_path,),
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT start_s, end_s FROM channel_cuts WHERE source_path = ? AND channel != ?",
                    (source_path, exclude_channel),
                ).fetchall()
            return [(r[0], r[1]) for r in rows]
