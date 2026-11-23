import json
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from backend.app import create_app
from backend.rps_ai import AdaptiveRPS, BEATS, MOVES, temperature_distribution
from backend.storage import Storage


class ModelTests(unittest.TestCase):
    def test_temperature_scaling(self):
        probabilities = {"R": 0.4, "P": 0.6, "S": 0}
        for temperature, expected in ((1, 0.6), (0.5, 9 / 13), (0, 1)):
            distribution = temperature_distribution(probabilities, temperature)
            self.assertAlmostEqual(distribution["P"], expected)
            self.assertEqual(distribution["S"], 0)
            self.assertAlmostEqual(sum(distribution.values()), 1)
        self.assertEqual(temperature_distribution({"R": 0.5, "P": 0.5, "S": 0}, 0),
                         {"R": 0.5, "P": 0.5, "S": 0})

    def test_sampling_and_prediction_are_independent(self):
        game = AdaptiveRPS()
        probabilities = {"R": 0.07, "P": 0.9, "S": 0.03}
        with patch.object(game, "predict", side_effect=lambda: (probabilities, {})), \
                patch("backend.rps_ai.random.choices", return_value=["R"]):
            ai, prediction, _, diagnostics = game.choose_move(1)
        self.assertEqual(ai, BEATS["R"])
        self.assertEqual(prediction, "P")
        for human, probability in probabilities.items():
            self.assertAlmostEqual(diagnostics["ai_move_probabilities"][BEATS[human]], probability)
        self.assertEqual(game.rounds, [])

    def test_learning_and_outcomes(self):
        for human in MOVES:
            for ai in MOVES:
                expected = ("tie" if human == ai else "win"
                            if (human, ai) in (("R", "S"), ("P", "R"), ("S", "P")) else "loss")
                self.assertEqual(AdaptiveRPS().add_round(human, ai, "R"), expected)
        for sequence, expected in (("R" * 30, "R"), ("RP" * 30, "R")):
            game = AdaptiveRPS()
            for human in sequence:
                game.add_round(human, "P", "R")
            probabilities, _ = game.predict()
            self.assertGreater(probabilities[expected], 0.95)


