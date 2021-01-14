import random

from cogs import HttpPlugin
from decorate import with_typing
from discord.ext import commands


def setup(bot):
    bot.add_cog(Giphy(bot))


class Giphy(HttpPlugin):

    PLUGIN_NAME = __name__
    GIPHY_URL = "http://api.giphy.com/v1/gifs/search?q={}&api_key={}&limit={}"
    SEARCH_LIMIT = 5

    @staticmethod
    def parse_url(url):
        index = url.find("?cid=")
        return url[:index]

    @with_typing
    @commands.has_permissions(send_messages=True)
    @commands.command(
        name="giphy",
        brief="Grabs a random Giphy image",
        description=("Grabs a random Giphy image based on your search."),
        usage="[search-terms]",
        help="\nLimitations: Mentions should not be used.",
    )
    async def giphy(self, ctx, *args):
        if not args:
            await self.bot.h.tagged_response(ctx, "I can't search for nothing!")
            return

        args_ = " ".join(args)
        args_q = "+".join(args)
        response = await self.http_call(
            "get",
            self.GIPHY_URL.format(args_q, self.config.dev_key, self.SEARCH_LIMIT),
        )
        data = response.get("data")

        if not data:
            args_f = f"*{args_}*"
            await self.bot.h.tagged_response(
                ctx, f"No search results found for: {args_f}"
            )
            return

        embeds = []
        for item in data:
            url = item.get("images", {}).get("original", {}).get("url")
            url = self.parse_url(url)
            embeds.append(url)

        self.bot.h.task_paginate(ctx, embeds, restrict=True)
