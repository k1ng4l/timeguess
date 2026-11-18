import os
import re
import uuid
import secrets
import hashlib
import json
import base64
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

BASE_DIR = Path(__file__).resolve().parent

load_dotenv(BASE_DIR / ".env")

DATABASE_URL = os.getenv("DATABASE_URL")

SESSION_DURATION_DAYS = 30
COOKIE_NAME = "timeguess_session"

SPECIAL_CYRILLIC_USER_ID = uuid.UUID(
    "3baebcf5-4744-4f7b-9298-97b8a0b56083"
)

MAX_AVATAR_BYTES = 350_000

MAX_ROOM_PLAYERS = 7
ALLOWED_SCORES = {3, 5, 7, 10, 15, 20}
ROOM_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

password_hasher = PasswordHasher()
db_pool = None

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
    version="2.2.0",
    lifespan=lifespan
)

class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=20)
    password: str = Field(min_length=8, max_length=128)

class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=20)
    password: str = Field(min_length=8, max_length=128)

class ChangeUsernameRequest(BaseModel):
    username: str = Field(min_length=3, max_length=20)
    current_password: str = Field(min_length=8, max_length=128)

class AvatarRequest(BaseModel):
    avatar_data: str | None = Field(..., max_length=500_000)

class PrivacyRequest(BaseModel):
    show_wins: bool
    show_games_played: bool
    show_rounds_played: bool
    show_win_percentage: bool
    show_total_points: bool
    show_longest_streak: bool
    show_registered_at: bool
    show_match_history: bool

class CreateRoomRequest(BaseModel):
    target_score: int = Field(default=7, ge=1, le=20)

class JoinRoomRequest(BaseModel):
    code: str = Field(min_length=6, max_length=6)

def normalize_username(username: str) -> str:
    return username.strip()

async def is_reserved_username(connection, username: str) -> bool:
    return await connection.fetchval(
        """
        SELECT EXISTS (
            SELECT 1
            FROM public.reserved_usernames
            WHERE LOWER(username) = LOWER($1)
        )
        """,
        username
    )

