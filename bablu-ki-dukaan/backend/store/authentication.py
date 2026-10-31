"""Token authentication with an expiry time.

Kept in its own file ON PURPOSE: DRF imports this class while it is still loading rest_framework.views,
so this file must not import views, throttling or anything else that imports them.
"""
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from rest_framework import exceptions
from rest_framework.authentication import TokenAuthentication


def token_ttl():
    return timedelta(hours=settings.TOKEN_TTL_HOURS)


class ExpiringTokenAuthentication(TokenAuthentication):
    """Same as DRF's TokenAuthentication, but tokens die after TOKEN_TTL_HOURS."""

    def authenticate_credentials(self, key):
        user, token = super().authenticate_credentials(key)
        if token.created + token_ttl() < timezone.now():
            token.delete()
            raise exceptions.AuthenticationFailed("Session expired. Please log in again.")
        return user, token