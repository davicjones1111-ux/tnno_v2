from app import create_app
from app.models import BlockchainState
from app.services.blockchain_service import BlockchainService

app = create_app('development')
with app.app_context():
    service = BlockchainService()
    current = service.get_current_block()
    if current:
        print(f'Current block: {current}')
        states = BlockchainState.query.all()
        for state in states:
            print(f'Before: {state.coin_type} last_block={state.last_block}')
            state.last_block = max(0, current - 1000)
            print(f'After: {state.coin_type} last_block={state.last_block}')
        app.db.session.commit()
        print('Reset complete')
    else:
        print('Could not get current block')