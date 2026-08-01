"""DOS2 Forge — a toolkit for Divinity: Original Sin 2 game data.

Library-first: everything the CLI does is available as importable,
reusable modules.

    from dos2forge.pak import PakReader
    from dos2forge.parsers import parse_resource

    with PakReader("Shared.pak") as pak:
        doc = parse_resource(pak.read("Public/Shared/RootTemplates/..."))
"""

from .game import Game, GameNotFoundError, LoadIssue, RootTemplate
from .locate import find_data_dir, find_game

__version__ = "0.1.0"

__all__ = [
    "Game",
    "GameNotFoundError",
    "LoadIssue",
    "RootTemplate",
    "find_data_dir",
    "find_game",
    "__version__",
]
