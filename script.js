
"use strict";

// ============================================
// TIMEGUESS V2 — COMPLETE FRONTEND SCRIPT
// ============================================

document.addEventListener("DOMContentLoaded", () => {

    // ----------------------------------------
    // HELPERS
    // ----------------------------------------

    const $ = (id) => document.getElementById(id);

    const show = (element) => {
        if (element) element.classList.remove("hidden");
    };

    const hide = (element) => {
        if (element) element.classList.add("hidden");
    };

    const setText = (element, value) => {
        if (element) element.textContent = value ?? "";
    };

    const setMessage = (element, message, type = "") => {
        if (!element) return;
        element.textContent = message;
        element.className = type ? `auth-message ${type}` : "auth-message";
    };

    async function apiRequest(url, options = {}) {
        const config = {
            method: options.method || "GET",
            credentials: "same-origin",
            headers: {
                ...(options.body ? { "Content-Type": "application/json" } : {}),
                ...options.headers
            }
        };

        if (options.body) {
            config.body = options.body;
        }

        const response = await fetch(url, config);

        let data = {};

        try {
            data = await response.json();
        } catch {
            data = {};
        }

        if (!response.ok) {
            throw new Error(
                data.detail ||
                data.message ||
                `Ошибка сервера: ${response.status}`
            );
        }

        return data;
    }

    function jsonBody(data) {
        return JSON.stringify(data);
    }

    function setLoading(button, loading, loadingText, normalText) {
        if (!button) return;

        button.disabled = loading;
        button.textContent = loading ? loadingText : normalText;
    }


    // ========================================
    // AUTHENTICATION
    // ========================================

    const authScreen = $("authScreen");
    const dashboardScreen = $("dashboardScreen");

    const loginForm = $("loginForm");
    const registerForm = $("registerForm");

    const loginTab = $("loginTab");
    const registerTab = $("registerTab");

    const loginMessage = $("loginMessage");
    const registerMessage = $("registerMessage");

    const logoutButton = $("logoutButton");

    let currentUser = null;
    let currentUserId = null;

    function clearAuthMessages() {
        if (loginMessage) {
            loginMessage.textContent = "";
            loginMessage.className = "auth-message";
        }

        if (registerMessage) {
            registerMessage.textContent = "";
            registerMessage.className = "auth-message";
        }
    }

    function showLogin() {
        clearAuthMessages();

        show(loginForm);
        hide(registerForm);

        loginTab?.classList.add("active");
        registerTab?.classList.remove("active");
    }

    function showRegister() {
        clearAuthMessages();

        show(registerForm);
        hide(loginForm);

        registerTab?.classList.add("active");
        loginTab?.classList.remove("active");
    }

    loginTab?.addEventListener("click", showLogin);
    registerTab?.addEventListener("click", showRegister);


    // ----------------------------------------
    // PROFILE
    // ----------------------------------------

    function renderProfile(user) {
        currentUser = user;
        currentUserId = user.id || null;

        setText($("welcomeUsername"), user.username);
        setText($("profileUsername"), user.username);
        setText($("profilePublicId"), user.public_id);

        const avatar = $("profileAvatar");

        if (avatar) {
            avatar.textContent = (user.username || "?")
                .charAt(0)
                .toUpperCase();
        }

        setText($("statWins"), user.wins ?? 0);
        setText($("statGames"), user.games_played ?? 0);
        setText($("statRounds"), user.rounds_played ?? 0);

        const winRate = Number(user.win_percentage ?? 0);

        setText(
            $("statWinRate"),
            `${winRate.toFixed(1)}%`
        );
    }

    async function loadProfile() {
        const user = await apiRequest("/api/profile/me");

        renderProfile(user);

        hide(authScreen);
        show(dashboardScreen);

        await restoreRoom();
    }

    const profileSearchForm = $("profileSearchForm");
    const profileSearchInput = $("profileSearchInput");
    const profileSearchMessage = $("profileSearchMessage");
    const publicProfileResult = $("publicProfileResult");

    profileSearchForm?.addEventListener("submit", async (event) => {
        event.preventDefault();
        const identifier = profileSearchInput?.value.trim();
        if (!identifier) return;

        setText(profileSearchMessage, "Ищем профиль...");
        hide(publicProfileResult);

        try {
            const profile = await apiRequest(
                `/api/profile/${encodeURIComponent(identifier)}`
            );
            setText(profileSearchMessage, "");
            setText($("publicProfileUsername"), profile.username);
            setText($("publicProfileId"), profile.public_id);
            setText($("publicProfileAvatar"), (profile.username || "?").charAt(0).toUpperCase());
            setText($("publicStatWins"), profile.wins ?? 0);
            setText($("publicStatGames"), profile.games_played ?? 0);
            setText($("publicStatRounds"), profile.rounds_played ?? 0);
            setText($("publicStatWinRate"), `${Number(profile.win_percentage ?? 0).toFixed(1)}%`);
            show(publicProfileResult);
        } catch (error) {
            setText(profileSearchMessage, error.message || "Профиль не найден.");
        }
    });


    async function initializeApp() {
        try {
            await loadProfile();
        } catch {
            currentUser = null;
            currentUserId = null;

            hide(dashboardScreen);
            show(authScreen);

            showLogin();
        }
    }


    // ----------------------------------------
    // REGISTER
    // ----------------------------------------

    async function handleRegister(event) {
        event?.preventDefault();

        if (!registerForm) return;

        clearAuthMessages();

        const username = $("registerUsername")?.value.trim();
        const password = $("registerPassword")?.value;
        const passwordConfirm = $("registerPasswordConfirm")?.value;

        if (!username || !password || !passwordConfirm) {
            setMessage(
                registerMessage,
                "Заполни все поля."
            );
            return;
        }

        if (password !== passwordConfirm) {
            setMessage(
                registerMessage,
                "Пароли не совпадают."
            );
            return;
        }

        if (password.length < 8) {
            setMessage(
                registerMessage,
                "Пароль должен содержать минимум 8 символов."
            );
            return;
        }

        const button = registerForm.querySelector(
            'button[type="submit"], button'
        );

        if (button?.disabled) return;

        const oldText = button?.textContent || "Зарегистрироваться";

        setLoading(
            button,
            true,
            "Создаём аккаунт...",
            oldText
        );

        try {
            await apiRequest("/api/auth/register", {
                method: "POST",
                body: jsonBody({
                    username,
                    password
                })
            });

            setMessage(
                registerMessage,
                "Аккаунт создан! Загружаем...",
                "success"
            );

            await loadProfile();

        } catch (error) {
            setMessage(
                registerMessage,
                error.message
            );

        } finally {
            if (button) {
                button.disabled = false;
                button.textContent = oldText;
            }
        }
    }

    if (registerForm) {
        registerForm.addEventListener("submit", handleRegister);

        const registerButton = registerForm.querySelector("button");

        if (registerButton && registerButton.type !== "submit") {
            registerButton.addEventListener("click", (event) => {
                event.preventDefault();
                handleRegister(event);
            });
        }
    }


    // ----------------------------------------
    // LOGIN
    // ----------------------------------------

    async function handleLogin(event) {
        event?.preventDefault();

        if (!loginForm) return;

        clearAuthMessages();

        const username = $("loginUsername")?.value.trim();
        const password = $("loginPassword")?.value;

        if (!username || !password) {
            setMessage(
                loginMessage,
                "Введи имя пользователя и пароль."
            );
            return;
        }

        const button = loginForm.querySelector(
            'button[type="submit"], button'
        );

        if (button?.disabled) return;

        const oldText = button?.textContent || "Войти";

        setLoading(
            button,
            true,
            "Входим...",
            oldText
        );

        try {
            await apiRequest("/api/auth/login", {
                method: "POST",
                body: jsonBody({
                    username,
                    password
                })
            });

            await loadProfile();

        } catch (error) {
            setMessage(
                loginMessage,
                error.message
            );

        } finally {
            if (button) {
                button.disabled = false;
                button.textContent = oldText;
            }
        }
    }

    if (loginForm) {
        loginForm.addEventListener("submit", handleLogin);

        const loginButton = loginForm.querySelector("button");

        if (loginButton && loginButton.type !== "submit") {
            loginButton.addEventListener("click", (event) => {
                event.preventDefault();
                handleLogin(event);
            });
        }
    }


    // ========================================
    // ROOMS
    // ========================================

    const createRoomButton = $("createRoomButton");
    const targetScoreSelect = $("targetScore");

    const joinRoomButton = $("joinRoomButton");
    const joinRoomCode = $("joinRoomCode");

    const roomMessage = $("roomMessage");
    const roomActions = $("roomActions");

    const lobbyPanel = $("lobbyPanel");
    const lobbyStatus = $("lobbyStatus");
    const lobbyRoomCode = $("lobbyRoomCode");
    const lobbyTargetScore = $("lobbyTargetScore");
    const lobbyPlayerCount = $("lobbyPlayerCount");
    const lobbyPlayers = $("lobbyPlayers");
    const lobbyHint = $("lobbyHint");

    const copyRoomCodeButton = $("copyRoomCode");
    const startRoomButton = $("startRoomButton");
    const leaveRoomButton = $("leaveRoomButton");

    // Support for the previous room HTML.
    const oldCreatedRoom = $("createdRoom");
    const oldRoomCode = $("roomCode");
    const oldRoomTargetScore = $("roomTargetScore");

    let activeRoomCode = null;
    let activeRoom = null;
    let lobbyTimer = null;
    let lobbyRequestBusy = false;
    let restoringRoom = false;

    const gamePanel = $("gamePanel");
    let gameRefreshTimer = null;
    let gameRequestBusy = false;
    let latestGame = null;
    let attemptStartedAt = 0;
    let attemptRunning = false;
    let lastTurnKey = "";

    function roomMessageText(message, type = "") {
        if (!roomMessage) return;

        roomMessage.textContent = message;
        roomMessage.className = `room-message ${type}`;
    }

    function clearRoomMessage() {
        if (!roomMessage) return;

        roomMessage.textContent = "";
        roomMessage.className = "room-message";
    }

    function normalizeRoom(room) {
        if (!room) {
            throw new Error("Сервер вернул пустую комнату.");
        }

        return {
            ...room,
            player_count: room.player_count ?? room.players?.length ?? 0,
            max_players: room.max_players ?? 4,
            players: Array.isArray(room.players) ? room.players : []
        };
    }


    // ----------------------------------------
    // OPEN ROOM
    // ----------------------------------------

    function openRoom(room) {
        room = normalizeRoom(room);

        stopLobbyRefresh();

        activeRoom = room;
        activeRoomCode = room.code;

        try {
            sessionStorage.setItem("timeguess_room", room.code);
        } catch {}

        hide(roomActions);
        hide(oldCreatedRoom);

        show(lobbyPanel);

        renderLobby(room);
        startLobbyRefresh();
    }


    // ----------------------------------------
    // CREATE ROOM
    // ----------------------------------------

    if (createRoomButton) {
        createRoomButton.addEventListener("click", async () => {
            const targetScore = Number(
                targetScoreSelect?.value || 7
            );

            const oldText = createRoomButton.textContent;

            setLoading(
                createRoomButton,
                true,
                "Создаём комнату...",
                oldText
            );

            clearRoomMessage();

            try {
                const response = await apiRequest("/api/rooms", {
                    method: "POST",
                    body: jsonBody({
                        target_score: targetScore
                    })
                });

                openRoom(response.room);

                roomMessageText(
                    "Комната успешно создана!",
                    "success"
                );

            } catch (error) {
                roomMessageText(error.message, "error");

            } finally {
                createRoomButton.disabled = false;
                createRoomButton.textContent = oldText;
            }
        });
    }


    // ----------------------------------------
    // JOIN ROOM
    // ----------------------------------------

    if (joinRoomCode) {
        joinRoomCode.addEventListener("input", () => {
            joinRoomCode.value = joinRoomCode.value
                .toUpperCase()
                .replace(/[^A-Z0-9]/g, "")
                .slice(0, 6);
        });

        joinRoomCode.addEventListener("keydown", (event) => {
            if (event.key === "Enter") {
                event.preventDefault();
                joinRoom();
            }
        });
    }

    if (joinRoomButton) {
        joinRoomButton.addEventListener("click", joinRoom);
    }

    async function joinRoom() {
        const code = joinRoomCode?.value.trim().toUpperCase();

        if (!code || code.length !== 6) {
            roomMessageText(
                "Введи правильный шестизначный код.",
                "error"
            );
            return;
        }

        const oldText = joinRoomButton?.textContent || "Присоединиться";

        setLoading(
            joinRoomButton,
            true,
            "Подключаемся...",
            oldText
        );

        clearRoomMessage();

        try {
            const response = await apiRequest("/api/rooms/join", {
                method: "POST",
                body: jsonBody({ code })
            });

            openRoom(response.room);

            roomMessageText(
                "Ты присоединился к комнате!",
                "success"
            );

        } catch (error) {
            roomMessageText(error.message, "error");

        } finally {
            if (joinRoomButton) {
                joinRoomButton.disabled = false;
                joinRoomButton.textContent = oldText;
            }
        }
    }


    // ----------------------------------------
    // RENDER LOBBY
    // ----------------------------------------

    function renderLobby(rawRoom) {
        const room = normalizeRoom(rawRoom);

        activeRoom = room;

        const roomIsPlaying = room.status === "playing";
        if (roomIsPlaying) {
            hide(lobbyPanel);
            hide(roomActions);
            show(gamePanel);
            startGameRefresh();
            refreshGame();
        } else {
            stopGameRefresh();
            hide(gamePanel);
            show(lobbyPanel);
        }

        setText(lobbyRoomCode, room.code);
        setText(oldRoomCode, room.code);

        setText(lobbyTargetScore, room.target_score);
        setText(oldRoomTargetScore, room.target_score);

        setText(
            lobbyPlayerCount,
            `${room.player_count} / ${room.max_players}`
        );

        setText(
            lobbyStatus,
            room.status === "playing" ? "PLAYING" : "LOBBY"
        );

        if (lobbyStatus) {
            lobbyStatus.className =
                room.status === "playing"
                    ? "room-status playing"
                    : "room-status";
        }

        if (lobbyPlayers) {
            lobbyPlayers.replaceChildren();

            room.players.forEach((player, index) => {
                const card = document.createElement("div");
                card.className = "lobby-player";

                const avatar = document.createElement("div");
                avatar.className = "lobby-player-avatar";
                avatar.textContent =
                    (player.username || "?").charAt(0).toUpperCase();

                const info = document.createElement("div");
                info.className = "lobby-player-info";

                const name = document.createElement("strong");
                name.textContent = player.username || "Игрок";

                const role = document.createElement("span");

                const isHost = player.user_id === room.host_id;

                role.textContent = isHost
                    ? "Создатель комнаты"
                    : `Игрок ${index + 1}`;

                info.append(name, role);

                const status = document.createElement("span");

                status.className = player.connected
                    ? "player-online"
                    : "player-offline";

                status.textContent = player.connected
                    ? "ONLINE"
                    : "OFFLINE";

                if (isHost) {
                    card.classList.add("lobby-host");
                }

                card.append(avatar, info, status);
                lobbyPlayers.append(card);
            });

            // Empty slots.
            for (let i = room.players.length; i < room.max_players; i++) {
                const slot = document.createElement("div");
                slot.className = "lobby-player lobby-empty-slot";

                const label = document.createElement("span");
                label.textContent = "Ожидаем игрока...";

                slot.append(label);
                lobbyPlayers.append(slot);
            }
        }

        const isHost = currentUserId === room.host_id;
        const isPlaying = room.status === "playing";

        if (startRoomButton) {
            if (isHost && !isPlaying) {
                show(startRoomButton);
                startRoomButton.disabled = room.player_count < 2;
            } else {
                hide(startRoomButton);
            }
        }

        if (lobbyHint) {
            if (isPlaying) {
                lobbyHint.textContent =
                    "Игра запущена.";
            } else if (isHost && room.player_count < 2) {
                lobbyHint.textContent =
                    "Ожидаем второго игрока. Для старта нужно минимум два участника.";
            } else if (isHost) {
                lobbyHint.textContent =
                    "Все готовы? Ты можешь начать игру.";
            } else {
                lobbyHint.textContent =
                    "Ожидаем начала игры от создателя комнаты.";
            }
        }
    }


    // ----------------------------------------
    // REFRESH LOBBY
    // ----------------------------------------

    function startLobbyRefresh() {
        stopLobbyRefresh();

        lobbyTimer = setInterval(() => {
            refreshLobby();
        }, 2500);
    }

    function stopLobbyRefresh() {
        if (lobbyTimer) {
            clearInterval(lobbyTimer);
            lobbyTimer = null;
        }
    }

    async function refreshLobby() {
        if (!activeRoomCode || lobbyRequestBusy) return;

        lobbyRequestBusy = true;

        try {
            const response = await apiRequest(
                `/api/rooms/${encodeURIComponent(activeRoomCode)}`
            );

            renderLobby(response.room);

        } catch (error) {
            stopLobbyRefresh();

            roomMessageText(
                `Ошибка обновления комнаты: ${error.message}`,
                "error"
            );

        } finally {
            lobbyRequestBusy = false;
        }
    }


    // ----------------------------------------
    // RESTORE ROOM AFTER RELOAD
    // ----------------------------------------

    async function restoreRoom() {
        if (restoringRoom) return;

        restoringRoom = true;

        let savedCode = null;

        try {
            savedCode = sessionStorage.getItem("timeguess_room");
        } catch {}

        if (!savedCode) {
            restoringRoom = false;
            return;
        }

        try {
            const response = await apiRequest(
                `/api/rooms/${encodeURIComponent(savedCode)}`
            );

            openRoom(response.room);

        } catch {
            try {
                sessionStorage.removeItem("timeguess_room");
            } catch {}
        } finally {
            restoringRoom = false;
        }
    }


    // ----------------------------------------
    // COPY CODE
    // ----------------------------------------

    if (copyRoomCodeButton) {
        copyRoomCodeButton.addEventListener("click", async () => {
            if (!activeRoomCode) return;

            try {
                await navigator.clipboard.writeText(activeRoomCode);

                const oldText = copyRoomCodeButton.textContent;
                copyRoomCodeButton.textContent = "Скопировано!";

                setTimeout(() => {
                    copyRoomCodeButton.textContent = oldText;
                }, 1500);

            } catch {
                roomMessageText(
                    "Не удалось скопировать код. Выдели его вручную.",
                    "error"
                );
            }
        });
    }


    // ----------------------------------------
    // START GAME
    // ----------------------------------------

    if (startRoomButton) {
        startRoomButton.addEventListener("click", async () => {
            if (!activeRoomCode || startRoomButton.disabled) return;

            const oldText = startRoomButton.textContent;

            setLoading(
                startRoomButton,
                true,
                "Запускаем...",
                oldText
            );

            try {
                await apiRequest(
                    `/api/rooms/${encodeURIComponent(activeRoomCode)}/start`,
                    { method: "POST" }
                );

                await refreshLobby();

                roomMessageText(
                    "Комната запущена!",
                    "success"
                );

            } catch (error) {
                roomMessageText(error.message, "error");

            } finally {
                startRoomButton.disabled = false;
                startRoomButton.textContent = oldText;

                if (activeRoom?.status === "playing") {
                    hide(startRoomButton);
                }
            }
        });
    }



    // ========================================
    // LIVE GAME
    // ========================================

    const roundNumber = $("roundNumber");
    const phaseLabel = $("phaseLabel");
    const scorePlayers = $("scorePlayers");
    const scoreTarget = $("scoreTarget");
    const setterPanel = $("setterPanel");
    const targetTimeInput = $("targetTimeInput");
    const setTargetButton = $("setTargetButton");
    const randomTargetButton = $("randomTargetButton");
    const timerPanel = $("timerPanel");
    const attemptTimerButton = $("attemptTimerButton");
    const attemptTimerStatus = $("attemptTimerStatus");
    const waitingPanel = $("waitingPanel");
    const waitingTitle = $("waitingTitle");
    const waitingText = $("waitingText");
    const targetPanel = $("targetPanel");
    const targetTime = $("targetTime");
    const roundResultPanel = $("roundResultPanel");
    const roundWinner = $("roundWinner");
    const roundResults = $("roundResults");
    const nextRoundButton = $("nextRoundButton");
    const finalPanel = $("finalPanel");
    const finalWinner = $("finalWinner");

    function stopGameRefresh() {
        if (gameRefreshTimer) {
            clearInterval(gameRefreshTimer);
            gameRefreshTimer = null;
        }
        gameRequestBusy = false;
    }

    function startGameRefresh() {
        if (gameRefreshTimer) return;
        gameRefreshTimer = setInterval(refreshGame, 1200);
    }

    async function refreshGame() {
        if (!activeRoomCode || gameRequestBusy || !gamePanel || gamePanel.classList.contains("hidden")) return;
        gameRequestBusy = true;
        try {
            const data = await apiRequest(`/api/rooms/${encodeURIComponent(activeRoomCode)}/game`);
            latestGame = data;
            renderGame(data);
        } catch (error) {
            if (error.message && !error.message.includes("ещё не началась")) {
                roomMessageText(error.message, "error");
            }
        } finally {
            gameRequestBusy = false;
        }
    }

    function playerName(id, players) {
        return players.find(p => p.user_id === id)?.username || "Игрок";
    }

    function renderGame(data) {
        const game = data.game || {};
        const players = data.players || [];
        const myId = data.my_id;
        const hostId = data.host_id;
        const phase = game.phase || "choosing_target";
        const currentId = game.attempt_order?.[game.turn_index] || null;
        const turnKey = `${game.round}:${currentId}:${game.turn_index}`;

        setText(roundNumber, game.round || 1);
        setText(scoreTarget, `ЦЕЛЬ: ${data.target_score}`);
        setText(phaseLabel, ({
            choosing_target: "ВЫБОР ВРЕМЕНИ",
            playing_turn: "ПОПЫТКИ",
            round_finished: "РАУНД ЗАВЕРШЁН",
            finished: "ИГРА ЗАВЕРШЕНА"
        })[phase] || "ИГРА");

        if (scorePlayers) {
            scorePlayers.replaceChildren();
            [...players].sort((a,b) => b.score - a.score).forEach((player, index) => {
                const row = document.createElement("div");
                row.className = "score-player";
                const name = document.createElement("span");
                name.textContent = `${index + 1}. ${player.username}${player.user_id === hostId ? " ★" : ""}`;
                const score = document.createElement("strong");
                score.textContent = `${player.score} очк.`;
                row.append(name, score);
                scorePlayers.append(row);
            });
        }

        const isSetter = phase === "choosing_target" && myId === game.setter_id;
        setterPanel?.classList.toggle("hidden", !isSetter);
        timerPanel?.classList.add("hidden");
        waitingPanel?.classList.add("hidden");
        roundResultPanel?.classList.add("hidden");
        finalPanel?.classList.add("hidden");
        const targetIsKnown = ["playing_turn", "round_finished", "finished"].includes(phase)
            && game.target_time !== null && game.target_time !== undefined;
        targetPanel?.classList.toggle("hidden", !targetIsKnown);
        if (targetIsKnown) {
            setText(targetTime, Number(game.target_time).toFixed(2));
        }

        if (["round_finished", "finished"].includes(phase)) {
            const winnerName = game.winner_id ? playerName(game.winner_id, players) : null;
            if (phase === "round_finished") {
                roundResultPanel?.classList.remove("hidden");
                setText(roundWinner, winnerName
                    ? `${winnerName} выиграл раунд!`
                    : "Ничья! Никто не получает очко.");
                if (roundResults) {
                    roundResults.replaceChildren();
                    (game.last_results || game.attempts || []).forEach((attempt, index) => {
                        const row = document.createElement("div");
                        row.className = "result-row";
                        const who = document.createElement("span");
                        who.textContent = playerName(attempt.player_id, players);
                        const result = document.createElement("strong");
                        result.textContent = `${Number(attempt.time).toFixed(2)} сек.`;
                        const diff = document.createElement("small");
                        diff.textContent = `± ${Number(attempt.difference).toFixed(2)}`;
                        row.append(who, result, diff);
                        roundResults.append(row);
                    });
                }
                nextRoundButton?.classList.toggle("hidden", myId !== hostId);
            } else {
                finalPanel?.classList.remove("hidden");
                setText(finalWinner, `${winnerName} победил в матче!`);
            }
            attemptRunning = false;
            lastTurnKey = turnKey;
            if (attemptTimerButton) attemptTimerButton.textContent = "Старт";
            setText(attemptTimerStatus, "Раунд завершён");
            return;
        }

        if (phase === "choosing_target") {
            if (!isSetter) {
                waitingPanel?.classList.remove("hidden");
                setText(waitingTitle, "Ожидаем загадку");
                setText(waitingText, `${playerName(game.setter_id, players)} выбирает загаданное время.`);
            }
            return;
        }

        if (phase === "playing_turn") {
            const myTurn = currentId === myId;
            if (myTurn) {
                timerPanel?.classList.remove("hidden");
                if (lastTurnKey !== turnKey) {
                    attemptRunning = false;
                    if (attemptTimerButton) attemptTimerButton.textContent = "Старт";
                    setText(attemptTimerStatus, "Готов к старту");
                    lastTurnKey = turnKey;
                }
            } else {
                waitingPanel?.classList.remove("hidden");
                setText(waitingTitle, `${playerName(currentId, players)} сейчас играет`);
                setText(waitingText, "Твоя попытка будет следующей. Загаданное время видно всем игрокам.");
                if (lastTurnKey !== turnKey) {
                    attemptRunning = false;
                    lastTurnKey = turnKey;
                }
            }
        }
    }

    async function chooseTarget(randomChoice) {
        if (!activeRoomCode) return;
        const payload = randomChoice
            ? { random: true }
            : { random: false, value: Number(targetTimeInput?.value) };
        try {
            await apiRequest(`/api/rooms/${encodeURIComponent(activeRoomCode)}/target`, {
                method: "POST",
                body: jsonBody(payload)
            });
            await refreshGame();
        } catch (error) {
            roomMessageText(error.message, "error");
        }
    }

    setTargetButton?.addEventListener("click", () => chooseTarget(false));
    randomTargetButton?.addEventListener("click", () => chooseTarget(true));

    attemptTimerButton?.addEventListener("click", async () => {
        if (!activeRoomCode) return;
        if (!attemptRunning) {
            attemptStartedAt = performance.now();
            attemptRunning = true;
            attemptTimerButton.textContent = "Стоп";
            setText(attemptTimerStatus, "Таймер запущен — нажми «Стоп», когда решишь");
            return;
        }
        const measured = Math.max(0, (performance.now() - attemptStartedAt) / 1000);
        attemptRunning = false;
        attemptTimerButton.disabled = true;
        attemptTimerButton.textContent = "Отправляем...";
        setText(attemptTimerStatus, "Результат отправлен");
        try {
            await apiRequest(`/api/rooms/${encodeURIComponent(activeRoomCode)}/attempt`, {
                method: "POST",
                body: jsonBody({ value: Number(measured.toFixed(2)) })
            });
            await refreshGame();
        } catch (error) {
            roomMessageText(error.message, "error");
            attemptTimerButton.textContent = "Старт";
            setText(attemptTimerStatus, "Попробуй ещё раз");
        } finally {
            attemptTimerButton.disabled = false;
        }
    });

    nextRoundButton?.addEventListener("click", async () => {
        if (!activeRoomCode) return;
        nextRoundButton.disabled = true;
        try {
            await apiRequest(`/api/rooms/${encodeURIComponent(activeRoomCode)}/next-round`, { method: "POST" });
            await refreshGame();
        } catch (error) {
            roomMessageText(error.message, "error");
        } finally {
            nextRoundButton.disabled = false;
        }
    });

    // ----------------------------------------
    // LEAVE ROOM
    // ----------------------------------------

    if (leaveRoomButton) {
        leaveRoomButton.addEventListener("click", async () => {
            if (!activeRoomCode) return;

            const oldText = leaveRoomButton.textContent;

            setLoading(
                leaveRoomButton,
                true,
                "Выходим...",
                oldText
            );

            try {
                await apiRequest(
                    `/api/rooms/${encodeURIComponent(activeRoomCode)}/leave`,
                    { method: "POST" }
                );

                closeLobby();

            } catch (error) {
                roomMessageText(error.message, "error");

            } finally {
                leaveRoomButton.disabled = false;
                leaveRoomButton.textContent = oldText;
            }
        });
    }

    const leaveGameButton = $("leaveGameButton");
    leaveGameButton?.addEventListener("click", async () => {
        if (!activeRoomCode) return;
        leaveGameButton.disabled = true;
        try {
            await apiRequest(`/api/rooms/${encodeURIComponent(activeRoomCode)}/leave`, { method: "POST" });
            closeLobby();
        } catch (error) {
            roomMessageText(error.message, "error");
        } finally {
            leaveGameButton.disabled = false;
        }
    });

    function closeLobby() {
        stopLobbyRefresh();
        stopGameRefresh();

        activeRoomCode = null;
        activeRoom = null;

        try {
            sessionStorage.removeItem("timeguess_room");
        } catch {}

        hide(lobbyPanel);
        hide(gamePanel);
        show(roomActions);

        clearRoomMessage();
    }


    // ========================================
    // LOGOUT
    // ========================================

    if (logoutButton) {
        logoutButton.addEventListener("click", async () => {
            logoutButton.disabled = true;

            try {
                if (activeRoomCode) {
                    try {
                        await apiRequest(
                            `/api/rooms/${encodeURIComponent(activeRoomCode)}/leave`,
                            { method: "POST" }
                        );
                    } catch {}
                }

                stopLobbyRefresh();

                await apiRequest("/api/auth/logout", {
                    method: "POST"
                });

                activeRoomCode = null;
                activeRoom = null;
                currentUser = null;
                currentUserId = null;

                try {
                    sessionStorage.removeItem("timeguess_room");
                } catch {}

                hide(dashboardScreen);
                show(authScreen);

                loginForm?.reset();
                registerForm?.reset();

                showLogin();

            } catch (error) {
                alert(error.message);

            } finally {
                logoutButton.disabled = false;
            }
        });
    }


    // ========================================
    // STOPWATCH
    // ========================================

    const stopwatchDisplay = $("stopwatchDisplay");
    const stopwatchStart = $("stopwatchStart");
    const stopwatchReset = $("stopwatchReset");
    const stopwatchLap = $("stopwatchLap");
    const stopwatchLaps = $("stopwatchLaps");
    const lapCount = $("lapCount");

    let stopwatchRunning = false;
    let stopwatchStartedAt = 0;
    let stopwatchElapsed = 0;
    let stopwatchFrame = null;
    let stopwatchLapNumber = 0;

    function formatStopwatchTime(milliseconds) {
        const centiseconds = Math.floor(milliseconds / 10);

        const minutes = Math.floor(centiseconds / 6000);
        const seconds = Math.floor((centiseconds % 6000) / 100);
        const hundredths = centiseconds % 100;

        return (
            String(minutes).padStart(2, "0") + ":" +
            String(seconds).padStart(2, "0") + "." +
            String(hundredths).padStart(2, "0")
        );
    }

    function updateStopwatch() {
        if (!stopwatchRunning) return;

        const elapsed =
            stopwatchElapsed +
            (performance.now() - stopwatchStartedAt);

        setText(
            stopwatchDisplay,
            formatStopwatchTime(elapsed)
        );

        stopwatchFrame = requestAnimationFrame(updateStopwatch);
    }

    function toggleStopwatch() {
        if (!stopwatchDisplay) return;

        if (!stopwatchRunning) {
            stopwatchRunning = true;
            stopwatchStartedAt = performance.now();

            if (stopwatchStart) {
                stopwatchStart.textContent = "Пауза";
            }

            if (stopwatchLap) {
                stopwatchLap.disabled = false;
            }

            updateStopwatch();

        } else {
            stopwatchElapsed +=
                performance.now() - stopwatchStartedAt;

            stopwatchRunning = false;

            cancelAnimationFrame(stopwatchFrame);

            if (stopwatchStart) {
                stopwatchStart.textContent = "Продолжить";
            }
        }
    }

    function resetStopwatch() {
        stopwatchRunning = false;
        stopwatchElapsed = 0;
        stopwatchLapNumber = 0;

        cancelAnimationFrame(stopwatchFrame);

        setText(stopwatchDisplay, "00:00.00");
        setText(lapCount, "0");

        if (stopwatchStart) {
            stopwatchStart.textContent = "Старт";
        }

        if (stopwatchLap) {
            stopwatchLap.disabled = true;
        }

        if (stopwatchLaps) {
            stopwatchLaps.innerHTML =
                '<div class="laps-empty">Здесь появятся твои результаты</div>';
        }
    }

    function addStopwatchLap() {
        if (!stopwatchRunning || !stopwatchLaps) return;

        const elapsed =
            stopwatchElapsed +
            (performance.now() - stopwatchStartedAt);

        stopwatchLapNumber++;

        if (stopwatchLapNumber === 1) {
            stopwatchLaps.replaceChildren();
        }

        const lap = document.createElement("div");
        lap.className = "lap-item";

        const number = document.createElement("span");
        number.textContent = `Круг ${stopwatchLapNumber}`;

        const time = document.createElement("strong");
        time.textContent = formatStopwatchTime(elapsed);

        lap.append(number, time);
        stopwatchLaps.prepend(lap);

        setText(lapCount, stopwatchLapNumber);
    }

    stopwatchStart?.addEventListener("click", toggleStopwatch);
    stopwatchReset?.addEventListener("click", resetStopwatch);
    stopwatchLap?.addEventListener("click", addStopwatchLap);


    // ========================================
    // START APPLICATION
    // ========================================

    initializeApp();

});
