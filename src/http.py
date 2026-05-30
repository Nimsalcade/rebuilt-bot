#!/usr/bin/env python3
"""
Gabagool Bot - HTTP Utilities Module

Purpose:
    Thread-local HTTP session management to avoid cross-thread
    connection reuse issues.

Author: AI-Generated (extracted from discountry/polymarket-trading-bot)
Created: 2026-01-26
Modified: 2026-01-26

Source:
    Extracted from: samples/discountry-base/src/http.py

Dependencies:
    - requests

Usage:
    from src.http import ThreadLocalSessionMixin

    class MyClient(ThreadLocalSessionMixin):
        def get_data(self):
            return self.session.get("https://api.example.com/data")

Notes:
    - Each thread gets its own requests.Session instance
    - Prevents connection pool issues in multi-threaded code
    - Used as a mixin for API client classes
"""

import threading
from typing import Any

import requests


class ThreadLocalSessionMixin:
    """
    Mixin providing a thread-local requests.Session.

    Each thread gets its own Session instance to keep connections isolated.
    This prevents issues with connection pooling in multi-threaded environments.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize with thread-local storage for sessions."""
        self._session_local = threading.local()
        super().__init__(*args, **kwargs)

    def _get_session(self) -> requests.Session:
        """Get a thread-local session to avoid cross-thread reuse."""
        session = getattr(self._session_local, "session", None)
        if session is None:
            session = requests.Session()
            self._session_local.session = session
        return session

    @property
    def session(self) -> requests.Session:
        """Expose the thread-local session for internal use."""
        return self._get_session()

    def close_session(self) -> None:
        """Close the current thread's session if it exists."""
        session = getattr(self._session_local, "session", None)
        if session is not None:
            session.close()
            self._session_local.session = None