class APITests(unittest.TestCase):
    def setUp(self):
        database_path = Path(__file__).with_name(f".test-{uuid4().hex}.sqlite3")
        self.addCleanup(database_path.unlink, missing_ok=True)
        self.config = {"TESTING": True,
                       "DATABASE": str(database_path),
                       "CORS_ORIGINS": "http://localhost:3000"}
        self.app = create_app(self.config)
        self.client = self.app.test_client()
        response = self.client.post("/api/games", json={"temperature": 0.5})
        self.assertEqual(response.status_code, 201)
        self.url = "/api/games/" + response.json["game_id"]

    def prepare(self):
        response = self.client.post(self.url + "/rounds", json={})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(set(response.json), {"round_id"})
        return response.json["round_id"]

    def test_complete_round_and_history(self):
        round_id = self.prepare()
        self.assertEqual(self.client.get(self.url + "/stats").json["rounds"], 0)
        self.assertEqual(self.client.get(self.url + "/history").json["rounds"], [])
        response = self.client.post(self.url + "/rounds/" + round_id, json={"human": " rock "})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["human"], "R")
        self.assertEqual(response.json["round_id"], round_id)
        self.assertEqual(response.json["stats"]["rounds"], 1)
        self.assertEqual(set(response.json), {"round_id", "human", "ai", "result", "player_id", "stats"})
        self.assertEqual(set(response.json["stats"]), {"rounds", "human_wins", "ai_wins", "ties"})
        history = self.client.get(self.url + "/history?offset=0&limit=1").json
        self.assertEqual(history["total"], 1)
        self.assertEqual(history["rounds"][0]["ai"], response.json["ai"])
        self.assertNotIn("prediction", history["rounds"][0])
        self.assertNotIn("probabilities", history["rounds"][0])
        self.assertIsNone(self.client.get(self.url).json["pending_round_id"])

    def test_preparation_is_idempotent_and_completion_cannot_repeat(self):
        round_id = self.prepare()
        self.assertEqual(self.prepare(), round_id)
        url = self.url + "/rounds/" + round_id
        self.assertEqual(self.client.post(url, json={"human": "P"}).status_code, 200)
        self.assertEqual(self.client.post(url, json={"human": "P"}).status_code, 409)
        self.assertEqual(self.client.get(self.url + "/stats").json["rounds"], 1)

    def test_concurrent_submissions_record_one_round(self):
        round_id = self.prepare()
        url = self.url + "/rounds/" + round_id

        def submit(_):
            with self.app.test_client() as client:
                return client.post(url, json={"human": "R"}).status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(sorted(executor.map(submit, range(2))), [200, 409])
        self.assertEqual(self.client.get(self.url + "/stats").json["rounds"], 1)

    def test_restart_keeps_pending_move_and_learning(self):
        with patch("backend.rps_ai.random.choices", return_value=["R"]):
            round_id = self.prepare()
        other = create_app(self.config).test_client()
        response = other.post(self.url + "/rounds/" + round_id, json={"human": "P"})
        self.assertEqual(response.json["ai"], "P")
        self.assertEqual(response.json["result"], "tie")
        self.assertEqual(self.client.get(self.url + "/stats").json["rounds"], 1)

    def test_temperature_reset_and_game_isolation(self):
        second = self.client.post("/api/games", json={}).json["game_id"]
        self.assertEqual(self.client.patch(self.url, json={"temperature": 0.25}).status_code, 200)
        round_id = self.prepare()
        self.assertEqual(self.client.patch(self.url, json={"temperature": 1}).status_code, 409)
        self.client.post(self.url + "/rounds/" + round_id, json={"human": "S"})
        self.assertEqual(self.client.get("/api/games/" + second + "/stats").json["rounds"], 0)
        reset = self.client.post(self.url + "/reset", json={}).json
        self.assertEqual(reset["stats"]["rounds"], 0)
        self.assertEqual(reset["temperature"], 0.25)
        self.assertIsNone(reset["pending_round_id"])
        self.assertEqual(self.client.delete(self.url).status_code, 204)
        self.assertEqual(self.client.get(self.url).status_code, 404)


    def test_invalid_inputs_and_json_errors(self):
        for temperature in (-1, True, "0.5", None, float("nan"), float("inf")):
            with self.subTest(temperature=temperature):
                self.assertEqual(self.client.patch(self.url, json={"temperature": temperature}).status_code, 400)
        for data in ([], {"unknown": 1}, {"temperature": {}}):
            self.assertEqual(self.client.post("/api/games", json=data).status_code, 400)
        self.assertEqual(self.client.post("/api/games", data="{", content_type="application/json").status_code, 400)
        self.assertEqual(self.client.post("/api/games", data="text").status_code, 415)
        round_id = self.prepare()
        for human in (None, {}, 1, "invalid"):
            self.assertEqual(self.client.post(self.url + "/rounds/" + round_id,
                                             json={"human": human}).status_code, 400)
        self.assertEqual(self.client.get(self.url + "/stats").json["rounds"], 0)
        for query in ("limit=0", "limit=1001", "offset=-1", "limit=abc"):
            self.assertEqual(self.client.get(self.url + "/history?" + query).status_code, 400)
        response = self.client.get("/does-not-exist")
        self.assertEqual(response.json["error"]["status"], 404)
        response = self.client.put(self.url)
        self.assertEqual(response.status_code, 405)
        self.assertIn("GET", response.headers["Allow"])


    def player_game(self, player_id="login-123", **options):
        response = self.client.post("/api/games", json={"player_id": player_id, **options})
        self.assertEqual(response.status_code, 201)
        return "/api/games/" + response.json["game_id"]

    def player_model(self, player_id="login-123"):
        store = Storage(self.config["DATABASE"])
        with store.transaction() as db:
            return store.model(db, "player", player_id)

    def play(self, url, human="R"):
        round_id = self.client.post(url + "/rounds", json={}).json["round_id"]
        response = self.client.post(url + "/rounds/" + round_id, json={"human": human})
        self.assertEqual(response.status_code, 200)
        return response.json

    def test_player_learning_carries_across_games_and_restart(self):
        first = self.player_game(temperature=0.25)
        for _ in range(10):
            self.play(first)
        second = self.player_game()
        self.assertEqual(self.client.get(second).json["temperature"], 0.25)
        self.assertEqual(self.client.get(second + "/stats").json["rounds"], 0)
        model = self.player_model()
        self.assertEqual(len(model.rounds), 10)
        self.assertGreater(model.predict()[0]["R"], 0.95)
        self.play(second, "P")
        other = create_app(self.config).test_client()
        self.assertEqual(other.get("/api/players/login-123").json["stats"]["rounds"], 11)
        self.assertEqual(len(self.player_model().rounds), 11)
        self.assertEqual(other.get("/api/players/login-123/games?limit=1").json["total"], 2)
        self.player_game("another-user")
        self.assertEqual(len(self.player_model("another-user").rounds), 0)

    def test_player_history_survives_game_reset_and_deletion(self):
        game = self.player_game()
        self.play(game)
        self.client.post(game + "/reset", json={})
        self.assertEqual(self.client.get(game + "/stats").json["rounds"], 0)
        self.assertEqual(len(self.player_model().rounds), 1)
        self.play(game, "P")
        self.client.delete(game)
        history = self.client.get("/api/players/login-123/history?offset=1&limit=1").json
        self.assertEqual(history["total"], 2)
        self.assertEqual(history["rounds"][0]["human"], "P")
        self.assertIsNone(history["rounds"][0]["game_id"])
        self.assertIn("created_at", history["rounds"][0])
        self.assertNotIn("ai_move_probabilities", history["rounds"][0])

    def test_temperature_sampling_and_model_details_stay_on_server(self):
        url = self.player_game(temperature=0.5)
        probabilities = {"R": 0.4, "P": 0.6, "S": 0.0}
        with patch.object(AdaptiveRPS, "predict", return_value=(probabilities, {})), \
                patch("backend.rps_ai.random.choices", return_value=["P"]) as sample:
            prepared = self.client.post(url + "/rounds", json={})
        self.assertEqual(set(prepared.json), {"round_id"})
        weights = sample.call_args.kwargs["weights"]
        for actual, expected in zip(weights, (4 / 13, 9 / 13, 0)):
            self.assertAlmostEqual(actual, expected)
        response = self.client.post(url + "/rounds/" + prepared.json["round_id"], json={"human": "R"})
        self.assertEqual(response.json["ai"], "S")
        private = {"prediction", "probabilities", "ai_move_probabilities", "diagnostics", "prediction_accuracy"}
        records = [response.json, response.json["stats"],
                   self.client.get(url + "/history").json["rounds"][0],
                   self.client.get("/api/players/login-123/history").json["rounds"][0],
                   self.client.get("/api/players/login-123/stats").json]
        for record in records:
            self.assertFalse(private.intersection(record))
        self.assertEqual(self.client.get(url + "/prediction").status_code, 404)
        self.assertEqual(self.client.get("/api/players/login-123/prediction").status_code, 404)
        self.assertIn("prediction", self.player_model().rounds[0])

    def test_attach_existing_game_and_reject_reassignment(self):
        self.play(self.url)
        response = self.client.patch(self.url, json={"player_id": "login-123"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/players/login-123/stats").json["rounds"], 1)
        self.assertEqual(self.client.patch(self.url, json={"player_id": "another"}).status_code, 409)
        self.client.patch(self.url, json={"player_id": "login-123"})
        self.assertEqual(self.client.get("/api/players/login-123/stats").json["rounds"], 1)

    def test_reset_player_clears_learning_and_all_pending_rounds(self):
        first, second = self.player_game(), self.player_game()
        self.play(first)
        round_id = self.client.post(second + "/rounds", json={}).json["round_id"]
        response = self.client.post("/api/players/login-123/reset", json={})
        self.assertEqual(response.json["stats"]["rounds"], 0)
        self.assertEqual(self.client.get(first + "/stats").json["rounds"], 0)
        self.assertIsNone(self.client.get(second).json["pending_round_id"])
        self.assertEqual(self.client.post(second + "/rounds/" + round_id, json={"human": "R"}).status_code, 409)

    def test_player_registration_preferences_and_validation(self):
        url = "/api/players/login-123"
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post("/api/players", json={"player_id": "login-123", "temperature": 0.5}).status_code, 201)
        existing = self.client.post("/api/players", json={"player_id": "login-123", "temperature": 1})
        self.assertEqual(existing.status_code, 200)
        self.assertEqual(existing.json["temperature"], 0.5)
        self.assertEqual(self.client.patch(url, json={"temperature": 0.25}).status_code, 200)
        game = self.player_game()
        self.assertEqual(self.client.get(game).json["temperature"], 0.25)
        for player_id in (None, "", "../other", "bad id", {}, True, "a" * 201):
            self.assertEqual(self.client.post("/api/games", json={"player_id": player_id}).status_code, 400)

    def test_authenticated_player_access(self):
        first = self.player_game("alice")
        second = self.player_game("bob")
        config = {**self.config, "PLAYER_ID_RESOLVER": lambda req: {"Bearer valid": "alice"}.get(req.headers.get("Authorization"))}
        client = create_app(config).test_client()
        headers = {"Authorization": "Bearer valid"}
        self.assertEqual(client.get(first).status_code, 401)
        self.assertEqual(client.get(first, headers=headers).status_code, 200)
        self.assertEqual(client.get(second, headers=headers).status_code, 403)
        self.assertEqual(client.get("/api/players/bob/history", headers=headers).status_code, 403)
        self.assertEqual(client.post("/api/games", json={"player_id": "bob"}, headers=headers).status_code, 403)
        self.assertEqual(client.post("/api/players", json={"player_id": "bob"}, headers=headers).status_code, 403)
        created = client.post("/api/games", json={}, headers=headers)
        self.assertEqual(created.json["player_id"], "alice")
        self.assertEqual(client.get("/api/health").status_code, 200)


class MigrationTests(unittest.TestCase):
    def test_existing_games_and_pending_moves_are_preserved(self):
        path = Path(__file__).with_name(f".test-{uuid4().hex}.sqlite3")
        self.addCleanup(path.unlink, missing_ok=True)
        pending = {"round_id": "pending-1", "ai": "P", "prediction": "R",
                   "probabilities": {"R": 1, "P": 0, "S": 0},
                   "diagnostics": {"ai_move_probabilities": {"R": 0, "P": 1, "S": 0}}}
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("CREATE TABLE games (id TEXT PRIMARY KEY, temperature REAL, rounds TEXT, pending TEXT)")
            records = [{"human": "R", "ai": "P", "prediction": "R", "result": "loss", "round_id": "past-1"}]
            db.execute("INSERT INTO games VALUES (?, ?, ?, ?)", ("existing-game", 0.5, json.dumps(records), json.dumps(pending)))
        config = {"TESTING": True, "DATABASE": str(path)}
        client = create_app(config).test_client()
        url = "/api/games/existing-game"
        self.assertEqual(client.get(url).json["stats"]["rounds"], 1)
        self.assertEqual(client.get(url).json["pending_round_id"], "pending-1")
        response = client.post(url + "/rounds/pending-1", json={"human": "R"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["ai"], "P")
        client = create_app(config).test_client()
        self.assertEqual(client.get(url + "/history").json["total"], 2)
        self.assertEqual(client.patch(url, json={"player_id": "legacy-player"}).status_code, 200)
        self.assertEqual(client.get("/api/players/legacy-player/stats").json["rounds"], 2)
        with closing(sqlite3.connect(path)) as db, db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM games_legacy_v1").fetchone()[0], 1)
            self.assertNotIn("rounds", [row[1] for row in db.execute("PRAGMA table_info(games)")])

    def test_failed_migration_rolls_back_original_schema(self):
        path = Path(__file__).with_name(f".test-{uuid4().hex}.sqlite3")
        self.addCleanup(path.unlink, missing_ok=True)
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("CREATE TABLE games (id TEXT PRIMARY KEY, temperature REAL, rounds TEXT, pending TEXT)")
            db.execute("INSERT INTO games VALUES ('bad-game', 1, 'not-json', NULL)")
        with self.assertRaises(ValueError):
            create_app({"DATABASE": str(path)})
        with closing(sqlite3.connect(path)) as db, db:
            self.assertIn("rounds", [row[1] for row in db.execute("PRAGMA table_info(games)")])


if __name__ == "__main__":
    unittest.main()
