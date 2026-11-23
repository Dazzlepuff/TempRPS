import json
import math
import random
from pathlib import Path

MOVES = ("R", "P", "S")
NAMES = {"R": "Rock", "P": "Paper", "S": "Scissors"}
BEATS = {"R": "P", "P": "S", "S": "R"}

MAX_ORDER = 4
HALF_LIFE = 50.0
MARKOV_PRIOR = 8.0
OUTCOME_PRIOR = 12.0
TEMPERATURE = 1.0
SAVE_PATH = Path(__file__).resolve().parents[1] / "data" / "rps_history.json"


def empty_counts():
    return dict.fromkeys(MOVES, 0.0)


def mix(old, new, weight):
    return {move: (1 - weight) * old[move] + weight * new[move]
            for move in MOVES}


def weighted_distribution(counts):
    total = sum(counts.values())
    if total == 0:
        return {move: 1 / 3 for move in MOVES}
    return {move: counts[move] / total for move in MOVES}


def temperature_distribution(probabilities, temperature):
    if (isinstance(temperature, bool) or not isinstance(temperature, (int, float))
            or not math.isfinite(temperature) or temperature < 0):
        raise ValueError("Temperature must be finite and non-negative")
    highest = max(probabilities.values())
    if temperature == 0:
        weights = {move: float(probabilities[move] == highest) for move in MOVES}
    else:
        weights = {move: (probabilities[move] / highest) ** (1 / temperature)
                   for move in MOVES}
    return weighted_distribution(weights)


class AdaptiveRPS:
    def __init__(self, rounds=None):
        self.rounds = list(rounds or [])

    def _age_weight(self, age):
        return 0.5 ** (age / HALF_LIFE)

    def _markov_counts(self, human_moves, order):
        counts = empty_counts()
        n = len(human_moves)
        if n <= order:
            return counts

        context = human_moves[-order:] if order else []
        for i in range(order, n):
            if order and human_moves[i - order:i] != context:
                continue
            age = n - 1 - i
            counts[human_moves[i]] += self._age_weight(age)
        return counts

    def _outcome_counts(self, use_previous_move):
        counts = empty_counts()
        n = len(self.rounds)
        if n < 2:
            return counts

        previous_result = self.rounds[-1]["result"]
        previous_human_move = self.rounds[-1]["human"]

        for i in range(1, n):
            previous_round = self.rounds[i - 1]
            if previous_round["result"] != previous_result:
                continue
            if use_previous_move and previous_round["human"] != previous_human_move:
                continue

            age = n - 1 - i
            next_move = self.rounds[i]["human"]
            counts[next_move] += self._age_weight(age)
        return counts

    def predict(self):
        human_moves = [round_["human"] for round_ in self.rounds]
        probabilities = {move: 1 / 3 for move in MOVES}
        diagnostics = {"markov_support": {}, "outcome_support": {}}
        models = [
            ("markov_support", order, self._markov_counts(human_moves, order),
             3.0 if order == 0 else MARKOV_PRIOR, 1.0)
            for order in range(MAX_ORDER + 1)
        ]
        models.extend([
            ("outcome_support", "outcome", self._outcome_counts(False), OUTCOME_PRIOR, 0.25),
            ("outcome_support", "move_and_outcome", self._outcome_counts(True), OUTCOME_PRIOR, 0.30),
        ])
        for group, key, counts, prior, influence in models:
            support = sum(counts.values())
            diagnostics[group][key] = support
            if support:
                weight = influence * support / (support + prior)
                probabilities = mix(probabilities, weighted_distribution(counts), weight)
        return probabilities, diagnostics

    def choose_move(self, temperature=None):
        if temperature is None:
            temperature = TEMPERATURE
        probabilities, diagnostics = self.predict()
        highest = max(probabilities.values())
        tied = [move for move in MOVES
                if abs(probabilities[move] - highest) < 1e-12]
        predicted_human = random.choice(tied)
        sampling = temperature_distribution(probabilities, temperature)
        sampled_human = random.choices(
            MOVES, weights=[sampling[move] for move in MOVES], k=1)[0]
        ai_move = BEATS[sampled_human]
        diagnostics["ai_move_probabilities"] = {
            BEATS[move]: sampling[move] for move in MOVES}
        diagnostics["temperature"] = temperature
        return ai_move, predicted_human, probabilities, diagnostics

    def add_round(self, human_move, ai_move, prediction):
        if human_move == ai_move:
            result = "tie"
        elif BEATS[ai_move] == human_move:
            result = "win"
        else:
            result = "loss"

        self.rounds.append({
            "human": human_move,
            "ai": ai_move,
            "prediction": prediction,
            "result": result,
        })
        return result

    def stats(self):
        total = len(self.rounds)
        human_wins = sum(r["result"] == "win" for r in self.rounds)
        ai_wins = sum(r["result"] == "loss" for r in self.rounds)
        ties = total - human_wins - ai_wins
        correct = sum(r["prediction"] == r["human"] for r in self.rounds)
        accuracy = (100.0 * correct / total) if total else 0.0
        return (total, human_wins, ai_wins, ties, accuracy)


