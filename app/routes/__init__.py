"""
Routes Package
All Flask blueprints for the RetroQuest Platform
"""
from app.blueprints import get_blueprints

_flat_blueprints = {
    blueprint.name: blueprint
    for domain_blueprints in get_blueprints().values()
    for blueprint, _prefix in domain_blueprints
}

globals().update(_flat_blueprints)
__all__ = sorted(_flat_blueprints.keys())
