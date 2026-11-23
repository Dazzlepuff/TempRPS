# Machine-Learning-Rock-Paper-Scissors

PHP account pages with a Python/Flask adaptive Rock-Paper-Scissors API.

## Structure

- `public/`: PHP pages and stylesheet; the web server document root.
- `backend/`: game engine, Flask API, SQLite storage, and PHP account helpers.
- `database/accounts.sql`: account database schema.
- `config/`: ignored local database credentials and runtime paths.
- `data/`: ignored local account database and game history.
- `scripts/`: account database setup, start, and shutdown.
- `tests/`: gameplay, persistence, ownership, and account regression tests.

## Account website

Run commands from this repository directory. The configured portable installation uses PHP and MariaDB under `%USERPROFILE%\Program Files`.

Start the local account database:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-accounts-db.ps1
```

Start the website:

```powershell
& "$env:USERPROFILE\Program Files\PHP\php.exe" -S 127.0.0.1:8000 -t .\public
```

Open <http://127.0.0.1:8000/>. Set `public/` as the document root so private configuration, source code, and data files stay outside the served directory. No router script is needed.

Registration creates an account; no default website credentials are supplied. Usernames are 1-64 characters with case-insensitive uniqueness. Passwords must be 8-72 bytes and are stored as hashes. Login assigns the stable player identity `account:<AccountID>`.

Stop PHP with Ctrl+C. Stop the database with:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\stop-accounts-db.ps1
```

MariaDB listens on `127.0.0.1:3307` and is not installed as a Windows service. Start it again after rebooting. Account data survives restarts.

For another Windows machine, install portable PHP with `mysqli` and `mbstring`, extract the official MariaDB Windows ZIP under `%USERPROFILE%\Program Files\MariaDB`, and run:

```powershell
py -B .\scripts\setup_accounts.py
```

Use `--mariadb-dir` for a different extracted location. Setup generates credentials and initializes a fresh database without overwriting existing data. It does not migrate old encrypted-password accounts.

PHP reads `config/accounts.local.json`. Connection settings can be overridden using `RPS_DB_HOST`, `RPS_DB_PORT`, `RPS_DB_NAME`, `RPS_DB_USER`, and `RPS_DB_PASSWORD`. Do not commit local configuration or database files.

## Game API

Requires Python 3.9+ and Flask. Install dependencies in a virtual environment:

```powershell
py -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m flask --app backend.app run --port 5000
```

The API uses `data/rps.sqlite3`; override the location with `RPS_DATABASE`. Existing JSON-per-game databases migrate on startup. The command-line game uses `data/rps_history.json` and runs with `py -m backend.rps_ai`.

| Request | Purpose |
|---|---|
| `POST /api/games` | Create a game; optional `player_id` and `temperature` |
| `GET /api/games/{game_id}` | Game state and score |
| `PATCH /api/games/{game_id}` | Set temperature or link an anonymous game to a player |
| `POST /api/games/{game_id}/rounds` | Commit the AI move; return a hidden round ID |
| `POST /api/games/{game_id}/rounds/{round_id}` | Submit `human` as `R`, `P`, or `S` and reveal the result |
| `GET /api/games/{game_id}/stats`, `/history` | Game scores and completed rounds |
| `POST /api/games/{game_id}/reset` | Reset the game score; retain linked player learning |
| `DELETE /api/games/{game_id}` | Delete the game; retain linked player learning |
| `POST /api/players` | Create or retrieve a player using `player_id` |
| `GET /api/players/{player_id}` | Player preferences and lifetime statistics |
| `PATCH /api/players/{player_id}` | Set default temperature for future games |
| `GET /api/players/{player_id}/stats`, `/history`, `/games` | Saved player data |
| `POST /api/players/{player_id}/reset` | Clear lifetime history and pending rounds |
| `GET /api/health` | API health |

POST/PATCH bodies must be JSON objects; send `{}` when no parameters are needed. History and player game listings accept `offset` and `limit` (1-1000). Results use the human perspective: `win`, `loss`, or `tie`.

Temperature defaults to `1.0`. Lower values favor the strongest prediction; zero always counters a strongest prediction. Players retain learned history across games. Configure `RPS_CORS_ORIGINS` for explicit frontend origins when needed.

Prediction and temperature sampling run entirely on the server. Preparing a round returns only its ID; submitting the human move reveals the AI move, result, and score. Responses and history omit internal predictions, probability distributions, and diagnostics. Prediction endpoints are not exposed. Temperature remains configurable through game/player settings. Internal model data remains stored server-side.

## Current integration status

Registration and login work. The website's game page currently displays account/guest identity. Browser gameplay and the authenticated PHP-to-Flask bridge are not implemented. Flask's `PLAYER_ID_RESOLVER` hook must be connected to verified login identity before exposing player-specific API access; player IDs alone are lookup keys, not authentication.

## Tests

```powershell
.\.venv\Scripts\python -B -m unittest discover -s tests -p test_rps.py
py -B .\tests\test_accounts.py
```

Account tests need the local database running and use a temporary PHP server. They remove their own test accounts and do not restart the database.