def validate_username(
    username: str,
    user_id: uuid.UUID | None = None
):
    if user_id == SPECIAL_CYRILLIC_USER_ID:
        pattern = r"^[A-Za-zА-Яа-яЁё0-9._]{3,20}$"
        error_message = (
            "Username должен содержать от 3 до 20 символов: "
            "латинские или кириллические буквы, цифры, точку "
            "или нижнее подчёркивание."
        )
    else:
        pattern = r"^[A-Za-z0-9._]{3,20}$"
        error_message = (
            "Username должен содержать от 3 до 20 символов: "
            "латинские буквы, цифры, точку или нижнее подчёркивание."
        )

    if not re.fullmatch(pattern, username):
        raise HTTPException(
            status_code=400,
            detail=error_message
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

def public_profile(row, is_owner=True):
    visibility = {
        "wins": row["show_wins"],
        "games_played": row["show_games_played"],
        "rounds_played": row["show_rounds_played"],
        "win_percentage": row["show_win_percentage"],
        "total_points": row["show_total_points"],
        "longest_streak": row["show_longest_streak"],
        "registered_at": row["show_registered_at"],
        "match_history": row["show_match_history"]
    }

    values = {
        "wins": row["wins"],
        "games_played": row["games_played"],
        "rounds_played": row["rounds_played"],
        "win_percentage": (
            round(
                (row["wins"] / row["games_played"]) * 100,
                1
            )
            if row["games_played"] else 0.0
        ),
        "total_points": row["total_points"],
        "longest_streak": row["longest_streak"],
        "registered_at": row["registered_at"].isoformat()
    }

    if not is_owner:
        for key, visible in visibility.items():
            if key in values and not visible:
                values[key] = None

    return {
        "id": str(row["id"]),
        "username": row["username"],
        "avatar_data": row["avatar_data"],
        **values,
        "visibility": visibility,
        "username_changed_at": (
            row["username_changed_at"].isoformat()
            if row["username_changed_at"] else None
        )
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
                p.username,
                p.avatar_data,
                p.registered_at,
                p.wins,
                p.total_points,
                p.rounds_played,
                p.games_played,
                p.win_percentage,
                p.longest_streak,
                p.username_changed_at,
                p.show_wins,
                p.show_games_played,
                p.show_rounds_played,
                p.show_win_percentage,
                p.show_total_points,
                p.show_longest_streak,
                p.show_registered_at,
                p.show_match_history
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

                if await is_reserved_username(connection, username):
                    raise HTTPException(
                        status_code=409,
                        detail="Это имя пользователя зарезервировано."
                    )

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

@app.get("/api/auth/me")
async def current_user(request: Request):
    user = await get_current_user(request)

    return {
        "authenticated": True,
        "user": public_profile(user)
    }

@app.get("/api/profile/me")
async def my_profile(request: Request):
    user = await get_current_user(request)
    return public_profile(user)

@app.get("/api/profile/{profile_identifier}")
async def public_profile_by_identifier(
    profile_identifier: str,
    request: Request
):
    await get_current_user(request)

    identifier = profile_identifier.strip()

    if not identifier or len(identifier) > 40:
        raise HTTPException(
            status_code=404,
            detail="Профиль не найден."
        )

    async with db_pool.acquire() as connection:
        profile = await connection.fetchrow(
            """
            SELECT *
            FROM profiles
            WHERE LOWER(username) = LOWER($1)
            LIMIT 1
            """,
            identifier
        )

    if not profile:
        raise HTTPException(
            status_code=404,
            detail="Профиль не найден."
        )

    return public_profile(profile, is_owner=False)

@app.patch("/api/profile/username")
async def change_username(
    data: ChangeUsernameRequest,
    request: Request
):
    user = await get_current_user(request)

    new_username = normalize_username(data.username)
    user_id = user["id"]

    validate_username(new_username, user_id)

    async with db_pool.acquire() as connection:
        async with connection.transaction():

            profile = await connection.fetchrow(
                """
                SELECT username, username_changed_at
                FROM profiles
                WHERE id = $1
                FOR UPDATE
                """,
                user_id
            )

            if new_username == profile["username"]:
                raise HTTPException(
                    status_code=400,
                    detail="Это уже ваше имя пользователя."
                )

            if await is_reserved_username(connection, new_username):
                raise HTTPException(
                    status_code=409,
                    detail="Это имя пользователя зарезервировано."
                )

            changed_at = profile["username_changed_at"]

            if changed_at:
                available_at = changed_at + timedelta(days=14)
                now = datetime.now(timezone.utc)

                if now < available_at:
                    remaining = available_at - now
                    total_seconds = int(remaining.total_seconds())

                    days = total_seconds // 86400
                    hours = (total_seconds % 86400) // 3600

                    raise HTTPException(
                        status_code=429,
                        detail=(
                            "Username можно менять раз в 14 дней. "
                            f"Попробуй снова через {days} дн. и {hours} ч."
                        )
                    )

            credential = await connection.fetchrow(
                """
                SELECT password_hash
                FROM auth_credentials
                WHERE user_id = $1
                """,
                user_id
            )

            if not credential:
                raise HTTPException(
                    status_code=401,
                    detail="Не удалось подтвердить аккаунт."
                )

            try:
                password_hasher.verify(
                    credential["password_hash"],
                    data.current_password
                )
            except (VerifyMismatchError, InvalidHashError):
                raise HTTPException(
                    status_code=401,
                    detail="Неверный текущий пароль."
                )

            try:
                await connection.execute(
                    """
                    UPDATE profiles
                    SET username = $1, username_changed_at = NOW()
                    WHERE id = $2
                    """,
                    new_username,
                    user_id
                )
            except asyncpg.UniqueViolationError:
                raise HTTPException(
                    status_code=409,
                    detail="Это имя пользователя уже занято."
                )

            updated = await connection.fetchrow(
                """
                SELECT *
                FROM profiles
                WHERE id = $1
                """,
                user_id
            )

    return {
        "success": True,
        "message": (
            "Имя пользователя обновлено. "
            "Следующая смена будет доступна через 14 дней."
        ),
        "profile": public_profile(updated)
    }

@app.patch("/api/profile/privacy")
async def update_profile_privacy(
    data: PrivacyRequest,
    request: Request
):
    user = await get_current_user(request)

    async with db_pool.acquire() as connection:
        updated = await connection.fetchrow(
            """
            UPDATE profiles SET
                show_wins = $1,
                show_games_played = $2,
                show_rounds_played = $3,
                show_win_percentage = $4,
                show_total_points = $5,
                show_longest_streak = $6,
                show_registered_at = $7,
                show_match_history = $8
            WHERE id = $9
            RETURNING *
            """,
            data.show_wins,
            data.show_games_played,
            data.show_rounds_played,
            data.show_win_percentage,
            data.show_total_points,
            data.show_longest_streak,
            data.show_registered_at,
            data.show_match_history,
            user["id"]
        )

    return {
        "success": True,
        "profile": public_profile(updated)
    }

@app.get("/api/profile/{profile_identifier}/history")
async def profile_match_history(
    profile_identifier: str,
    request: Request
):
    viewer = await get_current_user(request)
    identifier = profile_identifier.strip()

    async with db_pool.acquire() as connection:
        profile = await connection.fetchrow(
            """
            SELECT id, username, show_match_history
            FROM profiles
            WHERE LOWER(username) = LOWER($1)
            LIMIT 1
            """,
            identifier
        )

        if not profile:
            raise HTTPException(
                status_code=404,
                detail="Профиль не найден."
            )

        if (
            profile["id"] != viewer["id"]
            and not profile["show_match_history"]
        ):
            return {
                "success": True,
                "hidden": True,
                "matches": []
            }

        games = await connection.fetch(
            """
            SELECT
                g.id,
                g.started_at,
                g.finished_at,
                g.winner_id,
                gp.final_score
            FROM game_participants gp
            JOIN games g ON g.id = gp.game_id
            WHERE
                gp.user_id = $1
                AND g.finished_at IS NOT NULL
            ORDER BY g.finished_at DESC
            LIMIT 10
            """,
            profile["id"]
        )

        matches = []

        for game in games:
            participants = await connection.fetch(
                """
                SELECT
                    p.username,
                    gp.final_score,
                    gp.user_id
                FROM game_participants gp
                JOIN profiles p ON p.id = gp.user_id
                WHERE gp.game_id = $1
                ORDER BY gp.final_score DESC
                """,
                game["id"]
            )

            matches.append({
                "id": str(game["id"]),
                "started_at": game["started_at"].isoformat(),
                "finished_at": game["finished_at"].isoformat(),
                "won": game["winner_id"] == profile["id"],
                "final_score": game["final_score"] or 0,
                "players": [
                    {
                        "username": row["username"],
                        "score": row["final_score"] or 0,
                        "is_self": row["user_id"] == profile["id"]
                    }
                    for row in participants
                ]
            })

    return {
        "success": True,
        "hidden": False,
        "matches": matches
    }

@app.put("/api/profile/avatar")
async def update_avatar(
    data: AvatarRequest,
    request: Request
):
    user = await get_current_user(request)
    avatar_data = data.avatar_data

    if avatar_data is not None:
        prefix = "data:image/jpeg;base64,"

        if not avatar_data.startswith(prefix):
            raise HTTPException(
                status_code=400,
                detail="Аватарка должна быть изображением JPEG."
            )

        encoded = avatar_data[len(prefix):]

        try:
            image_bytes = base64.b64decode(
                encoded,
                validate=True
            )
        except (ValueError, base64.binascii.Error):
            raise HTTPException(
                status_code=400,
                detail="Не удалось прочитать изображение."
            )

        if not image_bytes or len(image_bytes) > MAX_AVATAR_BYTES:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Размер аватарки после обработки "
                    "не должен превышать 350 КБ."
                )
            )

        if not (
            image_bytes.startswith(b"\xff\xd8\xff")
            and image_bytes.endswith(b"\xff\xd9")
        ):
            raise HTTPException(
                status_code=400,
                detail="Файл не является корректным JPEG-изображением."
            )

    async with db_pool.acquire() as connection:
        await connection.execute(
            """
            UPDATE profiles
            SET avatar_data = $1
            WHERE id = $2
            """,
            avatar_data,
            user["id"]
        )

        updated = await connection.fetchrow(
            """
            SELECT *
            FROM profiles
            WHERE id = $1
            """,
            user["id"]
        )

    return {
        "success": True,
        "message": (
            "Аватарка удалена."
            if avatar_data is None
            else "Аватарка обновлена."
        ),
        "profile": public_profile(updated)
    }

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

            player_ids = [
                str(row["user_id"])
                for row in player_rows
            ]

            secrets.SystemRandom().shuffle(player_ids)

            initial_state = _new_round_state({}, player_ids)

            game_id = uuid.uuid4()
            initial_state["game_id"] = str(game_id)

            await connection.execute(
                """
                INSERT INTO games (
                    id,
                    room_id,
                    winner_id,
                    started_at,
                    finished_at
                )
                VALUES ($1, $2, NULL, NOW(), NULL)
                """,
                game_id,
                room["id"]
            )

            for player_id in player_ids:
                await connection.execute(
                    """
                    INSERT INTO game_participants (
                        id,
                        game_id,
                        user_id,
                        final_score
                    )
                    VALUES ($1, $2, $3, 0)
                    """,
                    uuid.uuid4(),
                    game_id,
                    uuid.UUID(player_id)
                )

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

    attempt_order = (
        player_ids[setter_index:]
        + player_ids[:setter_index]
    )

    return {
        "phase": "choosing_target",
        "game_id": state.get("game_id"),
        "round": round_number,
        "setter_id": setter_id,
        "attempt_order": attempt_order,
        "turn_index": 0,
        "target_time": None,
        "attempts": [],
        "winner_id": None,
        "last_results": []
    }

