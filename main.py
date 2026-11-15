
import os
import re
import uuid
import secrets
import hashlib
import json
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import asyncpg
from dotenv import load_dotenv
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field


# ==========================================
# CONFIGURATION
# ==========================================

BASE_DIR = Path(__file__).resolve().parent

load_dotenv(BASE_DIR / ".env")

DATABASE_URL = os.getenv("DATABASE_URL")

SESSION_DURATION_DAYS = 30
COOKIE_NAME = "timeguess_session"

MAX_ROOM_PLAYERS = 4
ALLOWED_SCORES = {3, 5, 7, 10, 15, 20}
ROOM_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

password_hasher = PasswordHasher()
db_pool = None


# ==========================================
# DATABASE
# ==========================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    global db_pool

    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL not found. Check your .env file."
        )

    db_pool = await asyncpg.create_pool(
        DATABASE_URL,
        ssl="require",
        min_size=1,
        max_size=5,
        command_timeout=30
    )

    print("TIMEGUESS DATABASE: CONNECTED")

    yield

    if db_pool:
        await db_pool.close()
        print("TIMEGUESS DATABASE: DISCONNECTED")


app = FastAPI(
    title="TIMEGUESS",
    version="2.1.0",
    lifespan=lifespan
)


# ==========================================
# REQUEST MODELS
# ==========================================

class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=20)
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=20)
    password: str = Field(min_length=8, max_length=128)


class CreateRoomRequest(BaseModel):
    target_score: int = Field(default=7, ge=1, le=20)


class JoinRoomRequest(BaseModel):
    code: str = Field(min_length=6, max_length=6)


# ==========================================
# HELPERS
# ==========================================

def normalize_username(username: str) -> str:
    return username.strip()


def validate_username(username: str):
    pattern = r"^[A-Za-zА-Яа-яЁё0-9_]{3,20}$"

    if not re.fullmatch(pattern, username):
        raise HTTPException(
            status_code=400,
            detail=(
                "Имя должно содержать от 3 до 20 символов: "
                "буквы, цифры или нижнее подчёркивание."
            )
        )


def normalize_room_code(code: str) -> str:
    return code.strip().upper()


def generate_session_token() -> str:
    return secrets.token_urlsafe(48)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()


def set_session_cookie(
    response: Response,
    request: Request,
    token: str
):
    forwarded_proto = request.headers.get(
        "x-forwarded-proto", ""
    )

    secure = (
        request.url.scheme == "https"
        or forwarded_proto == "https"
    )

    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=SESSION_DURATION_DAYS * 24 * 60 * 60,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/"
    )


def delete_session_cookie(response: Response):
    response.delete_cookie(
        key=COOKIE_NAME,
        httponly=True,
        samesite="lax",
        path="/"
    )


def public_profile(row):
    return {
        "id": str(row["id"]),
        "public_id": row["public_id"],
        "username": row["username"],
        "registered_at": row["registered_at"].isoformat(),
        "wins": row["wins"],
        "total_points": row["total_points"],
        "rounds_played": row["rounds_played"],
        "games_played": row["games_played"],
        "win_percentage": (
            round((row["wins"] / row["games_played"]) * 100, 1)
            if row["games_played"] else 0.0
        ),
        "longest_streak": row["longest_streak"]
    }


def room_player_data(row):
    return {
        "user_id": str(row["user_id"]),
        "username": row["username"],
        "connected": row["connected"],
        "score": row["score"]
    }


async def create_session(connection, user_id: uuid.UUID):
    token = generate_session_token()
    token_hash = hash_session_token(token)

    expires_at = (
        datetime.now(timezone.utc)
        + timedelta(days=SESSION_DURATION_DAYS)
    )

    await connection.execute(
        """
        INSERT INTO user_sessions (
            user_id,
            token_hash,
            expires_at
        )
        VALUES ($1, $2, $3)
        """,
        user_id,
        token_hash,
        expires_at
    )

    return token


async def get_room_by_code(connection, code: str):
    return await connection.fetchrow(
        """
        SELECT
            r.id,
            r.code,
            r.host_id,
            r.target_score,
            r.status,
            r.created_at,
            p.username AS host_username
        FROM game_rooms r
        JOIN profiles p ON p.id = r.host_id
        WHERE r.code = $1
        """,
        code
    )


