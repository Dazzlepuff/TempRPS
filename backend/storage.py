import json
import sqlite3
from contextlib import contextmanager
from uuid import uuid4

from .rps_ai import AdaptiveRPS


class Storage:
    def __init__(self, path):
        self.path = path
        with self.transaction(True) as db:
            columns = {row["name"] for row in db.execute("PRAGMA table_info(games)")}
            legacy = "rounds" in columns
            if legacy:
                db.execute("ALTER TABLE games RENAME TO games_legacy_v1")
            statements = (
                """CREATE TABLE IF NOT EXISTS players (
                    id TEXT PRIMARY KEY,
                    temperature REAL NOT NULL CHECK (temperature >= 0),
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                )""",
                """CREATE TABLE IF NOT EXISTS games (
                    id TEXT PRIMARY KEY,
                    player_id TEXT REFERENCES players(id),
                    temperature REAL NOT NULL CHECK (temperature >= 0),
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                )""",
                """CREATE TABLE IF NOT EXISTS rounds (
                    id INTEGER PRIMARY KEY,
                    round_id TEXT NOT NULL UNIQUE,
                    game_id TEXT REFERENCES games(id) ON DELETE SET NULL,
                    player_id TEXT REFERENCES players(id) ON DELETE CASCADE,
                    human TEXT NOT NULL CHECK (human IN ('R', 'P', 'S')),
                    ai TEXT NOT NULL CHECK (ai IN ('R', 'P', 'S')),
                    prediction TEXT NOT NULL CHECK (prediction IN ('R', 'P', 'S')),
                    result TEXT NOT NULL CHECK (result IN ('win', 'loss', 'tie')),
                    temperature REAL,
                    probabilities TEXT,
                    ai_move_probabilities TEXT,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                )""",
                """CREATE TABLE IF NOT EXISTS pending_rounds (
                    game_id TEXT PRIMARY KEY REFERENCES games(id) ON DELETE CASCADE,
                    data TEXT NOT NULL
                )""",
                "CREATE INDEX IF NOT EXISTS rounds_player_order ON rounds(player_id, id)",
                "CREATE INDEX IF NOT EXISTS rounds_game_order ON rounds(game_id, id)",
                "CREATE INDEX IF NOT EXISTS games_player ON games(player_id, created_at)",
            )
            for statement in statements:
                db.execute(statement)
            if legacy:
                for row in db.execute("SELECT * FROM games_legacy_v1").fetchall():
                    db.execute("INSERT INTO games (id, temperature) VALUES (?, ?)",
                               (row["id"], row["temperature"]))
                    for record in json.loads(row["rounds"]):
                        self.add_round(db, row["id"], None, record)
                    if row["pending"]:
                        db.execute("INSERT INTO pending_rounds VALUES (?, ?)", (row["id"], row["pending"]))
            db.execute("PRAGMA user_version = 2")

    @contextmanager
    def transaction(self, write=False):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def game(self, db, game_id):
        return db.execute("""SELECT games.*, pending_rounds.data AS pending FROM games
            LEFT JOIN pending_rounds ON games.id = pending_rounds.game_id
            WHERE games.id = ?""", (game_id,)).fetchone()

    def player(self, db, player_id):
        return db.execute("SELECT * FROM players WHERE id = ?", (player_id,)).fetchone()

    def ensure_player(self, db, player_id, temperature):
        db.execute("INSERT OR IGNORE INTO players (id, temperature) VALUES (?, ?)",
                   (player_id, temperature))
        return self.player(db, player_id)

    def records(self, db, kind, identifier, offset=None, limit=None):
        column = {"game": "game_id", "player": "player_id"}[kind]
        query = f"SELECT * FROM rounds WHERE {column} = ? ORDER BY id"
        parameters = [identifier]
        if limit is not None:
            query += " LIMIT ? OFFSET ?"
            parameters.extend((limit, offset or 0))
        records = []
        for row in db.execute(query, parameters):
            record = dict(row)
            record.pop("id")
            for key in ("probabilities", "ai_move_probabilities"):
                record[key] = json.loads(record[key]) if record[key] else None
            records.append(record)
        return records

    def model(self, db, kind, identifier):
        return AdaptiveRPS(self.records(db, kind, identifier))

    def count(self, db, kind, identifier):
        column = {"game": "game_id", "player": "player_id"}[kind]
        return db.execute(f"SELECT COUNT(*) FROM rounds WHERE {column} = ?", (identifier,)).fetchone()[0]

    def add_round(self, db, game_id, player_id, record):
        values = {**record, "game_id": game_id, "player_id": player_id,
                  "round_id": record.get("round_id", uuid4().hex)}
        columns = ("round_id", "game_id", "player_id", "human", "ai", "prediction", "result",
                   "temperature", "probabilities", "ai_move_probabilities")
        for key in ("probabilities", "ai_move_probabilities"):
            values[key] = json.dumps(values[key]) if values.get(key) is not None else None
        db.execute(f"INSERT INTO rounds ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                   [values.get(column) for column in columns])

    def reset_game(self, db, row):
        if row["player_id"]:
            db.execute("UPDATE rounds SET game_id = NULL WHERE game_id = ?", (row["id"],))
        else:
            db.execute("DELETE FROM rounds WHERE game_id = ?", (row["id"],))
        db.execute("DELETE FROM pending_rounds WHERE game_id = ?", (row["id"],))
