import asyncio
import time
from typing import Any, Dict, List

from mongo_cache import MongoStateCache
from mongo_store import (
    init_mongo,
    get_guild_settings as mongo_get_guild_settings,
    upsert_guild_settings as mongo_upsert_guild_settings,
    get_warnings as mongo_get_warnings,
    set_warnings as mongo_set_warnings,
    get_giveaways as mongo_get_giveaways,
    set_giveaways as mongo_set_giveaways,
    get_reaction_roles as mongo_get_reaction_roles,
    set_reaction_roles as mongo_set_reaction_roles,
)


class MongoPersistence:
    """High-level persistence wrapper for features.py state."""

    def __init__(self):
        self.cache = MongoStateCache()
        self._lock = asyncio.Lock()

    async def startup(self) -> None:
        await init_mongo()

    async def load_guild(self, guild_id: int) -> Dict[str, Any]:
        gid = str(guild_id)

        async with self._lock:
            if self.cache.get_guild_settings(gid) is not None:
                return {
                    "guild_settings": self.cache.get_guild_settings(gid) or {},
                    "warnings": self.cache.get_warnings(gid) or {},
                    "giveaways": self.cache.get_giveaways(gid) or [],
                    "reaction_roles": self.cache.get_reaction_roles(gid) or {},
                }

            settings = await mongo_get_guild_settings(guild_id)
            warnings = await mongo_get_warnings(guild_id)
            giveaways = await mongo_get_giveaways(guild_id)
            reaction_roles = await mongo_get_reaction_roles(guild_id)

            self.cache.set_guild_settings(gid, settings or {})
            self.cache.set_warnings(gid, warnings or {})
            self.cache.set_giveaways(gid, giveaways or [])
            self.cache.set_reaction_roles(gid, reaction_roles or {})

        return {
            "guild_settings": settings or {},
            "warnings": warnings or {},
            "giveaways": giveaways or [],
            "reaction_roles": reaction_roles or {},
        }

    # ---- guild settings ----
    async def get_guild_settings(self, guild_id: int) -> Dict[str, Any]:
        gid = str(guild_id)
        await self.load_guild(guild_id)
        return self.cache.get_guild_settings(gid) or {}

    async def set_guild_settings(self, guild_id: int, settings: Dict[str, Any]) -> None:
        gid = str(guild_id)
        self.cache.set_guild_settings(gid, settings)
        await mongo_upsert_guild_settings(guild_id, settings)

    # ---- warnings ----
    async def get_warnings(self, guild_id: int) -> Dict[str, Any]:
        gid = str(guild_id)
        await self.load_guild(guild_id)
        return self.cache.get_warnings(gid) or {}

    async def set_warnings(self, guild_id: int, warnings_obj: Dict[str, Any]) -> None:
        gid = str(guild_id)
        self.cache.set_warnings(gid, warnings_obj)
        await mongo_set_warnings(guild_id, warnings_obj)

    # ---- giveaways ----
    async def get_giveaways(self, guild_id: int) -> List[Dict[str, Any]]:
        gid = str(guild_id)
        await self.load_guild(guild_id)
        return self.cache.get_giveaways(gid) or []

    async def set_giveaways(self, guild_id: int, giveaways: List[Dict[str, Any]]) -> None:
        gid = str(guild_id)
        self.cache.set_giveaways(gid, giveaways)
        await mongo_set_giveaways(guild_id, giveaways)

    # ---- reaction roles ----
    async def get_reaction_roles(self, guild_id: int) -> Dict[str, Any]:
        gid = str(guild_id)
        await self.load_guild(guild_id)
        return self.cache.get_reaction_roles(gid) or {}

    async def set_reaction_roles(self, guild_id: int, reaction_roles: Dict[str, Any]) -> None:
        gid = str(guild_id)
        self.cache.set_reaction_roles(gid, reaction_roles)
        await set_reaction_roles(guild_id, reaction_roles)