async def get_room_players(connection, room_id):
    return await connection.fetch(
        """
        SELECT
            rp.user_id,
            p.username,
            rp.connected,
            rp.score
        FROM room_players rp
        JOIN profiles p ON p.id = rp.user_id
        WHERE rp.room_id = $1
        ORDER BY rp.joined_at ASC
        """,
        room_id
    )


async def build_room_response(connection, room):
    players = await get_room_players(
        connection,
        room["id"]
    )

    return {
        "success": True,
        "room": {
            "id": str(room["id"]),
            "code": room["code"],
            "host_id": str(room["host_id"]),
            "host": room["host_username"],
            "target_score": room["target_score"],
            "status": room["status"],
            "player_count": len(players),
            "max_players": MAX_ROOM_PLAYERS,
            "players": [
                room_player_data(player)
                for player in players
            ],
            "created_at": room["created_at"].isoformat()
        }
    }


# ==========================================
# AUTHENTICATION
# ==========================================

async def get_current_user(request: Request):
    token = request.cookies.get(COOKIE_NAME)

    if not token:
        raise HTTPException(
            status_code=401,
            detail="Необходима авторизация."
        )

    token_hash = hash_session_token(token)

    async with db_pool.acquire() as connection:
        row = await connection.fetchrow(
            """
            SELECT
                p.id,
                p.public_id,
                p.username,
                p.registered_at,
                p.wins,
                p.total_points,
                p.rounds_played,
                p.games_played,
                p.win_percentage,
                p.longest_streak
            FROM user_sessions s
            JOIN profiles p ON p.id = s.user_id
            WHERE
                s.token_hash = $1
                AND s.revoked_at IS NULL
                AND s.expires_at > NOW()
            """,
            token_hash
        )

    if not row:
        raise HTTPException(
            status_code=401,
            detail="Сессия недействительна. Войдите снова."
        )

    return row


# ==========================================
# REGISTER
# ==========================================

@app.post("/api/auth/register")
async def register(
    data: RegisterRequest,
    request: Request,
    response: Response
):
    username = normalize_username(data.username)
    validate_username(username)

    user_id = uuid.uuid4()
    password_hash = password_hasher.hash(data.password)

    try:
        async with db_pool.acquire() as connection:
            async with connection.transaction():

                await connection.execute(
                    """
                    INSERT INTO profiles (id, username)
                    VALUES ($1, $2)
                    """,
                    user_id,
                    username
                )

                await connection.execute(
                    """
                    INSERT INTO auth_credentials (
                        user_id,
                        password_hash
                    )
                    VALUES ($1, $2)
                    """,
                    user_id,
                    password_hash
                )

                token = await create_session(
                    connection,
                    user_id
                )

    except asyncpg.UniqueViolationError:
        raise HTTPException(
            status_code=409,
            detail="Это имя пользователя уже занято."
        )

    set_session_cookie(response, request, token)

    return {
        "success": True,
        "message": "Аккаунт успешно создан.",
        "user_id": str(user_id)
    }


# ==========================================
# LOGIN
# ==========================================

@app.post("/api/auth/login")
async def login(
    data: LoginRequest,
    request: Request,
    response: Response
):
    username = normalize_username(data.username)

    async with db_pool.acquire() as connection:
        row = await connection.fetchrow(
            """
            SELECT
                p.id,
                c.password_hash
            FROM profiles p
            JOIN auth_credentials c ON c.user_id = p.id
            WHERE LOWER(p.username) = LOWER($1)
            """,
            username
        )

        if not row:
            raise HTTPException(
                status_code=401,
                detail="Неверное имя пользователя или пароль."
            )

        try:
            password_hasher.verify(
                row["password_hash"],
                data.password
            )
        except (VerifyMismatchError, InvalidHashError):
            raise HTTPException(
                status_code=401,
                detail="Неверное имя пользователя или пароль."
            )

        user_id = row["id"]

        if password_hasher.check_needs_rehash(
            row["password_hash"]
        ):
            new_hash = password_hasher.hash(data.password)

            await connection.execute(
                """
                UPDATE auth_credentials
                SET password_hash = $1
                WHERE user_id = $2
                """,
                new_hash,
                user_id
            )

        token = await create_session(
            connection,
            user_id
        )

    set_session_cookie(response, request, token)

    return {
        "success": True,
        "message": "Вы успешно вошли."
    }


