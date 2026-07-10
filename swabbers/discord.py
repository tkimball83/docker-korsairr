import asyncio
import logging
from datetime import datetime, timedelta

import discord
from pydantic import Field, PositiveInt
from pydantic_settings import BaseSettings, SettingsConfigDict

from swabbers import common
from swabbers.common import format_duration, format_error

log = logging.getLogger("korsairr.discord")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="KORSAIRR_DISCORD_", str_strip_whitespace=True
    )

    delete_pinned: bool = False
    guild_id: PositiveInt
    retention_days: PositiveInt = 7
    token: str = Field(min_length=1)


def rate_limit_filter(record: logging.LogRecord) -> bool:
    if record.args and str(record.msg).endswith("Retrying in %.2f seconds."):
        record.msg = "🐢 Rate limited, retrying in %.2fs"
        record.args = (record.args[-1],)
    return True


class SwabClient(discord.Client):
    def __init__(self, settings: Settings, interval: int):
        intents = discord.Intents.none()
        intents.guilds = True
        super().__init__(intents=intents)
        self.interval = interval
        self.settings = settings

    async def setup_hook(self) -> None:
        self.swab_task = asyncio.create_task(self.swab_forever())

    async def swab_forever(self) -> None:
        await self.wait_until_ready()
        log.info("🤖 Logged in as %s\n", self.user)
        while True:
            try:
                await self.swab_guild()
            except discord.DiscordException as exc:
                log.error("❌ Swab pass failed: %s", exc)
            except Exception:
                log.exception("❌ Swab pass failed")
            log.info(
                "⏰ Swabbing again in about %s . . .\n",
                format_duration(self.interval),
            )
            await asyncio.sleep(self.interval)

    async def swab_channel(self, channel, cutoff: datetime) -> int:
        deleted = 0
        unarchived = False
        try:
            async for message in channel.history(limit=None, before=cutoff):
                if message.pinned and not self.settings.delete_pinned:
                    continue
                if not message.type.is_deletable():
                    continue
                if (
                    not unarchived
                    and isinstance(channel, discord.Thread)
                    and channel.archived
                ):
                    await channel.edit(archived=False)
                    unarchived = True
                try:
                    await message.delete()
                except discord.NotFound:
                    continue
                deleted += 1
                log.info(
                    "🗑️ Deleted message %d from #%s (author=%s, created=%s)",
                    message.id,
                    channel.name,
                    message.author,
                    message.created_at.isoformat(),
                )
        except discord.HTTPException as exc:
            log.warning("🚫 Skipping #%s: %s", channel.name, exc)
        finally:
            if unarchived:
                try:
                    await channel.edit(archived=True)
                except discord.HTTPException as exc:
                    log.warning("🚫 Failed to re-archive #%s: %s", channel.name, exc)
        return deleted

    async def swabbable_channels(self, guild: discord.Guild) -> list:
        candidates = [*guild.channels, *guild.threads]
        for channel in guild.channels:
            if not isinstance(channel, (discord.ForumChannel, discord.TextChannel)):
                continue
            try:
                async for thread in channel.archived_threads(limit=None):
                    candidates.append(thread)
                if (
                    isinstance(channel, discord.TextChannel)
                    and channel.permissions_for(guild.me).manage_threads
                ):
                    async for thread in channel.archived_threads(
                        limit=None, private=True
                    ):
                        candidates.append(thread)
            except discord.HTTPException as exc:
                log.warning(
                    "🚫 Skipping archived threads of #%s: %s", channel.name, exc
                )
        channels = []
        for channel in candidates:
            if not isinstance(channel, discord.abc.Messageable):
                continue
            permissions = channel.permissions_for(guild.me)
            archived = isinstance(channel, discord.Thread) and channel.archived
            if (
                not permissions.view_channel
                or not permissions.read_message_history
                or not permissions.manage_messages
                or (archived and not permissions.manage_threads)
            ):
                log.warning("🚫 Skipping #%s: missing permissions", channel.name)
                continue
            channels.append(channel)
        return channels

    async def swab_guild(self) -> None:
        guild = self.get_guild(self.settings.guild_id)
        if guild is None:
            log.error(
                "❌ Guild %d not found or bot is not a member", self.settings.guild_id
            )
            return
        cutoff = discord.utils.utcnow() - timedelta(days=self.settings.retention_days)
        channels = await self.swabbable_channels(guild)
        total = 0
        failures = 0
        for channel in channels:
            try:
                total += await self.swab_channel(channel, cutoff)
            except Exception as exc:
                failures += 1
                log.warning(
                    "🚫 Failed to swab #%s: %s", channel.name, format_error(exc)
                )
        if total:
            log.info("✅ Swabbed %d message(s)", total)
        if failures:
            log.warning("⚠️ %d channel(s) failed to swab", failures)
        elif not total:
            log.info("🤷 No messages matched the swab policy")


def run(settings: Settings, korsairr: common.Settings) -> None:
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("discord.client").setLevel(logging.ERROR)
    logging.getLogger("discord.http").addFilter(rate_limit_filter)

    log.info("🚀 Swabbing discord guild %s", settings.guild_id)
    log.info("   delete_pinned=%s", settings.delete_pinned)
    log.info("   retention=%dd\n", settings.retention_days)

    client = SwabClient(settings, korsairr.interval)

    try:
        client.run(settings.token, log_handler=None)
    except discord.LoginFailure as exc:
        log.error("❌ Login failed: %s", exc)
        raise
