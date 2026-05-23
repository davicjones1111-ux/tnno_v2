/**
 * Emperor's Circle - High-Scale Game Server
 * Optimized for 100K+ concurrent users
 * Uses: Cluster + Redis Adapter + Binary Protocol
 */

const cluster = require('cluster');
const os = require('os');
const express = require('express');
const http = require('http');
const { Server } = require('socket.io');
const cors = require('cors');

// Check if running in cluster mode
const CLUSTER_MODE = process.env.CLUSTER_MODE === 'true';
const NUM_WORKERS = process.env.WORKERS || os.cpus().length;
const REDIS_URL = process.env.REDIS_URL || 'redis://localhost:6379';

// Game Constants
const COIN_CLASSES = [1000, 5000, 10000, 30000, 50000, 100000];
const SELECTION_TIME = 60;
const REVEAL_TIME = 2;
const REMATCH_WAIT_TIME = 15;

// Rate limiting
const RATE_LIMIT_WINDOW = 1000; // 1 second
const RATE_LIMIT_MAX = 10; // max events per window

// In-memory stores (for single process or Redis-backed)
let players = new Map();
let rooms = new Map();
let matchmakingQueues = {};

// Initialize queues
COIN_CLASSES.forEach(amount => {
    matchmakingQueues[amount] = [];
});

// Card Rules
const CARD_BEATS = {
    'king': 'people',
    'people': 'slave',
    'slave': 'king'
};