# ==========================================
# LOGOUT
# ==========================================

@app.post("/api/auth/logout")
async def logout(
    request: Request,
    response: Response
):
    token = request.cookies.get(COOKIE_NAME)

    if token:
        token_hash = hash_session_token(token)

        async with db_pool.acquire() as connection:
            await connection.execute(
                """
                UPDATE user_sessions
                SET revoked_at = NOW()
                WHERE
                    token_hash = $1
                    AND revoked_at IS NULL
                """,
                token_hash
            )

    delete_session_cookie(response)

    return {
        "success": True,
        "message": "Вы вышли из аккаунта."
    }


# ==========================================
# CURRENT USER
# ==========================================

@app.get("/api/auth/me")
async def current_user(request: Request):
    user = await get_current_user(request)

    return {
        "authenticated": True,
        "user": public_profile(user)
    }


# ==========================================
# PROFILE
# ==========================================

@app.get("/api/profile/me")
async def my_profile(request: Request):
    user = await get_current_user(request)
    return public_profile(user)


# ==========================================
# CREATE ROOM
# ==========================================

@app.post("/api/rooms")
async def create_room(
    data: CreateRoomRequest,
    request: Request
):
    user = await get_current_user(request)

    if data.target_score not in ALLOWED_SCORES:
        raise HTTPException(
            status_code=400,
            detail=(
                "Допустимое количество побед: "
                "3, 5, 7, 10, 15 или 20."
            )
        )

    async with db_pool.acquire() as connection:
        for _ in range(10):
            room_code = "".join(
                secrets.choice(ROOM_CODE_ALPHABET)
                for _ in range(6)
            )

            try:
                async with connection.transaction():
                    room = await connection.fetchrow(
                        """
                        INSERT INTO game_rooms (
                            code,
                            host_id,
                            target_score,
                            status,
                            game_state
                        )
                        VALUES (
                            $1, $2, $3, 'lobby', '{}'::jsonb
                        )
                        RETURNING
                            id,
                            code,
                            host_id,
                            target_score,
                            status,
                            created_at
                        """,
                        room_code,
                        user["id"],
                        data.target_score
                    )

                    await connection.execute(
                        """
                        INSERT INTO room_players (
                            room_id,
                            user_id,
                            connected,
                            score
                        )
                        VALUES ($1, $2, TRUE, 0)
                        """,
                        room["id"],
                        user["id"]
                    )

                room = dict(room)
                room["host_username"] = user["username"]

                return await build_room_response(
                    connection,
                    room
                )

            except asyncpg.UniqueViolationError:
                continue

    raise HTTPException(
        status_code=503,
        detail="Не удалось создать комнату. Попробуйте ещё раз."
    )


# ==========================================
# JOIN ROOM
# ==========================================

@app.post("/api/rooms/join")
async def join_room(
    data: JoinRoomRequest,
    request: Request
):
    user = await get_current_user(request)
    code = normalize_room_code(data.code)

    if not re.fullmatch(r"[A-Z0-9]{6}", code):
        raise HTTPException(
            status_code=400,
            detail="Неверный формат кода комнаты."
        )

    async with db_pool.acquire() as connection:
        async with connection.transaction():
            room = await connection.fetchrow(
                """
                SELECT *
                FROM game_rooms
                WHERE code = $1
                FOR UPDATE
                """,
                code
            )

            if not room:
                raise HTTPException(
                    status_code=404,
                    detail="Комната с таким кодом не найдена."
                )

            if room["status"] != "lobby":
                raise HTTPException(
                    status_code=400,
                    detail="Эта комната уже запущена или завершена."
                )

            existing = await connection.fetchrow(
                """
                SELECT id
                FROM room_players
                WHERE room_id = $1 AND user_id = $2
                """,
                room["id"],
                user["id"]
            )

            if existing:
                await connection.execute(
                    """
                    UPDATE room_players
                    SET connected = TRUE
                    WHERE room_id = $1 AND user_id = $2
                    """,
                    room["id"],
                    user["id"]
                )
            else:
                player_count = await connection.fetchval(
                    """
                    SELECT COUNT(*)
                    FROM room_players
                    WHERE room_id = $1 AND connected = TRUE
                    """,
                    room["id"]
                )

                if player_count >= MAX_ROOM_PLAYERS:
                    raise HTTPException(
                        status_code=400,
                        detail="Комната уже заполнена."
                    )

                await connection.execute(
                    """
                    INSERT INTO room_players (
                        room_id,
                        user_id,
                        connected,
                        score
                    )
                    VALUES ($1, $2, TRUE, 0)
                    """,
                    room["id"],
                    user["id"]
                )

        room = await get_room_by_code(connection, code)

        return await build_room_response(
            connection,
            room
        )