async def _load_member_room(
    connection,
    code,
    user_id,
    lock=False
):
    query = """
        SELECT *
        FROM game_rooms
        WHERE code = $1
    """ + (" FOR UPDATE" if lock else "")

    room = await connection.fetchrow(query, code)

    if not room:
        raise HTTPException(
            status_code=404,
            detail="Комната не найдена."
        )

    member = await connection.fetchval(
        """
        SELECT EXISTS(
            SELECT 1
            FROM room_players
            WHERE room_id = $1 AND user_id = $2
        )
        """,
        room["id"],
        user_id
    )

    if not member:
        raise HTTPException(
            status_code=403,
            detail="Ты не состоишь в этой комнате."
        )

    return room

@app.get("/api/rooms/{code}/game")
async def get_game_state(
    code: str,
    request: Request
):
    user = await get_current_user(request)
    code = normalize_room_code(code)

    async with db_pool.acquire() as connection:
        room = await _load_member_room(
            connection,
            code,
            user["id"]
        )

        if room["status"] == "lobby":
            raise HTTPException(
                status_code=400,
                detail="Игра ещё не началась."
            )

        players = await get_room_players(
            connection,
            room["id"]
        )

        state = _json_state(room["game_state"])
        public_state = dict(state)

        if public_state.get("phase") == "choosing_target":
            public_state["target_time"] = None

        if public_state.get("phase") not in {
            "round_finished",
            "finished"
        }:
            public_state["attempts"] = []
            public_state["last_results"] = []

        return {
            "success": True,
            "game": public_state,
            "players": [
                room_player_data(player)
                for player in players
            ],
            "host_id": str(room["host_id"]),
            "target_score": room["target_score"],
            "my_id": str(user["id"])
        }

