from signalbot import Command, Context, triggered


HELP_TEXT = (
    "/plate [LICENSE PLATE] - Check a plate against the ICE vehicle databases "
    "(stopice.net and defrostmn.net)\n"
    "/plate + image - Attach a photo of a license plate to read it automatically\n"
    "/help - Show this message"
)


class HelpCommand(Command):
    @triggered("/help")
    async def handle(self, c: Context) -> None:
        await c.send(HELP_TEXT)