# ==========================================
# GET ROOM / REFRESH LOBBY
# ==========================================

@app.get("/api/rooms/{code}")
async def get_room(
    code: str,
    request: Request
):
    user = await get_current_user(request)
    code = normalize_room_code(code)

    async with db_pool.acquire() as connection:
        room = await get_room_by_code(connection, code)

        if not room:
            raise HTTPException(
                status_code=404,
                detail="Комната не найдена."
            )

        membership = await connection.fetchval(
            """
            SELECT EXISTS(
                SELECT 1
                FROM room_players
                WHERE room_id = $1 AND user_id = $2
            )
            """,
            room["id"],
            user["id"]
        )

        if not membership:
            raise HTTPException(
                status_code=403,
                detail="Вы не состоите в этой комнате."
            )

        await connection.execute(
            """
            UPDATE room_players
            SET connected = TRUE
            WHERE room_id = $1 AND user_id = $2
            """,
            room["id"],
            user["id"]
        )

        return await build_room_response(
            connection,
            room
        )


# ==========================================
# LEAVE ROOM
# ==========================================

@app.post("/api/rooms/{code}/leave")
async def leave_room(
    code: str,
    request: Request
):
    user = await get_current_user(request)
    code = normalize_room_code(code)

    async with db_pool.acquire() as connection:
        room = await get_room_by_code(connection, code)

        if not room:
            raise HTTPException(
                status_code=404,
                detail="Комната не найдена."
            )

        result = await connection.execute(
            """
            UPDATE room_players
            SET connected = FALSE
            WHERE room_id = $1 AND user_id = $2
            """,
            room["id"],
            user["id"]
        )

        if result == "UPDATE 0":
            raise HTTPException(
                status_code=403,
                detail="Вы не состоите в этой комнате."
            )

    return {
        "success": True,
        "message": "Вы вышли из комнаты."
    }


# ==========================================
# START ROOM
# ==========================================

@app.post("/api/rooms/{code}/start")
async def start_room(
    code: str,
    request: Request
):
    user = await get_current_user(request)
    code = normalize_room_code(code)

    async with db_pool.acquire() as connection:
        async with connection.transaction():
            room = await connection.fetchrow(
                """
                SELECT *
                FROM game_rooms
                WHERE code = $1
                FOR UPDATE
                """,
                code
            )

            if not room:
                raise HTTPException(
                    status_code=404,
                    detail="Комната не найдена."
                )

            if room["host_id"] != user["id"]:
                raise HTTPException(
                    status_code=403,
                    detail="Только создатель комнаты может начать игру."
                )

            if room["status"] != "lobby":
                raise HTTPException(
                    status_code=400,
                    detail="Эта комната уже запущена."
                )

            player_count = await connection.fetchval(
                """
                SELECT COUNT(*)
                FROM room_players
                WHERE room_id = $1 AND connected = TRUE
                """,
                room["id"]
            )

            if player_count < 2:
                raise HTTPException(
                    status_code=400,
                    detail="Для начала игры нужны минимум 2 игрока."
                )

            player_rows = await connection.fetch(
                """
                SELECT user_id
                FROM room_players
                WHERE room_id = $1 AND connected = TRUE
                ORDER BY joined_at ASC
                """,
                room["id"]
            )
            player_ids = [str(row["user_id"]) for row in player_rows]
            secrets.SystemRandom().shuffle(player_ids)
            initial_state = _new_round_state({}, player_ids)

            await connection.execute(
                """
                UPDATE game_rooms
                SET
                    status = 'playing',
                    game_state = $1::jsonb,
                    updated_at = NOW()
                WHERE id = $2
                """,
                json.dumps(initial_state),
                room["id"]
            )

    return {
        "success": True,
        "message": "Комната запущена.",
        "status": "playing"
    }




