import json
import os
import re
import sqlite3
from pathlib import Path
from uuid import uuid4

from flask import Flask, abort, g, jsonify, request
from werkzeug.exceptions import HTTPException

from .rps_ai import TEMPERATURE, parse_move, temperature_distribution
from .storage import Storage


def stats(game):
    return dict(zip(
        ("rounds", "human_wins", "ai_wins", "ties"),
        game.stats()[:4],
    ))


def public_round(record):
    fields = ("round_id", "game_id", "player_id", "human", "ai", "result", "created_at")
    return {key: record[key] for key in fields if key in record}


def body(allowed):
    data = request.get_json()
    if not isinstance(data, dict):
        abort(400, description="Body must be a JSON object")
    if data.keys() - set(allowed):
        abort(400, description="Unknown fields: " + ", ".join(sorted(data.keys() - set(allowed))))
    return data


def validate_temperature(value):
    try:
        temperature_distribution({"R": 1 / 3, "P": 1 / 3, "S": 1 / 3}, value)
    except (ValueError, OverflowError) as error:
        abort(400, description=str(error))
    return value


def validate_player_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:@|+\-]{0,199}", value):
        abort(400, description="player_id must be 1-200 letters, digits, or . _ : @ | + -; start with a letter or digit")
    return value


def pagination():
    try:
        offset = int(request.args.get("offset", 0))
        limit = int(request.args.get("limit", 100))
    except ValueError:
        abort(400, description="offset and limit must be integers")
    if offset < 0 or not 1 <= limit <= 1000:
        abort(400, description="offset must be non-negative; limit must be 1 to 1000")
    return offset, limit


