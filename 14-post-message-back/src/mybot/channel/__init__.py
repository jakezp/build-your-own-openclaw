"""Channel implementations for different platforms."""

from mybot.channel.base import Channel
from mybot.channel.telegram_channel import TelegramChannel
from mybot.channel.discord_channel import DiscordChannel

__all__ = ["Channel", "TelegramChannel", "DiscordChannel"]