@app.post("/api/rooms/{code}/target")
async def set_game_target(
    code: str,
    data: TargetTimeRequest,
    request: Request
):
    user = await get_current_user(request)
    code = normalize_room_code(code)

    async with db_pool.acquire() as connection:
        async with connection.transaction():
            room = await _load_member_room(
                connection,
                code,
                user["id"],
                lock=True
            )

            state = _json_state(room["game_state"])

            if (
                room["status"] != "playing"
                or state.get("phase") != "choosing_target"
            ):
                raise HTTPException(
                    status_code=400,
                    detail="Сейчас нельзя выбрать время."
                )

            if str(user["id"]) != state.get("setter_id"):
                raise HTTPException(
                    status_code=403,
                    detail="В этом раунде время загадывает другой игрок."
                )

            if data.random:
                target = secrets.randbelow(1000) / 100 + 0.01
            else:
                if data.value is None:
                    raise HTTPException(
                        status_code=400,
                        detail="Укажи время или выбери случайное."
                    )

                target = round(float(data.value), 2)

            if not 0.01 <= target <= 10:
                raise HTTPException(
                    status_code=400,
                    detail="Время должно быть от 0.01 до 10.00 секунд."
                )

            state["target_time"] = target
            state["phase"] = "playing_turn"
            state["turn_index"] = 0

            round_id = uuid.uuid4()
            state["round_id"] = str(round_id)

            await connection.execute(
                """
                INSERT INTO game_rounds (
                    id,
                    game_id,
                    round_number,
                    target_time,
                    winner_id
                )
                VALUES ($1, $2, $3, $4, NULL)
                """,
                round_id,
                uuid.UUID(state["game_id"]),
                int(state["round"]),
                target
            )

            await connection.execute(
                """
                UPDATE game_rooms
                SET game_state = $1::jsonb, updated_at = NOW()
                WHERE id = $2
                """,
                json.dumps(state),
                room["id"]
            )

    return {"success": True}

