"""Module for plugin loading.
"""

import logging
from os.path import dirname, basename, isfile, join
import importlib
import glob

class PluginLoader():
    """Handles plugin loading.

    parameters:
        bot (discord.ext.commands.Bot): the bot object to which plugins are loading
    """

    def __init__(self, bot):
        self.bot = bot

    def load_plugins(self):
        """Adds functions as commands from the plugins directory.
        """

        wildcard = f"{join(dirname(__file__))}/plugins/*.py"
        files = glob.glob(wildcard)
        module_names = [ 
            f"plugins.{basename(f)[:-3]}" for f in files if isfile(f) and not f.endswith('__init__.py')
        ]
        for module in module_names:
            imported = importlib.import_module(module)
            for name, func in imported.__dict__.items():
                if not name.startswith("__") and name != "commands":
                    logging.info(f"Loading command `{name}` from module {module}")
                    self.bot.add_command(func)