import os
import asyncio
from typing import Any, Dict, Optional, List

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase


_MONGO_URI = os.getenv("MONGODB_URI")
_MONGO_DB_NAME = os.getenv("MONGODB_DB", "cframe")

_client: Optional[AsyncIOMotorClient] = None
_db: Optional[AsyncIOMotorDatabase] = None


async def init_mongo() -> None:
    """Initialize the global mongo client.

    Safe to call multiple times.
    """
    global _client, _db
    if _client is not None and _db is not None:
        return

    if not _MONGO_URI:
        raise RuntimeError(
            "MONGODB_URI is not set. Add it to your environment variables (Atlas connection string)."
        )

    _client = AsyncIOMotorClient(
        _MONGO_URI,
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
        maxPoolSize=int(os.getenv("MONGODB_MAX_POOL_SIZE", "10")),
    )
    _db = _client[_MONGO_DB_NAME]

    # Trigger server selection early to fail fast
    await _db.command("ping")


def get_db() -> AsyncIOMotorDatabase:
    if _db is None:
        raise RuntimeError("Mongo not initialized. Call init_mongo() at startup.")
    return _db


async def get_guild_settings(guild_id: int) -> Dict[str, Any]:
    settings = get_db()["guild_settings"]
    doc = await settings.find_one({"guild_id": int(guild_id)})
    return doc.get("settings", {}) if doc else {}


async def upsert_guild_settings(guild_id: int, patch: Dict[str, Any]) -> None:
    settings = get_db()["guild_settings"]
    await settings.update_one(
        {"guild_id": int(guild_id)},
        {"$set": {"settings": patch, "updated_at": asyncio.get_event_loop().time()}},
        upsert=True,
    )


async def get_warnings(guild_id: int) -> Dict[str, Any]:
    coll = get_db()["warnings"]
    doc = await coll.find_one({"guild_id": int(guild_id)})
    return doc.get("warnings", {}) if doc else {}


async def set_warnings(guild_id: int, warnings_obj: Dict[str, Any]) -> None:
    coll = get_db()["warnings"]
    await coll.update_one(
        {"guild_id": int(guild_id)},
        {"$set": {"warnings": warnings_obj, "updated_at": asyncio.get_event_loop().time()}},
        upsert=True,
    )


async def get_giveaways(guild_id: int) -> List[Dict[str, Any]]:
    coll = get_db()["giveaways"]
    doc = await coll.find_one({"guild_id": int(guild_id)})
    return doc.get("giveaways", []) if doc else []


async def set_giveaways(guild_id: int, giveaways: List[Dict[str, Any]]) -> None:
    coll = get_db()["giveaways"]
    await coll.update_one(
        {"guild_id": int(guild_id)},
        {"$set": {"giveaways": giveaways, "updated_at": asyncio.get_event_loop().time()}},
        upsert=True,
    )


async def get_reaction_roles(guild_id: int) -> Dict[str, Any]:
    coll = get_db()["reaction_roles"]
    doc = await coll.find_one({"guild_id": int(guild_id)})
    return doc.get("reaction_roles", {}) if doc else {}


async def set_reaction_roles(guild_id: int, reaction_roles: Dict[str, Any]) -> None:
    coll = get_db()["reaction_roles"]
    await coll.update_one(
        {"guild_id": int(guild_id)},
        {"$set": {"reaction_roles": reaction_roles, "updated_at": asyncio.get_event_loop().time()}},
        upsert=True,
    )

