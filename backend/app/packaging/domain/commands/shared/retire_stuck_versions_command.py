from app.shared.adapters.message_bus import command_bus


class RetireStuckVersionsCommand(command_bus.Command):
    """Retire the component and recipe versions stuck in UPDATING that Image Builder never created (hourly)."""
