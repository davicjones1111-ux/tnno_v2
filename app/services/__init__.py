"""
Services package exports.

Keep imports lazy so routine web boot stays lightweight.
"""
from __future__ import annotations

from importlib import import_module


_SERVICE_EXPORTS = {
    'MissionService': ('app.services.mission_service', 'MissionService'),
    'DepositService': ('app.services.deposit_service', 'DepositService'),
    'UserService': ('app.services.user_service', 'UserService'),
    'HistoryService': ('app.services.history_service', 'HistoryService'),
}

__all__ = sorted(_SERVICE_EXPORTS.keys())


def __getattr__(name: str):
    if name not in _SERVICE_EXPORTS:
        raise AttributeError(f'module {__name__!r} has no attribute {name!r}')

    module_name, attr_name = _SERVICE_EXPORTS[name]
    module = import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