# ==========================================
# TIMEGUESS GAME ENGINE
# ==========================================

class TargetTimeRequest(BaseModel):
    random: bool = False
    value: float | None = None


class AttemptRequest(BaseModel):
    value: float = Field(ge=0, le=60)


def _json_state(raw):
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return {}
    return {}


def _new_round_state(state, player_ids):
    round_number = int(state.get("round", 0)) + 1
    setter_index = (round_number - 1) % len(player_ids)
    setter_id = player_ids[setter_index]
    attempt_order = player_ids[setter_index:] + player_ids[:setter_index]
    return {
        "phase": "choosing_target",
        "round": round_number,
        "setter_id": setter_id,
        "attempt_order": attempt_order,
        "turn_index": 0,
        "target_time": None,
        "attempts": [],
        "winner_id": None,
        "last_results": []
    }


async def _load_member_room(connection, code, user_id, lock=False):
    query = """
        SELECT *
        FROM game_rooms
        WHERE code = $1
    """ + (" FOR UPDATE" if lock else "")
    room = await connection.fetchrow(query, code)
    if not room:
        raise HTTPException(status_code=404, detail="Комната не найдена.")
    member = await connection.fetchval(
        "SELECT EXISTS(SELECT 1 FROM room_players WHERE room_id=$1 AND user_id=$2)",
        room["id"], user_id
    )
    if not member:
        raise HTTPException(status_code=403, detail="Ты не состоишь в этой комнате.")
    return room


@app.get("/api/rooms/{code}/game")
async def get_game_state(code: str, request: Request):
    user = await get_current_user(request)
    code = normalize_room_code(code)
    async with db_pool.acquire() as connection:
        room = await _load_member_room(connection, code, user["id"])
        if room["status"] == "lobby":
            raise HTTPException(status_code=400, detail="Игра ещё не началась.")
        players = await get_room_players(connection, room["id"])
        state = _json_state(room["game_state"])
        public_state = dict(state)
        if public_state.get("phase") not in {"round_finished", "finished"}:
            public_state["target_time"] = None
            public_state["attempts"] = []
            public_state["last_results"] = []
        return {
            "success": True,
            "game": public_state,
            "players": [room_player_data(p) for p in players],
            "host_id": str(room["host_id"]),
            "target_score": room["target_score"],
            "my_id": str(user["id"])
        }


@app.post("/api/rooms/{code}/target")
async def set_game_target(code: str, data: TargetTimeRequest, request: Request):
    user = await get_current_user(request)
    code = normalize_room_code(code)
    async with db_pool.acquire() as connection:
        async with connection.transaction():
            room = await _load_member_room(connection, code, user["id"], lock=True)
            state = _json_state(room["game_state"])
            if room["status"] != "playing" or state.get("phase") != "choosing_target":
                raise HTTPException(status_code=400, detail="Сейчас нельзя выбрать время.")
            if str(user["id"]) != state.get("setter_id"):
                raise HTTPException(status_code=403, detail="В этом раунде время загадывает другой игрок.")
            if data.random:
                target = secrets.randbelow(1000) / 100 + 0.01
            else:
                if data.value is None:
                    raise HTTPException(status_code=400, detail="Укажи время или выбери случайное.")
                target = round(float(data.value), 2)
            if not 0.01 <= target <= 10:
                raise HTTPException(status_code=400, detail="Время должно быть от 0.01 до 10.00 секунд.")
            state["target_time"] = target
            state["phase"] = "playing_turn"
            state["turn_index"] = 0
            await connection.execute(
                "UPDATE game_rooms SET game_state=$1::jsonb, updated_at=NOW() WHERE id=$2",
                json.dumps(state), room["id"]
            )
    return {"success": True}


