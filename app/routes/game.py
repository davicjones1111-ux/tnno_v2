"""
Game Routes
Game center with Emperor's Circle and other games
"""
import time
import uuid
from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user
from app.models import GameScore, User, EmperorMatchStat
from app.extensions import db, cache
from app.game_state import get_game_state, game_state_lock
from app.route_modules.game_matchmaking import register_game_matchmaking_routes
from app.services.game_wallet_service import GameWalletService
from app.validators import ValidationError

game_bp = Blueprint('game', __name__)

ALLOWED_BETS = {1000, 5000, 10000, 30000, 50000, 100000, 500000, 1000000, 5000000, 10000000}
ROUND_SECONDS = 240
REMATCH_WINDOW_SECONDS = 60
REMATCH_START_DELAY_SECONDS = 3
PLATFORM_FEE_BPS = 250  # 2.5% of stake, charged only to winner
VALID_CARDS = {'king', 'people', 'slave'}

# track when each player last polled/acted; helps detect lost connections
INACTIVITY_TIMEOUT = 90  # seconds before we consider a player disconnected


def _state():
    return get_game_state()


def _save_room(room):
    if not room:
        return
    _state().set_room(room['id'], room)


def _cleanup_room(room_id):
    room = _state().pop_room(room_id)
    if not room:
        return
    for uid in room['players']:
        if _state().get_user_room(uid) == room_id:
            _state().pop_user_room(uid)


def _remove_from_queue(user_id):
    bet = _state().pop_user_queue_bet(user_id)
    if bet is not None:
        _state().queue_remove(bet, user_id)


def _get_room_for_user(user_id):
    room_id = _state().get_user_room(user_id)
    if not room_id:
        return None, None
    room = _state().get_room(room_id)
    if not room:
        _state().pop_user_room(user_id)
        return None, None
    return room_id, room


def _other_player(room, user_id):
    return room['players'][0] if room['players'][1] == user_id else room['players'][1]


def _compare_cards(card_a, card_b):
    if card_a == card_b:
        return 0
    beats = {'king': 'people', 'people': 'slave', 'slave': 'king'}
    return 1 if beats[card_a] == card_b else -1


def _get_or_create_match_stat(user_id):
    stat = EmperorMatchStat.query.filter_by(user_id=user_id).first()
    if not stat:
        stat = EmperorMatchStat(user_id=user_id, matches_played=0, matches_won=0, total_winnings=0)
        db.session.add(stat)
    return stat


def _record_match_stats(room, result):
    uid_a, uid_b = room['players']
    stat_a = _get_or_create_match_stat(uid_a)
    stat_b = _get_or_create_match_stat(uid_b)
    stat_a.matches_played += 1
    stat_b.matches_played += 1

    winner_id = result.get('winner_id')
    if winner_id == uid_a:
        stat_a.matches_won += 1
        stat_a.total_winnings += int(result.get('payout', 0))
    elif winner_id == uid_b:
        stat_b.matches_won += 1
        stat_b.total_winnings += int(result.get('payout', 0))


def _check_inactivity(room):
    """Terminate room if one player hasn\'t been seen for too long."""
    now = time.time()
    last = room.get('last_seen', {})
    for uid in room['players']:
        if now - last.get(uid, 0) > INACTIVITY_TIMEOUT:
            # mark other player as winner by abandonment, refund if before round
            other = _other_player(room, uid)
            if room.get('status') == 'active' and not room.get('result'):
                try:
                    GameWalletService.refund_match_stakes(
                        user_ids=list(room['players']),
                        amount=room['bet'],
                        room_id=room.get('id'),
                        reason='disconnect_refund',
                        commit=True,
                    )
                except ValidationError:
                    db.session.rollback()
            room['status'] = 'terminated'
            room['termination_message'] = 'Opponent disconnected.'
            _save_room(room)
            return True
    return False


