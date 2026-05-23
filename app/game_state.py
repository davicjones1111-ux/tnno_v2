"""
Shared game-state backend for Emperor's Circle.
Supports in-memory mode for development and Redis mode for multi-instance production.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from threading import Lock
from typing import Any

from flask import current_app, has_app_context
try:
    import redis
except Exception:  # pragma: no cover - optional in local setups
    redis = None


def _dict_keys_to_str(data: dict[Any, Any] | None) -> dict[str, Any]:
    if not data:
        return {}
    return {str(k): v for k, v in data.items()}


def _dict_keys_to_int(data: dict[Any, Any] | None) -> dict[int, Any]:
    if not data:
        return {}
    out: dict[int, Any] = {}
    for k, v in data.items():
        try:
            out[int(k)] = v
        except Exception:
            continue
    return out


def _serialize_room(room: dict[str, Any]) -> str:
    payload = dict(room)
    payload['players'] = [int(x) for x in payload.get('players', [])]
    payload['rematch_requests'] = sorted([int(x) for x in payload.get('rematch_requests', set())])
    payload['selections'] = _dict_keys_to_str(payload.get('selections'))
    payload['last_seen'] = _dict_keys_to_str(payload.get('last_seen'))
    result = payload.get('result')
    if isinstance(result, dict):
        r = dict(result)
        r['cards'] = _dict_keys_to_str(r.get('cards'))
        r['outcomes'] = _dict_keys_to_str(r.get('outcomes'))
        r['coin_changes'] = _dict_keys_to_str(r.get('coin_changes'))
        payload['result'] = r
    return json.dumps(payload, separators=(',', ':'))


def _deserialize_room(blob: str | bytes | None) -> dict[str, Any] | None:
    if not blob:
        return None
    if isinstance(blob, bytes):
        blob = blob.decode('utf-8')
    payload = json.loads(blob)
    payload['players'] = [int(x) for x in payload.get('players', [])]
    payload['rematch_requests'] = set(int(x) for x in payload.get('rematch_requests', []))
    payload['selections'] = _dict_keys_to_int(payload.get('selections'))
    payload['last_seen'] = _dict_keys_to_int(payload.get('last_seen'))
    result = payload.get('result')
    if isinstance(result, dict):
        result['cards'] = _dict_keys_to_int(result.get('cards'))
        result['outcomes'] = _dict_keys_to_int(result.get('outcomes'))
        result['coin_changes'] = _dict_keys_to_int(result.get('coin_changes'))
    return payload


class InMemoryGameState:
    def __init__(self):
        self.stake_queues: dict[int, list[int]] = {}
        self.user_queue_bet: dict[int, int] = {}
        self.active_rooms: dict[str, dict[str, Any]] = {}
        self.user_room: dict[int, str] = {}

    def pop_room(self, room_id: str):
        return self.active_rooms.pop(room_id, None)

    def get_room(self, room_id: str):
        return self.active_rooms.get(room_id)

    def set_room(self, room_id: str, room: dict[str, Any]):
        self.active_rooms[room_id] = room

    def get_user_room(self, user_id: int):
        return self.user_room.get(user_id)

    def set_user_room(self, user_id: int, room_id: str):
        self.user_room[user_id] = room_id

    def pop_user_room(self, user_id: int):
        return self.user_room.pop(user_id, None)

    def get_user_queue_bet(self, user_id: int):
        return self.user_queue_bet.get(user_id)

    def set_user_queue_bet(self, user_id: int, bet: int):
        self.user_queue_bet[user_id] = bet

    def pop_user_queue_bet(self, user_id: int):
        return self.user_queue_bet.pop(user_id, None)

    def queue_push(self, bet: int, user_id: int):
        self.stake_queues.setdefault(int(bet), []).append(int(user_id))

    def queue_pop(self, bet: int):
        queue = self.stake_queues.get(int(bet), [])
        if not queue:
            return None
        return queue.pop(0)

    def queue_remove(self, bet: int, user_id: int):
        b = int(bet)
        uid = int(user_id)
        queue = self.stake_queues.get(b, [])
        self.stake_queues[b] = [x for x in queue if x != uid]

    def acquire_lock(self, name: str, timeout: int, blocking_timeout: int):
        return None


class RedisGameState:
    def __init__(self, client, prefix: str, room_ttl: int):
        self.client = client
        self.prefix = prefix
        self.room_ttl = int(room_ttl)

    def _k(self, suffix: str) -> str:
        return f'{self.prefix}:{suffix}'

    def _k_room(self, room_id: str) -> str:
        return self._k(f'room:{room_id}')

    def pop_room(self, room_id: str):
        key = self._k_room(room_id)
        room = _deserialize_room(self.client.get(key))
        self.client.delete(key)
        return room

    def get_room(self, room_id: str):
        return _deserialize_room(self.client.get(self._k_room(room_id)))

    def set_room(self, room_id: str, room: dict[str, Any]):
        self.client.set(self._k_room(room_id), _serialize_room(room), ex=self.room_ttl)

    def get_user_room(self, user_id: int):
        value = self.client.hget(self._k('user_room'), int(user_id))
        if value is None:
            return None
        return str(value)

    def set_user_room(self, user_id: int, room_id: str):
        self.client.hset(self._k('user_room'), int(user_id), room_id)

    def pop_user_room(self, user_id: int):
        key = self._k('user_room')
        uid = int(user_id)
        value = self.client.hget(key, uid)
        self.client.hdel(key, uid)
        return str(value) if value is not None else None

    def get_user_queue_bet(self, user_id: int):
        value = self.client.hget(self._k('user_queue_bet'), int(user_id))
        if value is None:
            return None
        try:
            return int(value)
        except Exception:
            return None

    def set_user_queue_bet(self, user_id: int, bet: int):
        self.client.hset(self._k('user_queue_bet'), int(user_id), int(bet))

    def pop_user_queue_bet(self, user_id: int):
        key = self._k('user_queue_bet')
        uid = int(user_id)
        value = self.client.hget(key, uid)
        self.client.hdel(key, uid)
        if value is None:
            return None
        try:
            return int(value)
        except Exception:
            return None

    def queue_push(self, bet: int, user_id: int):
        self.client.rpush(self._k(f'queue:{int(bet)}'), int(user_id))

    def queue_pop(self, bet: int):
        value = self.client.lpop(self._k(f'queue:{int(bet)}'))
        if value is None:
            return None
        try:
            return int(value)
        except Exception:
            return None

    def queue_remove(self, bet: int, user_id: int):
        self.client.lrem(self._k(f'queue:{int(bet)}'), 0, int(user_id))

    def acquire_lock(self, name: str, timeout: int, blocking_timeout: int):
        return self.client.lock(self._k(f'lock:{name}'), timeout=timeout, blocking_timeout=blocking_timeout)


_local_state = InMemoryGameState()
_local_lock = Lock()


def init_game_state(app):
    backend = (app.config.get('GAME_STATE_BACKEND') or 'memory').strip().lower()
    if backend == 'redis':
        redis_url = app.config.get('REDIS_URL')
        prefix = app.config.get('GAME_STATE_PREFIX') or 'retroquest:game'
        room_ttl = int(app.config.get('GAME_ROOM_TTL_SECONDS') or 7200)
        try:
            if redis is None:
                raise RuntimeError('redis package is not installed')
            client = app.extensions.get('redis_client')
            if client is None:
                client = redis.Redis.from_url(redis_url, decode_responses=True)
            client.ping()
            app.extensions['game_state'] = RedisGameState(client, prefix=prefix, room_ttl=room_ttl)
            app.logger.info('Game state backend: redis')
            return
        except Exception as exc:
            app.logger.warning(f'Failed to initialize redis game state, falling back to memory: {exc}')
    app.extensions['game_state'] = InMemoryGameState()
    app.logger.info('Game state backend: memory')


def get_game_state():
    if has_app_context():
        state = current_app.extensions.get('game_state')
        if state is not None:
            return state
    return _local_state


@contextmanager
def game_state_lock():
    state = get_game_state()
    lock_name = 'state'
    timeout = 10
    blocking_timeout = 5
    if has_app_context():
        timeout = int(current_app.config.get('GAME_STATE_LOCK_TIMEOUT') or timeout)
        blocking_timeout = int(current_app.config.get('GAME_STATE_LOCK_BLOCKING_TIMEOUT') or blocking_timeout)

    distributed_lock = None
    try:
        distributed_lock = state.acquire_lock(lock_name, timeout=timeout, blocking_timeout=blocking_timeout)
    except Exception:
        distributed_lock = None

    if distributed_lock is not None:
        acquired = distributed_lock.acquire(blocking=True)
        if not acquired:
            raise RuntimeError('Could not acquire distributed game-state lock')
        try:
            yield
        finally:
            try:
                distributed_lock.release()
            except Exception:
                pass
    else:
        _local_lock.acquire()
        try:
            yield
        finally:
            _local_lock.release()
