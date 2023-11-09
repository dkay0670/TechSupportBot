"""Module for defining the data bot methods."""

import time
import urllib
from collections import deque
from json import JSONDecodeError
from urllib.parse import urlparse

import aiohttp
import expiringdict
import gino
import munch
from base import extension
from botlogging import LogLevel
from error import HTTPRateLimit


class DataBot(extension.ExtensionsBot):
    """Bot that supports Postgres."""

    def __init__(self, *args, **kwargs):
        self.db = None
        super().__init__(*args, **kwargs)
        self.http_cache = expiringdict.ExpiringDict(
            max_len=self.file_config.cache.http_cache_length,
            max_age_seconds=self.file_config.cache.http_cache_seconds,
        )
        self.url_rate_limit_history = {}
        # Rate limit configurations for each root URL
        # This is "URL": (calls, seconds)
        self.rate_limits = {
            "api.urbandictionary.com": (2, 60),
            "api.openai.com": (3, 60),
            "www.googleapis.com": (5, 60),
            "ipinfo.io": (1, 30),
            "api.open-notify.org": (1, 60),
            "geocode.xyz": (1, 60),
            "v2.jokeapi.dev": (10, 60),
            "api.kanye.rest": (1, 60),
            "newsapi.org": (1, 30),
            "accounts.spotify.com": (3, 60),
            "api.spotify.com": (3, 60),
            "api.mymemory.translated.net": (1, 60),
            "api.openweathermap.org": (3, 60),
            "api.wolframalpha.com": (3, 60),
            "xkcd.com": (5, 60),
            "api.github.com": (3, 60),
            "api.giphy.com": (3, 60),
            "strawpoll.com": (3, 60),
            "api.thecatapi.com": (10, 60),
        }
        # For the variable APIs, if they don't exist, don't rate limit them
        try:
            self.rate_limits[urlparse(self.file_config.api.api_url.dumpdbg).netloc] = (
                1,
                60,
            )
        except AttributeError:
            print("No dumpdbg API URL found. Not rate limiting dumpdbg")
        try:
            self.rate_limits[urlparse(self.file_config.api.api_url.linx).netloc] = (
                20,
                60,
            )
        except AttributeError:
            print("No linx API URL found. Not rate limiting linx")

    def generate_db_url(self):
        """Dynamically converts config to a Postgres url."""
        db_type = "postgres"

        try:
            config_child = getattr(self.file_config.database, db_type)

            user = config_child.user
            password = config_child.password

            name = getattr(config_child, "name")

            host = config_child.host
            port = config_child.port

        except AttributeError as exception:
            self.logger.console.warning(
                f"Could not generate DB URL for {db_type.upper()}: {exception}"
            )
            return None

        url = f"{db_type}://{user}:{password}@{host}:{port}"
        url_filtered = f"{db_type}://{user}:********@{host}:{port}"

        if name:
            url = f"{url}/{name}"

        # don't log the password
        self.logger.console.debug(f"Generated DB URL: {url_filtered}")

        return url

    async def get_postgres_ref(self):
        """Grabs the main DB reference.

        This doesn't follow a singleton pattern (use bot.db instead).
        """
        await self.logger.send_log(
            message="Obtaining and binding to Gino instance",
            level=LogLevel.DEBUG,
            console_only=True,
        )

        db_ref = gino.Gino()
        db_url = self.generate_db_url()
        await db_ref.set_bind(db_url)

        db_ref.Model.__table_args__ = {"extend_existing": True}

        return db_ref

    async def http_call(self, method, url, *args, **kwargs):
        """Makes an HTTP request.

        By default this returns JSON/dict with the status code injected.

        parameters:
            method (str): the HTTP method to use
            url (str): the URL to call
            use_cache (bool): True if the GET result should be grabbed from cache
            get_raw_response (bool): True if the actual response object should be returned
        """
        # Get the URL not the endpoint being called
        ignore_rate_limit = False
        root_url = urlparse(url).netloc

        # If the URL is not rate limited, we assume it can be executed an unlimited amount of times
        if root_url in self.rate_limits:
            executions_allowed, time_window = self.rate_limits[root_url]

            now = time.time()

            # If the URL being called is not in the history, add it
            # A deque allows easy max limit length
            if root_url not in self.url_rate_limit_history:
                self.url_rate_limit_history[root_url] = deque(
                    [], maxlen=executions_allowed
                )

            # Determine which calls, if any, have to be removed because they are out of the time
            while (
                self.url_rate_limit_history[root_url]
                and now - self.url_rate_limit_history[root_url][0] >= time_window
            ):
                self.url_rate_limit_history[root_url].popleft()

            # Determind if we hit or exceed the limit, and we should observe the limit
            if (
                not ignore_rate_limit
                and len(self.url_rate_limit_history[root_url]) >= executions_allowed
            ):
                time_to_wait = time_window - (
                    now - self.url_rate_limit_history[root_url][0]
                )
                time_to_wait = max(time_to_wait, 0)
                raise HTTPRateLimit(time_to_wait)

            # Add an entry for this call with the timestamp the call was placed
            self.url_rate_limit_history[root_url].append(now)

        url = url.replace(" ", "%20").replace("+", "%2b")

        method = method.lower()
        use_cache = kwargs.pop("use_cache", False)
        get_raw_response = kwargs.pop("get_raw_response", False)

        cache_key = url.lower()
        if kwargs.get("params"):
            params = urllib.parse.urlencode(kwargs.get("params"))
            cache_key = f"{cache_key}?{params}"

        cached_response = (
            self.http_cache.get(cache_key) if (use_cache and method == "get") else None
        )

        client = None
        if cached_response:
            response_object = cached_response
            log_message = f"Retrieving cached HTTP GET response ({cache_key})"
            return await self.process_http_response(
                response_object, method, cache_key, get_raw_response, log_message
            )
        async with aiohttp.ClientSession() as client:
            method_fn = getattr(client, method.lower())
            async with method_fn(url, *args, **kwargs) as response_object:
                log_message = (
                    f"Making HTTP {method.upper()} request to URL: {cache_key}"
                )
                return await self.process_http_response(
                    response_object,
                    method,
                    cache_key,
                    get_raw_response,
                    log_message,
                )

    async def process_http_response(
        self,
        response_object: aiohttp.ClientResponse,
        method: str,
        cache_key: str,
        get_raw_response: bool,
        log_message: bool,
    ) -> munch.Munch:
        """Processes the HTTP response object, both cached and fresh

        Args:
            response_object (aiohttp.ClientResponse): The raw response object
            method (str): The HTTP method this request is using
            cache_key (str): The key for the cache array
            get_raw_response (bool): Whether the function should return the response raw
            log_message (bool): The message to send to the log

        Returns:
            munch.Munch: The resposne object ready for use
        """
        if method == "get":
            self.http_cache[cache_key] = response_object

        await self.logger.send_log(
            message=log_message,
            level=LogLevel.INFO,
            console_only=True,
        )

        if get_raw_response:
            response = {
                "status": response_object.status,
                "text": await response_object.text(),
            }
        else:
            try:
                response_json = await response_object.json()
            except (
                aiohttp.ClientResponseError,
                JSONDecodeError,
            ) as exception:
                response_json = {}
                await self.logger.send_log(
                    message=f"{method.upper()} request to URL: {cache_key} failed",
                    level=LogLevel.ERROR,
                    console_only=True,
                    exception=exception,
                )

            response = (
                munch.munchify(response_json) if response_object else munch.Munch()
            )
            try:
                response["status_code"] = getattr(response_object, "status", None)
            except TypeError:
                await self.logger.send_log(
                    message="Failed to add status_code to API response",
                    level=LogLevel.WARNING,
                )

        return response