@app.post("/api/rooms/{code}/attempt")
async def submit_game_attempt(code: str, data: AttemptRequest, request: Request):
    user = await get_current_user(request)
    code = normalize_room_code(code)
    async with db_pool.acquire() as connection:
        async with connection.transaction():
            room = await _load_member_room(connection, code, user["id"], lock=True)
            state = _json_state(room["game_state"])
            if room["status"] != "playing" or state.get("phase") != "playing_turn":
                raise HTTPException(status_code=400, detail="Сейчас нельзя отправить попытку.")
            order = state.get("attempt_order", [])
            index = int(state.get("turn_index", 0))
            if index >= len(order) or order[index] != str(user["id"]):
                raise HTTPException(status_code=403, detail="Сейчас ход другого игрока.")
            result = round(float(data.value), 2)
            target = float(state["target_time"])
            state.setdefault("attempts", []).append({
                "player_id": str(user["id"]),
                "time": result,
                "difference": round(abs(result - target), 2)
            })
            state["turn_index"] = index + 1
            if state["turn_index"] >= len(order):
                winner_attempt = min(state["attempts"], key=lambda item: item["difference"])
                winner_id = winner_attempt["player_id"]
                state["winner_id"] = winner_id
                state["phase"] = "round_finished"
                state["last_results"] = state["attempts"]
                await connection.execute(
                    "UPDATE room_players SET score=score+1 WHERE room_id=$1 AND user_id=$2",
                    room["id"], uuid.UUID(winner_id)
                )
                participant_ids = state.get("attempt_order", [])
                if participant_ids:
                    await connection.execute(
                        """
                        UPDATE profiles
                        SET rounds_played = rounds_played + 1
                        WHERE id = ANY($1::uuid[])
                        """,
                        [uuid.UUID(pid) for pid in participant_ids]
                    )
                await connection.execute(
                    "UPDATE profiles SET total_points=total_points+1 WHERE id=$1",
                    uuid.UUID(winner_id)
                )
                winner_score = await connection.fetchval(
                    "SELECT score FROM room_players WHERE room_id=$1 AND user_id=$2",
                    room["id"], uuid.UUID(winner_id)
                )
                if winner_score >= room["target_score"]:
                    state["phase"] = "finished"
                    participant_ids = state.get("attempt_order", [])
                    if participant_ids:
                        await connection.execute(
                            """
                            UPDATE profiles
                            SET
                                games_played = games_played + 1,
                                wins = wins + CASE WHEN id = $1 THEN 1 ELSE 0 END
                            WHERE id = ANY($2::uuid[])
                            """,
                            uuid.UUID(winner_id),
                            [uuid.UUID(pid) for pid in participant_ids]
                        )
            await connection.execute(
                "UPDATE game_rooms SET game_state=$1::jsonb, updated_at=NOW() WHERE id=$2",
                json.dumps(state), room["id"]
            )
    return {"success": True}


@app.post("/api/rooms/{code}/next-round")
async def next_game_round(code: str, request: Request):
    user = await get_current_user(request)
    code = normalize_room_code(code)
    async with db_pool.acquire() as connection:
        async with connection.transaction():
            room = await _load_member_room(connection, code, user["id"], lock=True)
            if room["host_id"] != user["id"]:
                raise HTTPException(status_code=403, detail="Следующий раунд может начать создатель комнаты.")
            state = _json_state(room["game_state"])
            if state.get("phase") != "round_finished":
                raise HTTPException(status_code=400, detail="Раунд ещё не завершён или игра уже окончена.")
            rows = await connection.fetch(
                "SELECT user_id FROM room_players WHERE room_id=$1 ORDER BY joined_at ASC",
                room["id"]
            )
            player_ids = [str(row["user_id"]) for row in rows]
            state = _new_round_state(state, player_ids)
            await connection.execute(
                "UPDATE game_rooms SET game_state=$1::jsonb, updated_at=NOW() WHERE id=$2",
                json.dumps(state), room["id"]
            )
    return {"success": True}


# ==========================================
# HEALTH CHECK
# ==========================================

@app.get("/api/health")
async def health():
    try:
        async with db_pool.acquire() as connection:
            await connection.fetchval("SELECT 1")

        return {
            "status": "ok",
            "database": "connected",
            "application": "TIMEGUESS"
        }

    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Database connection failed."
        )


# ==========================================
# WEBSITE
# ==========================================

@app.get("/")
async def home():
    return FileResponse(BASE_DIR / "index.html")


@app.get("/style.css")
async def css():
    return FileResponse(BASE_DIR / "style.css")


@app.get("/script.js")
async def javascript():
    return FileResponse(BASE_DIR / "script.js")
