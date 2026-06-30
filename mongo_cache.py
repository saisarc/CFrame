import asyncio
from typing import Any, Dict, List, Optional


class MongoStateCache:
    """A tiny in-memory cache to avoid excessive Mongo writes.

    We keep the authoritative in-memory object for each guild and flush to Mongo
    on explicit updates.
    """

    def __init__(self):
        self._guild_settings: Dict[str, Dict[str, Any]] = {}
        self._warnings: Dict[str, Dict[str, Any]] = {}
        self._giveaways: Dict[str, List[Dict[str, Any]]] = {}
        self._reaction_roles: Dict[str, Dict[str, Any]] = {}

    def get_guild_settings(self, gid: str) -> Optional[Dict[str, Any]]:
        return self._guild_settings.get(gid)

    def set_guild_settings(self, gid: str, data: Dict[str, Any]) -> None:
        self._guild_settings[gid] = data

    def get_warnings(self, gid: str) -> Optional[Dict[str, Any]]:
        return self._warnings.get(gid)

    def set_warnings(self, gid: str, data: Dict[str, Any]) -> None:
        self._warnings[gid] = data

    def get_giveaways(self, gid: str) -> Optional[List[Dict[str, Any]]]:
        return self._giveaways.get(gid)

    def set_giveaways(self, gid: str, data: List[Dict[str, Any]]) -> None:
        self._giveaways[gid] = data

    def get_reaction_roles(self, gid: str) -> Optional[Dict[str, Any]]:
        return self._reaction_roles.get(gid)

    def set_reaction_roles(self, gid: str, data: Dict[str, Any]) -> None:
        self._reaction_roles[gid] = data