@app.post("/api/rooms/{code}/attempt")
async def submit_game_attempt(
    code: str,
    data: AttemptRequest,
    request: Request
):
    user = await get_current_user(request)
    code = normalize_room_code(code)

    async with db_pool.acquire() as connection:
        async with connection.transaction():
            room = await _load_member_room(
                connection,
                code,
                user["id"],
                lock=True
            )

            state = _json_state(room["game_state"])

            if (
                room["status"] != "playing"
                or state.get("phase") != "playing_turn"
            ):
                raise HTTPException(
                    status_code=400,
                    detail="Сейчас нельзя отправить попытку."
                )

            order = state.get("attempt_order", [])
            index = int(state.get("turn_index", 0))

            if index >= len(order) or order[index] != str(user["id"]):
                raise HTTPException(
                    status_code=403,
                    detail="Сейчас ход другого игрока."
                )

            result = round(float(data.value), 2)
            target = float(state["target_time"])
            difference = round(abs(result - target), 2)

            state.setdefault("attempts", []).append({
                "player_id": str(user["id"]),
                "time": result,
                "difference": difference
            })

            if state.get("round_id"):
                await connection.execute(
                    """
                    INSERT INTO game_attempts (
                        id,
                        round_id,
                        user_id,
                        attempted_time,
                        difference,
                        created_at
                    )
                    VALUES ($1, $2, $3, $4, $5, NOW())
                    """,
                    uuid.uuid4(),
                    uuid.UUID(state["round_id"]),
                    user["id"],
                    result,
                    difference
                )

            state["turn_index"] = index + 1

            if state["turn_index"] >= len(order):
                best_difference = min(
                    item["difference"]
                    for item in state["attempts"]
                )

                best_attempts = [
                    item
                    for item in state["attempts"]
                    if item["difference"] == best_difference
                ]

                state["phase"] = "round_finished"
                state["last_results"] = state["attempts"]

                participant_ids = state.get("attempt_order", [])

                if participant_ids:
                    await connection.execute(
                        """
                        UPDATE profiles
                        SET rounds_played = rounds_played + 1
                        WHERE id = ANY($1::uuid[])
                        """,
                        [
                            uuid.UUID(pid)
                            for pid in participant_ids
                        ]
                    )

                if len(best_attempts) == 1:
                    winner_id = best_attempts[0]["player_id"]
                    state["winner_id"] = winner_id

                    await connection.execute(
                        """
                        UPDATE room_players
                        SET score = score + 1
                        WHERE room_id = $1 AND user_id = $2
                        """,
                        room["id"],
                        uuid.UUID(winner_id)
                    )

                    await connection.execute(
                        """
                        UPDATE profiles
                        SET total_points = total_points + 1
                        WHERE id = $1
                        """,
                        uuid.UUID(winner_id)
                    )

                    winner_score = await connection.fetchval(
                        """
                        SELECT score
                        FROM room_players
                        WHERE room_id = $1 AND user_id = $2
                        """,
                        room["id"],
                        uuid.UUID(winner_id)
                    )

                    if winner_score >= room["target_score"]:
                        state["phase"] = "finished"

                        if participant_ids:
                            await connection.execute(
                                """
                                UPDATE profiles
                                SET
                                    games_played = games_played + 1,
                                    wins = wins + CASE
                                        WHEN id = $1 THEN 1
                                        ELSE 0
                                    END
                                WHERE id = ANY($2::uuid[])
                                """,
                                uuid.UUID(winner_id),
                                [
                                    uuid.UUID(pid)
                                    for pid in participant_ids
                                ]
                            )
                else:
                    state["winner_id"] = None

                if state.get("round_id"):
                    await connection.execute(
                        """
                        UPDATE game_rounds
                        SET winner_id = $1
                        WHERE id = $2
                        """,
                        (
                            uuid.UUID(state["winner_id"])
                            if state.get("winner_id")
                            else None
                        ),
                        uuid.UUID(state["round_id"])
                    )

                if state["phase"] == "finished":
                    game_id = uuid.UUID(state["game_id"])

                    await connection.execute(
                        """
                        UPDATE games
                        SET winner_id = $1, finished_at = NOW()
                        WHERE id = $2
                        """,
                        uuid.UUID(state["winner_id"]),
                        game_id
                    )

                    await connection.execute(
                        """
                        UPDATE game_participants gp
                        SET final_score = rp.score
                        FROM room_players rp
                        WHERE gp.game_id = $1
                          AND gp.user_id = rp.user_id
                          AND rp.room_id = $2
                        """,
                        game_id,
                        room["id"]
                    )

            await connection.execute(
                """
                UPDATE game_rooms
                SET game_state = $1::jsonb, updated_at = NOW()
                WHERE id = $2
                """,
                json.dumps(state),
                room["id"]
            )

    return {"success": True}