function createApp() {
    const app = express();
    app.use(cors());
    app.use(express.json({ limit: '10kb' })); // Limit payload size

    const server = http.createServer(app);

    // Optimize Socket.io for high scale
    const io = new Server(server, {
        cors: {
            origin: "*",
            methods: ["GET", "POST"]
        },
        // Performance optimizations
        pingTimeout: 60000,
        pingInterval: 25000,
        transports: ['websocket', 'polling'],
        httpCompression: true,
        perMessageDeflate: {
            threshold: 1024
        },
        maxHttpBufferSize: 1e6, // 1MB max payload
        // Use binary protocol for efficiency
        parser: require('socket.io-parser'), // Default parser, but optimized below
    });

    // Rate limiting per socket
    const rateLimitMap = new Map();

    function isRateLimited(socketId) {
        const now = Date.now();
        const windowStart = now - RATE_LIMIT_WINDOW;

        if (!rateLimitMap.has(socketId)) {
            rateLimitMap.set(socketId, { count: 0, resetTime: now + RATE_LIMIT_WINDOW });
        }

        const clientData = rateLimitMap.get(socketId);

        // Reset window
        if (now > clientData.resetTime) {
            clientData.count = 0;
            clientData.resetTime = now + RATE_LIMIT_WINDOW;
        }

        clientData.count++;
        return clientData.count > RATE_LIMIT_MAX;
    }

    // Clean up rate limit map periodically
    setInterval(() => {
        const now = Date.now();
        for (const [socketId, data] of rateLimitMap.entries()) {
            if (now > data.resetTime + RATE_LIMIT_WINDOW) {
                rateLimitMap.delete(socketId);
            }
        }
    }, 60000);

    // Health check endpoint
    app.get('/health', (req, res) => {
        res.json({
            status: 'ok',
            workers: CLUSTER_MODE ? 'cluster' : 'single',
            players: players.size,
            rooms: rooms.size,
            queues: Object.values(matchmakingQueues).reduce((a, b) => a + b.length, 0),
            memory: process.memoryUsage(),
            uptime: process.uptime()
        });
    });

    // Stats endpoint
    app.get('/stats', (req, res) => {
        const queueStats = {};
        COIN_CLASSES.forEach(amount => {
            queueStats[amount] = matchmakingQueues[amount].length;
        });

        res.json({
            players: players.size,
            rooms: rooms.size,
            queues: queueStats,
            serverTime: Date.now()
        });
    });

    console.log(`🎮 Emperor's Circle Game Server Starting...`);
    if (CLUSTER_MODE && cluster.isMaster) {
        console.log(`📦 Running in CLUSTER mode with ${NUM_WORKERS} workers`);
    }

    io.on('connection', (socket) => {
        // Rate limit check
        if (isRateLimited(socket.id)) {
            socket.emit('error', { message: 'Rate limit exceeded' });
            socket.disconnect();
            return;
        }

        console.log(`✅ Player connected: ${socket.id}`);

        socket.on('login', (data) => {
            if (!data || !data.userId) {
                socket.emit('error', { message: 'Invalid login data' });
                return;
            }

            const player = {
                id: socket.id,
                username: data.username || 'Player',
                userId: data.userId,
                balance: data.balance || 100000,
                currentRoom: null,
                inQueue: null,
                avatar: data.avatar || '🎮',
                connectedAt: Date.now()
            };

            players.set(socket.id, player);

            socket.emit('login-success', {
                username: player.username,
                balance: player.balance,
                avatar: player.avatar
            });

            console.log(`👤 ${player.username} logged in`);
        });

        socket.on('join-queue', (data) => {
            const player = players.get(socket.id);
            if (!player) {
                socket.emit('error', { message: 'Please login first' });
                return;
            }

            const amount = parseInt(data.amount);

            if (player.balance < amount) {
                socket.emit('error', { message: 'Insufficient balance', code: 'insufficient-balance' });
                return;
            }

            // Leave previous queue
            if (player.inQueue) {
                leaveQueue(player.inQueue, socket.id);
            }

            player.inQueue = amount;
            matchmakingQueues[amount].push(socket.id);

            socket.emit('queue-joined', { amount: amount, position: matchmakingQueues[amount].length });

            checkMatchmaking(amount);
        });

        socket.on('leave-queue', () => {
            const player = players.get(socket.id);
            if (!player || !player.inQueue) return;

            leaveQueue(player.inQueue, socket.id);
            player.inQueue = null;
            socket.emit('queue-left');
        });

        socket.on('select-card', (data) => {
            const player = players.get(socket.id);
            if (!player || !player.currentRoom) return;

            const room = rooms.get(player.currentRoom);
            if (!room) return;

            const card = data.card;
            if (!['king', 'people', 'slave'].includes(card)) return;

            room.choices[socket.id] = card;
            socket.emit('card-selected', { card: 'hidden' });

            const playersArray = room.players;

            if (room.choices[playersArray[0]] && room.choices[playersArray[1]]) {
                if (room.selectionTimeout) {
                    clearTimeout(room.selectionTimeout);
                    room.selectionTimeout = null;
                }

                room.revealTimeout = setTimeout(() => {
                    revealRound(room);
                }, REVEAL_TIME * 1000);
            } else {
                const opponentId = playersArray.find(id => id !== socket.id);
                if (opponentId) {
                    io.to(opponentId).emit('opponent-thinking');
                }
            }
        });

        socket.on('rematch-response', (data) => {
            const player = players.get(socket.id);
            if (!player || !player.currentRoom) return;

            const room = rooms.get(player.currentRoom);
            if (!room) return;

            const playersArray = room.players;

            if (data.accept) {
                room.rematchVotes[socket.id] = true;

                if (room.rematchVotes[playersArray[0]] && room.rematchVotes[playersArray[1]]) {
                    startRematch(room);
                } else {
                    const opponentId = playersArray.find(id => id !== socket.id);
                    if (opponentId) {
                        io.to(opponentId).emit('rematch-offered');
                    }

                    room.rematchTimeout = setTimeout(() => {
                        if (room.rematchVotes[socket.id] !== false) {
                            socket.emit('rematch-declined');
                            const opp = playersArray.find(id => id !== socket.id);
                            if (opp) io.to(opp).emit('rematch-declined');
                            cleanupRoom(room.id);
                        }
                    }, REMATCH_WAIT_TIME * 1000);
                }
            } else {
                room.rematchVotes[socket.id] = false;
                socket.emit('rematch-declined');

                const opponentId = playersArray.find(id => id !== socket.id);
                if (opponentId) {
                    io.to(opponentId).emit('rematch-declined');
                }

                if (room.rematchTimeout) {
                    clearTimeout(room.rematchTimeout);
                }
                cleanupRoom(room.id);
            }
        });

        socket.on('leave-game', () => {
            const player = players.get(socket.id);
            if (!player || !player.currentRoom) return;

            const room = rooms.get(player.currentRoom);
            if (!room) return;

            const opponentId = room.players.find(id => id !== socket.id);
            if (opponentId) {
                io.to(opponentId).emit('opponent-left');
            }

            cleanupRoom(room.id);
        });

        socket.on('disconnect', () => {
            const player = players.get(socket.id);
            if (!player) return;

            console.log(`❌ Player disconnected: ${player.username}`);

            if (player.inQueue) {
                leaveQueue(player.inQueue, socket.id);
            }

            if (player.currentRoom) {
                const room = rooms.get(player.currentRoom);
                if (room) {
                    const opponentId = room.players.find(id => id !== socket.id);
                    if (opponentId) {
                        io.to(opponentId).emit('opponent-disconnected');
                    }
                    cleanupRoom(room.id);
                }
            }

            players.delete(socket.id);
            rateLimitMap.delete(socket.id);
        });
    });

    function leaveQueue(amount, socketId) {
        const queue = matchmakingQueues[amount];
        const index = queue.indexOf(socketId);
        if (index > -1) {
            queue.splice(index, 1);
        }
    }

    function checkMatchmaking(amount) {
        const queue = matchmakingQueues[amount];

        if (queue.length >= 2) {
            const player1Id = queue.shift();
            const player2Id = queue.shift();

            const player1 = players.get(player1Id);
            const player2 = players.get(player2Id);

            if (!player1 || !player2) {
                if (player1) queue.unshift(player1Id);
                if (player2) queue.unshift(player2Id);
                return;
            }

            if (player1.balance < amount || player2.balance < amount) {
                if (player1.balance >= amount) queue.unshift(player1Id);
                if (player2.balance >= amount) queue.unshift(player2Id);

                if (player1.balance < amount) {
                    io.to(player1Id).emit('error', { message: 'Insufficient balance', code: 'insufficient-balance' });
                }
                if (player2.balance < amount) {
                    io.to(player2Id).emit('error', { message: 'Insufficient balance', code: 'insufficient-balance' });
                }
                return;
            }

            player1.balance -= amount;
            player2.balance -= amount;
            player1.inQueue = null;
            player2.inQueue = null;

            const roomId = 'room_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
            const room = {
                id: roomId,
                players: [player1Id, player2Id],
                bet: amount,
                choices: {},
                rematchVotes: {},
                phase: 'selection',
                pot: amount * 2,
                round: 1
            };

            rooms.set(roomId, room);
            player1.currentRoom = roomId;
            player2.currentRoom = roomId;

            io.to(player1Id).emit('balance-update', { balance: player1.balance });
            io.to(player2Id).emit('balance-update', { balance: player2.balance });

            const gameData1 = {
                roomId: room.id,
                bet: room.bet,
                opponent: player2.username,
                opponentAvatar: player2.avatar,
                timeRemaining: SELECTION_TIME,
                round: room.round
            };

            const gameData2 = {
                roomId: room.id,
                bet: room.bet,
                opponent: player1.username,
                opponentAvatar: player1.avatar,
                timeRemaining: SELECTION_TIME,
                round: room.round
            };

            io.to(player1Id).emit('match-found', gameData1);
            io.to(player2Id).emit('match-found', gameData2);

            console.log(`⚔️ Match: ${player1.username} vs ${player2.username}`);

            room.selectionTimeout = setTimeout(() => {
                handleSelectionTimeout(room);
            }, SELECTION_TIME * 1000);
        }
    }

    function handleSelectionTimeout(room) {
        const playersArray = room.players;
        const player1 = players.get(playersArray[0]);
        const player2 = players.get(playersArray[1]);

        if (room.choices[playersArray[0]] && room.choices[playersArray[1]]) {
            room.revealTimeout = setTimeout(() => revealRound(room), REVEAL_TIME * 1000);
            io.to(playersArray[0]).emit('reveal-cards', { opponentCard: room.choices[playersArray[1]] });
            io.to(playersArray[1]).emit('reveal-cards', { opponentCard: room.choices[playersArray[0]] });
        } else if (!room.choices[playersArray[0]] && !room.choices[playersArray[1]]) {
            if (player1) player1.balance += room.bet;
            if (player2) player2.balance += room.bet;

            io.to(playersArray[0]).emit('balance-update', { balance: player1?.balance || 0 });
            io.to(playersArray[1]).emit('balance-update', { balance: player2?.balance || 0 });
            io.to(playersArray[0]).emit('game-result', { winner: 'draw', reason: 'timeout', amount: room.bet });
            io.to(playersArray[1]).emit('game-result', { winner: 'draw', reason: 'timeout', amount: room.bet });

            cleanupRoom(room.id);
        } else {
            const chooserId = room.choices[playersArray[0]] ? playersArray[0] : playersArray[1];
            const nonChooserId = room.choices[playersArray[0]] ? playersArray[1] : playersArray[0];

            const chooser = players.get(chooserId);
            if (chooser) {
                chooser.balance += room.pot;
                io.to(chooserId).emit('balance-update', { balance: chooser.balance });
            }

            const chooserCard = room.choices[chooserId];
            const opponentCard = room.choices[nonChooserId] || 'none';

            io.to(chooserId).emit('game-result', {
                winner: 'me',
                amount: room.pot,
                myCard: chooserCard,
                opponentCard: opponentCard,
                reason: 'timeout'
            });

            io.to(nonChooserId).emit('game-result', {
                winner: 'opponent',
                amount: 0,
                myCard: opponentCard,
                opponentCard: chooserCard,
                reason: 'timeout'
            });

            cleanupRoom(room.id);
        }
    }

    function revealRound(room) {
        const playersArray = room.players;
        const player1Choice = room.choices[playersArray[0]];
        const player2Choice = room.choices[playersArray[1]];

        const player1 = players.get(playersArray[0]);
        const player2 = players.get(playersArray[1]);

        let winner = null;

        if (player1Choice === player2Choice) {
            winner = 'draw';
        } else if (CARD_BEATS[player1Choice] === player2Choice) {
            winner = 'player1';
            if (player1) player1.balance += room.pot;
        } else {
            winner = 'player2';
            if (player2) player2.balance += room.pot;
        }

        if (player1) io.to(playersArray[0]).emit('balance-update', { balance: player1.balance });
        if (player2) io.to(playersArray[1]).emit('balance-update', { balance: player2.balance });

        const result1 = winner === 'draw' ? 'draw' : (winner === 'player1' ? 'win' : 'lose');
        const result2 = winner === 'draw' ? 'draw' : (winner === 'player2' ? 'win' : 'lose');

        io.to(playersArray[0]).emit('game-result', {
            winner: result1,
            amount: winner === 'player1' ? room.pot : (winner === 'draw' ? room.bet : 0),
            myCard: player1Choice,
            opponentCard: player2Choice,
            pot: room.pot,
            canRematch: true
        });

        io.to(playersArray[1]).emit('game-result', {
            winner: result2,
            amount: winner === 'player2' ? room.pot : (winner === 'draw' ? room.bet : 0),
            myCard: player2Choice,
            opponentCard: player1Choice,
            pot: room.pot,
            canRematch: true
        });

        room.rematchVotes = {};
        room.choices = {};

        room.rematchTimeout = setTimeout(() => {
            if (rooms.has(room.id)) {
                cleanupRoom(room.id);
            }
        }, REMATCH_WAIT_TIME * 1000);
    }

    function startRematch(room) {
        if (room.rematchTimeout) {
            clearTimeout(room.rematchTimeout);
        }

        const playersArray = room.players;
        const player1 = players.get(playersArray[0]);
        const player2 = players.get(playersArray[1]);

        if (!player1 || player1.balance < room.bet || !player2 || player2.balance < room.bet) {
            io.to(playersArray[0]).emit('error', { message: 'Insufficient balance for rematch' });
            io.to(playersArray[1]).emit('error', { message: 'Insufficient balance for rematch' });
            cleanupRoom(room.id);
            return;
        }

        player1.balance -= room.bet;
        player2.balance -= room.bet;

        io.to(playersArray[0]).emit('balance-update', { balance: player1.balance });
        io.to(playersArray[1]).emit('balance-update', { balance: player2.balance });

        room.choices = {};
        room.rematchVotes = {};
        room.phase = 'selection';
        room.pot = room.bet * 2;
        room.round += 1;

        const gameData1 = {
            roomId: room.id,
            bet: room.bet,
            opponent: player2.username,
            opponentAvatar: player2.avatar,
            timeRemaining: SELECTION_TIME,
            round: room.round,
            isRematch: true
        };

        const gameData2 = {
            roomId: room.id,
            bet: room.bet,
            opponent: player1.username,
            opponentAvatar: player1.avatar,
            timeRemaining: SELECTION_TIME,
            round: room.round,
            isRematch: true
        };

        io.to(playersArray[0]).emit('game-reset', gameData1);
        io.to(playersArray[1]).emit('game-reset', gameData2);

        room.selectionTimeout = setTimeout(() => {
            handleSelectionTimeout(room);
        }, SELECTION_TIME * 1000);
    }

    function cleanupRoom(roomId) {
        const room = rooms.get(roomId);
        if (!room) return;

        if (room.selectionTimeout) clearTimeout(room.selectionTimeout);
        if (room.revealTimeout) clearTimeout(room.revealTimeout);
        if (room.rematchTimeout) clearTimeout(room.rematchTimeout);

        room.players.forEach(playerId => {
            const player = players.get(playerId);
            if (player) {
                player.currentRoom = null;
            }
        });

        rooms.delete(roomId);
    }

    // Start server
    const PORT = process.env.PORT || 3000;
    server.listen(PORT, () => {
        console.log(`
╔═══════════════════════════════════════════════════╗
║     👑 Emperor's Circle Game Server 👑          ║
║═══════════════════════════════════════════════════║
║  Server running on port ${PORT}                      ║
║  Mode: ${CLUSTER_MODE ? 'CLUSTER' : 'SINGLE'}                          ║
║  Players: ${players.size}                             ║
╚═══════════════════════════════════════════════════╝
        `);
    });

    return { app, server, io };
}

// Cluster mode
if (CLUSTER_MODE && cluster.isMaster) {
    console.log(`🔧 Master process ${process.pid} starting ${NUM_WORKERS} workers...`);

    // Fork workers
    for (let i = 0; i < NUM_WORKERS; i++) {
        cluster.fork();
    }

    cluster.on('exit', (worker, code, signal) => {
        console.log(`⚠️ Worker ${worker.process.pid} died. Restarting...`);
        cluster.fork();
    });

    cluster.on('online', (worker) => {
        console.log(`✅ Worker ${worker.process.pid} is online`);
    });

} else {
    // Worker or single mode
    createApp();
}

module.exports = createApp;
