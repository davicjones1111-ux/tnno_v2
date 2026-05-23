"""
Services Package
Business logic layer for the RetroQuest Platform
"""
from app.services.mission_service import MissionService
from app.services.deposit_service import DepositService
from app.services.user_service import UserService
from app.services.history_service import HistoryService

try:
    from app.services.blockchain_service import BlockchainService, BlockchainChecker
except Exception:  # pragma: no cover - optional dependency during local dev
    BlockchainService = None
    BlockchainChecker = None

__all__ = [
    'BlockchainService',
    'BlockchainChecker',
    'MissionService',
    'DepositService',
    'UserService',
    'HistoryService'
]
