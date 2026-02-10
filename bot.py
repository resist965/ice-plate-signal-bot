"""ice-plate-signal-bot — Signal bot for license plate lookups."""

import asyncio
import atexit
import logging
import os

from signalbot import SignalBot

from commands import HelpCommand, PlateCommand, PlateDetailCommand, VoicePlateCommand
from lookup import close_session


def main() -> None:
    signal_service = os.environ.get("SIGNAL_SERVICE", "localhost:8080")
    phone_number = os.environ.get("PHONE_NUMBER", "")
    signal_group = os.environ.get("SIGNAL_GROUP", "")
    debug = os.environ.get("DEBUG", "false").lower() == "true"

    if not phone_number:
        raise SystemExit("PHONE_NUMBER environment variable is required")
    if not signal_group:
        raise SystemExit("SIGNAL_GROUP environment variable is required")

    has_decrypt_key = bool(os.environ.get("DEFROST_DECRYPT_KEY"))
    has_json_url = bool(os.environ.get("DEFROST_JSON_URL"))
    if not has_decrypt_key and not has_json_url:
        logging.warning(
            "Neither DEFROST_DECRYPT_KEY nor DEFROST_JSON_URL set "
            "— all defrostmn.net lookups will be disabled"
        )
    elif not has_decrypt_key:
        logging.warning("DEFROST_DECRYPT_KEY not set — only stopice snapshot lookups active")

    if debug:
        from signalbot import enable_console_logging

        enable_console_logging(logging.INFO)
    else:
        logging.disable(logging.CRITICAL)

    config = {
        "signal_service": signal_service,
        "phone_number": phone_number,
        "storage": {"type": "in-memory"},
    }

    bot = SignalBot(config)

    groups = [signal_group]
    plate_cmd = PlateCommand()
    detail_cmd = PlateDetailCommand()
    voice_cmd = VoicePlateCommand()

    bot.register(plate_cmd, contacts=False, groups=groups)
    bot.register(detail_cmd, contacts=False, groups=groups)
    bot.register(voice_cmd, contacts=False, groups=groups)

    detail_cmd.set_plate_command(plate_cmd)
    voice_cmd.set_plate_command(plate_cmd)
    bot.register(HelpCommand(), contacts=False, groups=groups)

    atexit.register(lambda: asyncio.get_event_loop().run_until_complete(close_session()))
    bot.start()


if __name__ == "__main__":
    main()