@app.post("/api/rooms/{code}/next-round")
async def next_game_round(
    code: str,
    request: Request
):
    user = await get_current_user(request)
    code = normalize_room_code(code)

    async with db_pool.acquire() as connection:
        async with connection.transaction():
            room = await _load_member_room(
                connection,
                code,
                user["id"],
                lock=True
            )

            if room["host_id"] != user["id"]:
                raise HTTPException(
                    status_code=403,
                    detail="Следующий раунд может начать создатель комнаты."
                )

            state = _json_state(room["game_state"])

            if state.get("phase") != "round_finished":
                raise HTTPException(
                    status_code=400,
                    detail="Раунд ещё не завершён или игра уже окончена."
                )

            rows = await connection.fetch(
                """
                SELECT user_id
                FROM room_players
                WHERE room_id = $1
                ORDER BY joined_at ASC
                """,
                room["id"]
            )

            player_ids = [
                str(row["user_id"])
                for row in rows
            ]

            state = _new_round_state(state, player_ids)

            await connection.execute(
                """
                UPDATE game_rooms
                SET game_state = $1::jsonb, updated_at = NOW()
                WHERE id = $2
                """,
                json.dumps(state),
                room["id"]
            )

    return {"success": True}

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

@app.get("/")
async def home():
    return FileResponse(BASE_DIR / "index.html")

@app.get("/style.css")
async def css():
    return FileResponse(BASE_DIR / "style.css")

@app.get("/script.js")
async def javascript():
    return FileResponse(BASE_DIR / "script.js")