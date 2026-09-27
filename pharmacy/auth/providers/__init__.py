"""Identity provider adapters.

An adapter knows two things about a provider: how to reach its keys and
where it puts the claims the contract (`pharmacy.auth.claims`) needs. The
generic OIDC adapter covers any provider that can be configured to emit the
contract directly; a provider with its own claim layout subclasses it and
overrides `claims()`. Which one is in use is the AUTH_PROVIDER setting.
"""

from django.conf import settings
from django.utils.module_loading import import_string

from .base import IdentityProvider
from .oidc import OIDCProvider

_provider = None


def get_provider():
    """The configured adapter, built once per process from settings."""
    global _provider
    if _provider is None:
        _provider = import_string(settings.AUTH_PROVIDER).from_settings()
    return _provider


def set_provider(provider):
    """Replace the adapter -- the tests' seam."""
    global _provider
    _provider = provider


__all__ = ["IdentityProvider", "OIDCProvider", "get_provider", "set_provider"]