def create_app(config=None):
    app = Flask(__name__)
    app.config.from_mapping(
        DATABASE=os.environ.get("RPS_DATABASE", str(Path(__file__).resolve().parents[1] / "data" / "rps.sqlite3")),
        CORS_ORIGINS=os.environ.get("RPS_CORS_ORIGINS", ""),
        MAX_CONTENT_LENGTH=16384,
        PLAYER_ID_RESOLVER=None,
    )
    app.config.update(config or {})
    Path(app.config["DATABASE"]).parent.mkdir(parents=True, exist_ok=True)
    store = Storage(app.config["DATABASE"])
    database = store.transaction

    def check_owner(player_id):
        if app.config["PLAYER_ID_RESOLVER"] and player_id != g.get("player_id"):
            abort(403, description="Player does not match the authenticated user")

    def load_row(db, game_id):
        row = store.game(db, game_id)
        if row is None:
            abort(404, description="Game not found")
        check_owner(row["player_id"])
        return row

    def load(db, game_id):
        row = load_row(db, game_id)
        return row, store.model(db, "game", game_id)

    def load_player(db, player_id):
        validate_player_id(player_id)
        check_owner(player_id)
        row = store.player(db, player_id)
        if row is None:
            abort(404, description="Player not found")
        return row

    def learned_model(db, row, game):
        return store.model(db, "player", row["player_id"]) if row["player_id"] else game

    def player_state(db, row):
        return {"player_id": row["id"], "temperature": row["temperature"],
                "created_at": row["created_at"], "stats": stats(store.model(db, "player", row["id"]))}

    def describe(row, game):
        pending = json.loads(row["pending"]) if row["pending"] else None
        return {
            "game_id": row["id"],
            "player_id": row["player_id"],
            "created_at": row["created_at"],
            "temperature": row["temperature"],
            "pending_round_id": pending["round_id"] if pending else None,
            "stats": stats(game),
        }

    @app.before_request
    def authenticate():
        resolver = app.config["PLAYER_ID_RESOLVER"]
        if resolver and request.method != "OPTIONS" and request.path.startswith("/api/") and request.endpoint != "health":
            player_id = resolver(request)
            if not isinstance(player_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:@|+\-]{0,199}", player_id):
                abort(401, description="Authentication required")
            g.player_id = player_id

    @app.after_request
    def response_headers(response):
        response.headers["Cache-Control"] = "no-store"
        origins = {origin.strip() for origin in app.config["CORS_ORIGINS"].split(",")}
        origin = request.headers.get("Origin")
        if origin and origin in origins:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, DELETE, OPTIONS"
            response.vary.add("Origin")
        return response

    @app.errorhandler(HTTPException)
    def http_error(error):
        response = error.get_response()
        response.data = app.json.dumps({"error": {"status": error.code, "message": error.description}})
        response.content_type = "application/json"
        return response

    @app.errorhandler(sqlite3.Error)
    def database_error(error):
        app.logger.exception("Database operation failed")
        return jsonify(error={"status": 503, "message": "Storage unavailable; retry the request"}), 503

    @app.get("/api/health")
    def health():
        with database() as db:
            db.execute("SELECT 1 FROM games LIMIT 1")
        return {"status": "ok"}

    @app.post("/api/games")
    def create_game():
        data = body({"temperature", "player_id"})
        player_id = validate_player_id(data["player_id"]) if "player_id" in data else g.get("player_id")
        check_owner(player_id)
        if "temperature" in data:
            validate_temperature(data["temperature"])
        game_id = uuid4().hex
        with database(True) as db:
            player = store.ensure_player(db, player_id, data.get("temperature", TEMPERATURE)) if player_id else None
            temperature = data.get("temperature", player["temperature"] if player else TEMPERATURE)
            db.execute("INSERT INTO games (id, player_id, temperature) VALUES (?, ?, ?)",
                       (game_id, player_id, temperature))
            row, game = load(db, game_id)
            result = describe(row, game)
        return result, 201

    @app.get("/api/games/<game_id>")
    def get_game(game_id):
        with database() as db:
            row, game = load(db, game_id)
            return describe(row, game)

    @app.get("/api/games/<game_id>/stats")
    def get_stats(game_id):
        with database() as db:
            _, game = load(db, game_id)
            return stats(game)

    @app.get("/api/games/<game_id>/history")
    def history(game_id):
        offset, limit = pagination()
        with database() as db:
            load_row(db, game_id)
            return {"rounds": [public_round(record) for record in store.records(db, "game", game_id, offset, limit)],
                    "total": store.count(db, "game", game_id),
                    "offset": offset, "limit": limit}

    @app.patch("/api/games/<game_id>")
    def configure_game(game_id):
        data = body({"temperature", "player_id"})
        if not data:
            abort(400, description="temperature or player_id is required")
        if "temperature" in data:
            validate_temperature(data["temperature"])
        if "player_id" in data:
            validate_player_id(data["player_id"])
            check_owner(data["player_id"])
        with database(True) as db:
            row, _ = load(db, game_id)
            if row["pending"]:
                abort(409, description="Complete or reset the pending round before changing game settings")
            if "player_id" in data:
                if row["player_id"] and row["player_id"] != data["player_id"]:
                    abort(409, description="A game cannot be reassigned to a different player")
                store.ensure_player(db, data["player_id"], TEMPERATURE)
                db.execute("UPDATE games SET player_id = ? WHERE id = ?", (data["player_id"], game_id))
                db.execute("UPDATE rounds SET player_id = ? WHERE game_id = ?", (data["player_id"], game_id))
            if "temperature" in data:
                db.execute("UPDATE games SET temperature = ? WHERE id = ?", (data["temperature"], game_id))
            row, game = load(db, game_id)
            return describe(row, game)

    @app.post("/api/games/<game_id>/rounds")
    def prepare_round(game_id):
        body(set())
        with database(True) as db:
            row, game = load(db, game_id)
            if row["pending"]:
                pending = json.loads(row["pending"])
            else:
                model = learned_model(db, row, game)
                ai, guess, probabilities, diagnostics = model.choose_move(row["temperature"])
                pending = {"round_id": uuid4().hex, "ai": ai, "prediction": guess,
                           "probabilities": probabilities, "diagnostics": diagnostics}
                db.execute("INSERT INTO pending_rounds (game_id, data) VALUES (?, ?)",
                           (game_id, json.dumps(pending)))
            return {"round_id": pending["round_id"]}, 201

    @app.post("/api/games/<game_id>/rounds/<round_id>")
    def finish_round(game_id, round_id):
        data = body({"human"})
        human = parse_move(data.get("human"))
        if human is None:
            abort(400, description="human must be R, P, S, Rock, Paper, or Scissors")
        with database(True) as db:
            row, game = load(db, game_id)
            pending = json.loads(row["pending"]) if row["pending"] else None
            if not pending or pending["round_id"] != round_id:
                abort(409, description="Round is not pending; it may already be completed or reset")
            game.add_round(human, pending["ai"], pending["prediction"])
            record = {**game.rounds[-1], "round_id": round_id, "probabilities": pending["probabilities"],
                      "ai_move_probabilities": pending["diagnostics"]["ai_move_probabilities"],
                      "temperature": row["temperature"]}
            store.add_round(db, game_id, row["player_id"], record)
            db.execute("DELETE FROM pending_rounds WHERE game_id = ?", (game_id,))
            return {**public_round(record), "player_id": row["player_id"], "stats": stats(game)}

    @app.post("/api/games/<game_id>/reset")
    def reset(game_id):
        body(set())
        with database(True) as db:
            row, _ = load(db, game_id)
            store.reset_game(db, row)
            row, game = load(db, game_id)
            return describe(row, game)

    @app.delete("/api/games/<game_id>")
    def delete_game(game_id):
        with database(True) as db:
            row, _ = load(db, game_id)
            store.reset_game(db, row)
            db.execute("DELETE FROM games WHERE id = ?", (game_id,))
        return "", 204

    @app.post("/api/players")
    def create_player():
        data = body({"player_id", "temperature"})
        player_id = validate_player_id(data.get("player_id"))
        check_owner(player_id)
        temperature = validate_temperature(data.get("temperature", TEMPERATURE))
        with database(True) as db:
            exists = store.player(db, player_id) is not None
            row = store.ensure_player(db, player_id, temperature)
            return player_state(db, row), 200 if exists else 201

    @app.get("/api/players/<player_id>")
    def get_player(player_id):
        with database() as db:
            return player_state(db, load_player(db, player_id))

    @app.patch("/api/players/<player_id>")
    def configure_player(player_id):
        data = body({"temperature"})
        temperature = validate_temperature(data.get("temperature"))
        with database(True) as db:
            load_player(db, player_id)
            db.execute("UPDATE players SET temperature = ? WHERE id = ?", (temperature, player_id))
            return player_state(db, load_player(db, player_id))

    @app.get("/api/players/<player_id>/stats")
    def player_stats(player_id):
        with database() as db:
            load_player(db, player_id)
            return stats(store.model(db, "player", player_id))

    @app.get("/api/players/<player_id>/history")
    def player_history(player_id):
        offset, limit = pagination()
        with database() as db:
            load_player(db, player_id)
            return {"rounds": [public_round(record) for record in store.records(db, "player", player_id, offset, limit)],
                    "total": store.count(db, "player", player_id), "offset": offset, "limit": limit}

    @app.get("/api/players/<player_id>/games")
    def player_games(player_id):
        offset, limit = pagination()
        with database() as db:
            load_player(db, player_id)
            games = db.execute("""SELECT id, temperature, created_at FROM games WHERE player_id = ?
                ORDER BY created_at, id LIMIT ? OFFSET ?""", (player_id, limit, offset)).fetchall()
            total = db.execute("SELECT COUNT(*) FROM games WHERE player_id = ?", (player_id,)).fetchone()[0]
            return {"games": [{"game_id": row["id"], "temperature": row["temperature"],
                               "created_at": row["created_at"]} for row in games],
                    "total": total, "offset": offset, "limit": limit}

    @app.post("/api/players/<player_id>/reset")
    def reset_player(player_id):
        body(set())
        with database(True) as db:
            row = load_player(db, player_id)
            db.execute("DELETE FROM pending_rounds WHERE game_id IN (SELECT id FROM games WHERE player_id = ?)",
                       (player_id,))
            db.execute("DELETE FROM rounds WHERE player_id = ?", (player_id,))
            return player_state(db, row)

    return app