def load_game(path):
    if not path.exists():
        return AdaptiveRPS()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("Save file must be a JSON list")
        for round_ in data:
            if not isinstance(round_, dict):
                raise ValueError("Invalid round")
            if (round_.get("human") not in MOVES
                    or round_.get("ai") not in MOVES
                    or round_.get("prediction") not in MOVES
                    or round_.get("result") not in ("win", "loss", "tie")):
                raise ValueError("Invalid round")
        return AdaptiveRPS(data)
    except (OSError, ValueError, TypeError) as error:
        print(f"Could not load save file ({error}). Starting fresh.")
        return AdaptiveRPS()


def save_game(game, path):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(game.rounds, indent=2), encoding="utf-8")
    except OSError as error:
        print(f"Warning: could not save history: {error}")


def print_stats(game):
    total, human_wins, ai_wins, ties, accuracy = game.stats()
    print(f"\nRounds: {total} | You: {human_wins} | AI: {ai_wins} | Ties: {ties}")
    if total:
        print(f"AI next-move prediction accuracy: {accuracy:.1f}%")
        print("Random baseline: about 33.3% prediction accuracy.\n")
    else:
        print("No games played yet.\n")


def parse_move(user_input):
    if not isinstance(user_input, str):
        return None
    text = user_input.strip().upper()
    return {"R": "R", "ROCK": "R",
            "P": "P", "PAPER": "P",
            "S": "S", "SCISSORS": "S"}.get(text)


def main():
    game = load_game(SAVE_PATH)
    print("\nADAPTIVE ROCK-PAPER-SCISSORS")
    print("Learns move sequences (orders 0-4), recent habits, and outcomes.")
    print("Enter R, P, or S to play; STATS, RESET, or QUIT for commands.")
    print(f"History is stored in {SAVE_PATH.resolve()}")
    print_stats(game)

    while True:
        ai, predicted, probabilities, diagnostics = game.choose_move()
        try:
            command = input("Your move [R/P/S]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if command in ("q", "quit", "exit"):
            break
        if command in ("stats", "score"):
            print_stats(game)
            continue
        if command == "reset":
            game = AdaptiveRPS()
            save_game(game, SAVE_PATH)
            print("Learned history and score cleared.\n")
            continue

        human = parse_move(command)
        if human is None:
            print("Please type R, P, S, STATS, RESET, or QUIT.\n")
            continue

        result = game.add_round(human, ai, predicted)
        save_game(game, SAVE_PATH)
        outcome_text = {"win": "You win!", "loss": "AI wins!", "tie": "Tie!"}
        print(f"AI played {NAMES[ai]}. {outcome_text[result]}")
        print(f"AI guessed you would play {NAMES[predicted]}.")
        print("Prediction probabilities: " + " | ".join(
            f"{NAMES[move]} {probabilities[move]:.1%}" for move in MOVES))
        total, human_wins, ai_wins, ties, accuracy = game.stats()
        print(f"Score - You: {human_wins} | AI: {ai_wins} | Ties: {ties} "
              f"| Prediction accuracy: {accuracy:.1f}%\n")

    save_game(game, SAVE_PATH)
    print("Game saved. See you next time!")


if __name__ == "__main__":
    main()