def _resolve_room_if_needed(room):
    # Check if rematch is confirmed and delay has passed
    confirmed_at = room.get('rematch_confirmed_at')
    if confirmed_at:
        if time.time() - confirmed_at >= REMATCH_START_DELAY_SECONDS:
            # Start the rematch
            uid_a, uid_b = room['players']
            user_a = User.query.get(uid_a)
            user_b = User.query.get(uid_b)
            if not user_a or not user_b:
                room['status'] = 'terminated'
                room['termination_message'] = 'Player data missing. Room closed.'
                _save_room(room)
                return
            if user_a.coins < room['bet'] or user_b.coins < room['bet']:
                room['rematch_requests'] = set()
                room['rematch_started_at'] = None
                room['rematch_confirmed_at'] = None
                _save_room(room)
                return  # Insufficient balance, don't start
            try:
                GameWalletService.debit_match_stakes(
                    user_ids=[uid_a, uid_b],
                    amount=room['bet'],
                    room_id=room.get('id'),
                    commit=True,
                )
            except ValidationError:
                db.session.rollback()
                room['rematch_requests'] = set()
                room['rematch_started_at'] = None
                room['rematch_confirmed_at'] = None
                _save_room(room)
                return
            room['round'] += 1
            room['pot'] = room['bet'] * 2
            room['selections'] = {}
            room['deadline'] = time.time() + ROUND_SECONDS
            room['result'] = None
            room['status'] = 'active'
            room['rematch_requests'] = set()
            room['rematch_started_at'] = None
            room['rematch_expired_at'] = None
            room['rematch_confirmed_at'] = None
            _save_room(room)
        return  # Wait for delay or just started

    # check for inactivity first
    if _check_inactivity(room):
        return
    if room.get('result') or room.get('status') != 'active':
        return

    selections = room['selections']
    if len(selections) < 2 and time.time() < room['deadline']:
        return

    uid_a, uid_b = room['players']
    card_a = selections.get(uid_a)
    card_b = selections.get(uid_b)
    user_a = User.query.get(uid_a)
    user_b = User.query.get(uid_b)
    if not user_a or not user_b:
        room['status'] = 'terminated'
        room['termination_message'] = 'Game data invalid. Room closed.'
        _save_room(room)
        return

    if card_a and card_b:
        cmp_result = _compare_cards(card_a, card_b)
        if cmp_result == 0:
            GameWalletService.refund_match_stakes(
                user_ids=[uid_a, uid_b],
                amount=room['bet'],
                room_id=room.get('id'),
                reason='draw_refund',
            )
            result = {
                'kind': 'draw',
                'winner_id': None,
                'loser_id': None,
                'payout': room['bet'],  # Each player gets their bet back (1000)
                'platform_fee': 0,
                'cards': {uid_a: card_a, uid_b: card_b},
                'outcomes': {uid_a: 'draw', uid_b: 'draw'},
                'coin_changes': {uid_a: 0, uid_b: 0}
            }
        else:
            winner_id = uid_a if cmp_result > 0 else uid_b
            loser_id = uid_b if winner_id == uid_a else uid_a
            payout_data = GameWalletService.payout_winner(
                winner_id=winner_id,
                loser_id=loser_id,
                bet_amount=room['bet'],
                platform_fee_bps=PLATFORM_FEE_BPS,
                room_id=room.get('id'),
            )
            fee_amount = payout_data['fee_amount']
            winner_payout = payout_data['payout']
            winner_net_gain = payout_data['winner_net_gain']
            result = {
                'kind': 'win',
                'winner_id': winner_id,
                'loser_id': loser_id,
                'payout': winner_payout,  # Winner gets pot minus platform fee
                'platform_fee': fee_amount,
                'cards': {uid_a: card_a, uid_b: card_b},
                'outcomes': {winner_id: 'win', loser_id: 'lose'},
                'coin_changes': {winner_id: winner_net_gain, loser_id: -room['bet']}
            }
    else:
        # Selection timeout behavior: if both cards were not submitted in time,
        # treat the round as draw and refund each player's own bet.
        GameWalletService.refund_match_stakes(
            user_ids=[uid_a, uid_b],
            amount=room['bet'],
            room_id=room.get('id'),
            reason='timeout_refund',
        )
        result = {
            'kind': 'draw_timeout',
            'winner_id': None,
            'loser_id': None,
            'payout': room['bet'],
            'platform_fee': 0,
            'cards': {uid_a: None, uid_b: None},
            'outcomes': {uid_a: 'draw', uid_b: 'draw'},
            'coin_changes': {uid_a: 0, uid_b: 0}
        }

    _record_match_stats(room, result)
    db.session.commit()
    room['result'] = result
    room['status'] = 'finished'
    room['resolved_at'] = int(time.time())
    room['rematch_requests'] = set()
    room['rematch_started_at'] = None
    room['rematch_expired_at'] = None
    _save_room(room)


def _expire_rematch_if_needed(room):
    started = room.get('rematch_started_at')
    if not started or not room.get('rematch_requests'):
        return
    if time.time() - started > REMATCH_WINDOW_SECONDS:
        room['rematch_requests'] = set()
        room['rematch_started_at'] = None
        room['rematch_expired_at'] = int(time.time())
        _save_room(room)


