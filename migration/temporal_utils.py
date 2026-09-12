"""Shared helpers for Temporal activities that go through the Django ORM."""

from __future__ import annotations

import functools

from django.db import close_old_connections


def release_db_connection(func):
    """Activities run in the Worker's thread pool, each thread getting its
    own Django DB connection on first use -- close it when the activity
    finishes so a long-running worker doesn't accumulate idle connections."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        finally:
            close_old_connections()

    return wrapper
