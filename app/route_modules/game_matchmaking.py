"""
Real-time Emperor's Circle matchmaking and room routes.
"""
from __future__ import annotations

import time
import uuid

from flask import jsonify, request
from flask_login import current_user, login_required

from app.extensions import db
from app.models import User
from app.services.game_wallet_service import GameWalletService
from app.validators import ValidationError


def register_game_matchmaking_routes(
    game_bp,
    *,
    allowed_bets,
    valid_cards,
    round_seconds,
    rematch_window_seconds,
    rematch_start_delay_seconds,
    game_state_lock,
    state_fn,
    get_room_for_user_fn,
    resolve_room_if_needed_fn,
    room_payload_fn,
    remove_from_queue_fn,
    other_player_fn,
    expire_rematch_if_needed_fn,
    save_room_fn,
    cleanup_room_fn,
):
    @game_bp.route('/join-queue', methods=['POST'])
    @login_required
    def join_queue():
        """Join exact-stake queue. Match pays both bets immediately."""
        amount = request.form.get('bet', type=int)
        if amount not in allowed_bets:
            return jsonify({'success': False, 'message': 'Invalid bet amount'}), 400

        with game_state_lock():
            user_id = current_user.id
            user = User.query.get(user_id)
            if not user:
                return jsonify({'success': False, 'message': 'User not found'}), 404

            room_id, room = get_room_for_user_fn(user_id)
            if room_id and room:
                resolve_room_if_needed_fn(room)
                payload = room_payload_fn(user_id, room)
                return jsonify({'success': True, **payload})

            remove_from_queue_fn(user_id)

            if user.coins < amount:
                return jsonify({'success': False, 'message': 'Not enough balance for this stake'}), 400

            while True:
                opp_id = state_fn().queue_pop(amount)
                if opp_id is None:
                    break
                state_fn().pop_user_queue_bet(opp_id)
                if opp_id == user_id:
                    continue
                if get_room_for_user_fn(opp_id)[0]:
                    continue

                opponent = User.query.get(opp_id)
                if not opponent or opponent.coins < amount:
                    continue

                new_room_id = uuid.uuid4().hex[:12]
                try:
                    GameWalletService.debit_match_stakes(
                        user_ids=[user_id, opp_id],
                        amount=amount,
                        room_id=new_room_id,
                        commit=True,
                    )
                except ValidationError:
                    db.session.rollback()
                    continue

                now = time.time()
                new_room = {
                    'id': new_room_id,
                    'players': [user_id, opp_id],
                    'bet': amount,
                    'pot': amount * 2,
                    'round': 1,
                    'selections': {},
                    'deadline': now + round_seconds,
                    'result': None,
                    'status': 'active',
                    'rematch_requests': set(),
                    'rematch_started_at': None,
                    'rematch_expired_at': None,
                    'created_at': int(now),
                    'last_seen': {user_id: now, opp_id: now},
                }
                state_fn().set_room(new_room_id, new_room)
                state_fn().set_user_room(user_id, new_room_id)
                state_fn().set_user_room(opp_id, new_room_id)

                payload = room_payload_fn(user_id, new_room)
                return jsonify({'success': True, **payload})

            state_fn().queue_push(amount, user_id)
            state_fn().set_user_queue_bet(user_id, amount)
            return jsonify({'success': True, 'status': 'waiting', 'bet': amount})

    @game_bp.route('/queue-status')
    @login_required
    def queue_status():
        """Poll queue and matchmaking state."""
        with game_state_lock():
            user_id = current_user.id
            room_id, room = get_room_for_user_fn(user_id)
            if room_id and room:
                resolve_room_if_needed_fn(room)
                expire_rematch_if_needed_fn(room)
                payload = room_payload_fn(user_id, room)
                if payload['status'] == 'opponent_left':
                    cleanup_room_fn(room_id)
                return jsonify({'success': True, **payload})

            queued_bet = state_fn().get_user_queue_bet(user_id)
            if queued_bet is not None:
                return jsonify({'success': True, 'status': 'waiting', 'bet': queued_bet})

            return jsonify({'success': True, 'status': 'idle'})

    @game_bp.route('/round-status')
    @login_required
    def round_status():
        """Poll in-room game state and resolve timeout when needed."""
        with game_state_lock():
            user_id = current_user.id
            room_id, room = get_room_for_user_fn(user_id)
            if not room_id or not room:
                return jsonify({'success': True, 'status': 'idle'})

            resolve_room_if_needed_fn(room)
            expire_rematch_if_needed_fn(room)
            payload = room_payload_fn(user_id, room)
            if payload['status'] == 'opponent_left':
                cleanup_room_fn(room_id)
            return jsonify({'success': True, **payload})

    @game_bp.route('/select-card', methods=['POST'])
    @login_required
    def select_card():
        """Submit chosen card for the active room."""
        card = (request.form.get('card') or '').strip().lower()
        if card not in valid_cards:
            return jsonify({'success': False, 'message': 'Invalid card'}), 400

        with game_state_lock():
            user_id = current_user.id
            room_id, room = get_room_for_user_fn(user_id)
            if not room_id or not room:
                return jsonify({'success': False, 'message': 'No active room'}), 400
            if room.get('status') != 'active':
                payload = room_payload_fn(user_id, room)
                return jsonify({'success': True, **payload})

            if user_id in room['selections']:
                return jsonify({'success': False, 'message': 'You already selected a card. Cannot change it.'}), 400

            room['selections'][user_id] = card
            save_room_fn(room)
            resolve_room_if_needed_fn(room)
            payload = room_payload_fn(user_id, room)
            return jsonify({'success': True, **payload})

    @game_bp.route('/rematch', methods=['POST'])
    @login_required
    def rematch():
        """Request rematch; start next round when both agree and can pay."""
        with game_state_lock():
            user_id = current_user.id
            room_id, room = get_room_for_user_fn(user_id)
            if not room_id or not room:
                return jsonify({'success': False, 'message': 'No active room'}), 400
            if room.get('status') == 'terminated':
                return jsonify({'success': False, 'message': 'Room already closed'}), 400

            if user_id in room.get('rematch_requests', set()):
                payload = room_payload_fn(user_id, room)
                return jsonify({'success': True, **payload})

            if not room.get('result'):
                return jsonify({'success': False, 'message': 'Round not finished yet'}), 400

            user = User.query.get(user_id)
            if not user or user.coins < room['bet']:
                return jsonify({'success': False, 'message': 'Insufficient balance for rematch'}), 400

            expire_rematch_if_needed_fn(room)
            now = time.time()
            if not room.get('rematch_requests'):
                room['rematch_started_at'] = now
                room['rematch_expired_at'] = None

            room['rematch_requests'].add(user_id)
            save_room_fn(room)
            seconds_left = max(0, rematch_window_seconds - int(time.time() - room['rematch_started_at']))
            if len(room['rematch_requests']) < 2:
                return jsonify({'success': True, 'status': 'waiting_rematch', 'rematch_seconds_left': seconds_left})

            if time.time() - room['rematch_started_at'] > rematch_window_seconds:
                room['rematch_requests'] = set()
                room['rematch_started_at'] = None
                room['rematch_expired_at'] = int(time.time())
                save_room_fn(room)
                return jsonify({
                    'success': True,
                    'status': 'rematch_expired',
                    'message': 'Rematch expired. Both players must press Rematch within 60 seconds.'
                })

            room['rematch_confirmed_at'] = time.time()
            save_room_fn(room)
            payload = room_payload_fn(user_id, room)
            return jsonify({'success': True, **payload})

    @game_bp.route('/leave-queue', methods=['POST'])
    @login_required
    def leave_queue():
        """Leave queue or active room."""
        with game_state_lock():
            user_id = current_user.id
            remove_from_queue_fn(user_id)

            room_id, room = get_room_for_user_fn(user_id)
            if not room_id or not room:
                return jsonify({'success': True})

            opp_id = other_player_fn(room, user_id)
            if room.get('status') == 'active' and not room.get('result'):
                try:
                    GameWalletService.refund_match_stakes(
                        user_ids=list(room['players']),
                        amount=room['bet'],
                        room_id=room.get('id'),
                        reason='leave_refund',
                        commit=True,
                    )
                except ValidationError:
                    db.session.rollback()

                room['status'] = 'terminated'
                room['termination_message'] = 'Opponent left the game.'
                state_fn().pop_user_room(user_id)
                if state_fn().get_user_room(opp_id) != room_id:
                    cleanup_room_fn(room_id)
                else:
                    save_room_fn(room)
                return jsonify({'success': True})

            room['status'] = 'terminated'
            room['termination_message'] = 'Opponent left the game.'
            state_fn().pop_user_room(user_id)
            if state_fn().get_user_room(opp_id) != room_id:
                cleanup_room_fn(room_id)
            else:
                save_room_fn(room)
            return jsonify({'success': True})

    @game_bp.route('/game-state')
    @login_required
    def game_state():
        """Get current game state for polling."""
        with game_state_lock():
            user_id = current_user.id
            user = User.query.get(user_id)

            queued_bet = state_fn().get_user_queue_bet(user_id)
            if queued_bet is not None:
                return jsonify({
                    'success': True,
                    'status': 'waiting',
                    'bet': queued_bet,
                    'balance': user.coins if user else 0
                })

            room_id, room = get_room_for_user_fn(user_id)
            if room_id and room:
                resolve_room_if_needed_fn(room)
                expire_rematch_if_needed_fn(room)
                payload = room_payload_fn(user_id, room)
                payload['balance'] = user.coins if user else 0
                if payload['status'] == 'opponent_left':
                    cleanup_room_fn(room_id)
                return jsonify({'success': True, **payload})

            return jsonify({
                'success': True,
                'status': 'idle',
                'balance': user.coins if user else 0
            })

    @game_bp.route('/respond-rematch', methods=['POST'])
    @login_required
    def respond_rematch():
        """Respond to rematch offer."""
        accept = request.form.get('accept', 'false').lower() == 'true'

        with game_state_lock():
            user_id = current_user.id
            room_id, room = get_room_for_user_fn(user_id)
            if not room_id or not room:
                return jsonify({'success': False, 'message': 'No active room'}), 400
            if not room.get('result'):
                return jsonify({'success': False, 'message': 'Round not finished'}), 400

            user = User.query.get(user_id)
            if not user:
                return jsonify({'success': False, 'message': 'User not found'}), 400

            if not accept:
                room['rematch_requests'] = set()
                room['rematch_started_at'] = None
                save_room_fn(room)
                payload = room_payload_fn(user_id, room)
                return jsonify({'success': True, **payload})

            if user.coins < room['bet']:
                return jsonify({'success': False, 'message': 'Insufficient balance for rematch'}), 400

            expire_rematch_if_needed_fn(room)
            if not room.get('rematch_requests'):
                room['rematch_started_at'] = time.time()

            room['rematch_requests'].add(user_id)
            if len(room['rematch_requests']) >= 2:
                room['rematch_confirmed_at'] = time.time()

            save_room_fn(room)
            payload = room_payload_fn(user_id, room)
            return jsonify({'success': True, **payload})

    @game_bp.route('/leave-game', methods=['POST'])
    @login_required
    def leave_game():
        """Leave current game and return to lobby."""
        return leave_queue()

    @game_bp.route('/new-match-same-opponent', methods=['POST'])
    @login_required
    def new_match_same_opponent():
        """Start a new match with the same opponent."""
        with game_state_lock():
            user_id = current_user.id
            user = User.query.get(user_id)
            if not user:
                return jsonify({'success': False, 'message': 'User not found'}), 404

            room_id, room = get_room_for_user_fn(user_id)
            if not room_id or not room:
                return jsonify({'success': False, 'message': 'No active room'}), 400

            opp_id = other_player_fn(room, user_id)
            opponent = User.query.get(opp_id)
            if not opponent:
                return jsonify({'success': False, 'message': 'Opponent not found'}), 404

            bet_amount = room['bet']
            if user.coins < bet_amount:
                return jsonify({'success': False, 'message': 'Insufficient balance'}), 400
            if opponent.coins < bet_amount:
                return jsonify({'success': False, 'message': 'Opponent has insufficient balance'}), 400

            room['status'] = 'terminated'
            room['termination_message'] = 'Starting new match.'
            state_fn().pop_user_room(user_id)
            state_fn().pop_user_room(opp_id)
            cleanup_room_fn(room_id)

            try:
                GameWalletService.debit_match_stakes(
                    user_ids=[user_id, opp_id],
                    amount=bet_amount,
                    room_id=room.get('id'),
                    commit=True,
                )
            except ValidationError:
                db.session.rollback()
                return jsonify({'success': False, 'message': 'Insufficient balance'}), 400

            new_room_id = uuid.uuid4().hex[:12]
            now = time.time()
            new_room = {
                'id': new_room_id,
                'players': [user_id, opp_id],
                'bet': bet_amount,
                'pot': bet_amount * 2,
                'round': 1,
                'selections': {},
                'deadline': now + round_seconds,
                'result': None,
                'status': 'active',
                'rematch_requests': set(),
                'rematch_started_at': None,
                'rematch_expired_at': None,
                'created_at': int(now),
                'last_seen': {user_id: now, opp_id: now}
            }
            state_fn().set_room(new_room_id, new_room)
            state_fn().set_user_room(user_id, new_room_id)
            state_fn().set_user_room(opp_id, new_room_id)

            payload = room_payload_fn(user_id, new_room)
            return jsonify({'success': True, **payload})