def _room_payload(user_id, room):
    # update last-seen for heartbeat
    room.setdefault('last_seen', {})[user_id] = time.time()
    _save_room(room)
    opp_id = _other_player(room, user_id)
    opp_user = User.query.get(opp_id)
    me = User.query.get(user_id)

    payload = {
        'room_id': room['id'],
        'bet': room['bet'],
        'pot': room['pot'],
        'round': room['round'],
        'opponent_name': opp_user.username if opp_user else 'Unknown',
        'balance': me.coins if me else 0
    }

    if room.get('status') == 'terminated':
        payload.update({
            'status': 'opponent_left',
            'message': room.get('termination_message', 'Opponent left the game.')
        })
        return payload

    if room.get('rematch_confirmed_at'):
        elapsed = time.time() - room['rematch_confirmed_at']
        if elapsed < REMATCH_START_DELAY_SECONDS:
            payload.update({
                'status': 'waiting_rematch_start',
                'seconds_left': max(0, REMATCH_START_DELAY_SECONDS - elapsed),
                'message': 'Both players confirmed. Starting match in...'
            })
            return payload

    if room.get('result'):
        requests = room.get('rematch_requests') or set()
        if requests:
            started = room.get('rematch_started_at') or int(time.time())
            payload.update({
                'status': 'waiting_rematch',
                'rematch_seconds_left': max(0, REMATCH_WINDOW_SECONDS - int(time.time() - started)),
                'rematch_requested_by_you': user_id in requests,
                'rematch_ready_count': len(requests),
                'message': 'Waiting for both players to confirm rematch.'
            })
            return payload

        if room.get('rematch_expired_at'):
            payload.update({
                'status': 'rematch_expired',
                'message': 'Rematch expired. Both players must press Rematch within 60 seconds.'
            })
            return payload

        result = room['result']
        payload.update({
            'status': 'result',
            'outcome': result['outcomes'].get(user_id, 'draw'),
            'your_card': result['cards'].get(user_id),
            'opponent_card': result['cards'].get(opp_id),
            'winner_id': result.get('winner_id'),
            'payout': result.get('payout', 0),
            'coin_change': result.get('coin_changes', {}).get(user_id, 0),
            'platform_fee': result.get('platform_fee', 0),
            'result_kind': result.get('kind', 'win'),
            'resolved_at': room.get('resolved_at')
        })
        return payload

    payload.update({
        'status': 'matched',
        'time_left': max(0, int(room['deadline'] - time.time())),
        'your_selected': user_id in room['selections'],
        'opponent_selected': opp_id in room['selections']
    })
    return payload


@game_bp.route('/')

@login_required
def index():
    """Game center home"""
    # Get user's game scores
    user_scores = GameScore.query.filter_by(user_id=current_user.id)\
        .order_by(GameScore.score.desc()).all()
    
    # Top players by match wins
    top_players = EmperorMatchStat.query\
        .order_by(EmperorMatchStat.matches_won.desc(), EmperorMatchStat.total_winnings.desc())\
        .limit(10).all()
    
    return render_template('game/index.html',
                          user_scores=user_scores,
                          top_players=top_players)


@game_bp.route('/emperors-circle')

@login_required
def emperors_circle():
    """Emperor's Circle game"""
    # Get user's best score
    best_score = GameScore.query.filter_by(
        user_id=current_user.id,
        game_id='emperors_circle'
    ).first()
    
    # Top players by match wins and total winnings.
    top_players = EmperorMatchStat.query\
        .order_by(EmperorMatchStat.matches_won.desc(), EmperorMatchStat.total_winnings.desc())\
        .limit(10).all()
    
    return render_template('game/emperors_circle.html',
                         best_score=best_score,
                         top_players=top_players)




register_game_matchmaking_routes(
    game_bp,
    allowed_bets=ALLOWED_BETS,
    valid_cards=VALID_CARDS,
    round_seconds=ROUND_SECONDS,
    rematch_window_seconds=REMATCH_WINDOW_SECONDS,
    rematch_start_delay_seconds=REMATCH_START_DELAY_SECONDS,
    game_state_lock=game_state_lock,
    state_fn=_state,
    get_room_for_user_fn=_get_room_for_user,
    resolve_room_if_needed_fn=_resolve_room_if_needed,
    room_payload_fn=_room_payload,
    remove_from_queue_fn=_remove_from_queue,
    other_player_fn=_other_player,
    expire_rematch_if_needed_fn=_expire_rematch_if_needed,
    save_room_fn=_save_room,
    cleanup_room_fn=_cleanup_room,
)


@game_bp.route('/save-score', methods=['POST'])

@login_required
def save_score():
    """Save game score via AJAX"""
    from flask import jsonify
    from app.services import UserService
    
    score = request.form.get('score', 0, type=int)
    game_id = request.form.get('game_id', 'emperors_circle')
    
    if score <= 0:
        return jsonify({'success': False, 'message': 'Invalid score'}), 400
    
    game_score = UserService.save_game_score(current_user.id, score, game_id)
    
    return jsonify({
        'success': True,
        'message': 'Score saved!',
        'score': game_score.score
    })


@game_bp.route('/leaderboard')

@login_required
def leaderboard():
    """Game leaderboard page"""
    game_id = request.args.get('game_id', 'emperors_circle')
    limit = request.args.get('limit', 50, type=int)
    
    scores = GameScore.query.filter_by(game_id=game_id)\
        .order_by(GameScore.score.desc())\
        .limit(limit).all()
    
    # Calculate user's rank
    user_rank = None
    all_scores = GameScore.query.filter_by(game_id=game_id)\
        .order_by(GameScore.score.desc()).all()
    
    for rank, s in enumerate(all_scores, 1):
        if s.user_id == current_user.id:
            user_rank = rank
            break
    
    return render_template('game/leaderboard.html',
                         scores=scores,
                         game_id=game_id,
                         user_rank=user_rank)


