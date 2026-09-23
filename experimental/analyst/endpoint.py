"""Pure endpoint parsing, address classification, and Ollama URL construction.

This module owns everything Analyst knows about *where* a model server lives.
It performs no network, database, or Tk I/O.  Name resolution and the full
transport policy are N3 concerns; N1 classifies literal addresses only and
reports a hostname as :attr:`AddressClass.UNRESOLVED`.

D17: until N3 writes the transport policy, :func:`require_connectable` refuses
any non-loopback endpoint.  The guard is called from the network client, not
from the UI, so a script or a direct database write cannot bypass it.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from enum import Enum
from typing import Final, NamedTuple


DEFAULT_SCHEME: Final = "http"
DEFAULT_HOST: Final = "127.0.0.1"
DEFAULT_PORT: Final = 11434
DEFAULT_ENDPOINT: Final = f"{DEFAULT_SCHEME}://{DEFAULT_HOST}:{DEFAULT_PORT}"

MAX_HOST_CHARS: Final = 253
MAX_ENDPOINT_CHARS: Final = 320

_SCHEMES: Final = frozenset({"http", "https"})
_LABEL = re.compile(r"[0-9A-Za-z]([0-9A-Za-z-]{0,61}[0-9A-Za-z])?\Z", re.ASCII)
_ENDPOINT = re.compile(
    r"(?P<scheme>[A-Za-z][0-9A-Za-z+.-]*)://"
    r"(?P<host>\[[0-9A-Fa-f:.]+\]|[^/?#:\[\]@]+)"
    r"(?::(?P<port>[0-9]{1,5}))?\Z",
    re.ASCII,
)

# RFC 6761 reserves "localhost" for the loopback interface.  It is the one name
# Analyst treats as loopback without resolving it.
_LOOPBACK_NAMES: Final = frozenset({"localhost"})

_RFC1918: Final = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)
_RFC6598: Final = ipaddress.ip_network("100.64.0.0/10")
_UNIQUE_LOCAL: Final = ipaddress.ip_network("fd00::/8")


class EndpointError(ValueError):
    """A supplied endpoint is outside the N1 endpoint contract."""


class RemoteNotEnabledError(EndpointError):
    """D17: a non-loopback endpoint was reached before N3 enabled remote use."""


class AddressClass(str, Enum):
    """How a host literal is classified without resolving any name."""

    LOOPBACK = "loopback"
    PRIVATE = "private"
    CGNAT = "cgnat"
    UNIQUE_LOCAL = "unique_local"
    LINK_LOCAL = "link_local"
    PUBLIC = "public"
    UNRESOLVED = "unresolved"


#: Classes §4.2 of the remote-backends contract permits under a plaintext
#: acknowledgement.  N3 enforces this; N1 only reports it.
PRIVATE_CLASSES: Final = frozenset(
    {
        AddressClass.LOOPBACK,
        AddressClass.PRIVATE,
        AddressClass.CGNAT,
        AddressClass.UNIQUE_LOCAL,
    }
)


class OllamaUrls(NamedTuple):
    """The four native Ollama URLs derived from one endpoint."""

    version: str
    tags: str
    ps: str
    chat: str


def _normalize_host(value: object) -> str:
    if type(value) is not str:
        raise EndpointError("endpoint host must be a string")
    host = value.strip()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if not host or len(host) > MAX_HOST_CHARS:
        raise EndpointError("endpoint host is empty or too long")
    lowered = host.lower()
    try:
        parsed = ipaddress.ip_address(lowered)
    except ValueError:
        labels = lowered.split(".")
        if lowered.endswith("."):
            labels = labels[:-1]
        if not labels or any(_LABEL.fullmatch(label) is None for label in labels):
            raise EndpointError("endpoint host is not an IP address or hostname")
        return ".".join(labels)
    mapped = getattr(parsed, "ipv4_mapped", None)
    if mapped is not None:
        parsed = mapped
    return parsed.compressed


def _normalize_port(value: object) -> int:
    if type(value) is bool or type(value) is not int:
        if type(value) is str and value.isdigit():
            value = int(value)
        else:
            raise EndpointError("endpoint port must be an integer")
    if not 1 <= value <= 65535:
        raise EndpointError("endpoint port is outside 1-65535")
    return value


def _normalize_scheme(value: object) -> str:
    if type(value) is not str:
        raise EndpointError("endpoint scheme must be a string")
    scheme = value.strip().lower()
    if scheme not in _SCHEMES:
        raise EndpointError("endpoint scheme must be http or https")
    return scheme


def classify_host(host: object) -> AddressClass:
    """Classify one host literal without performing any name resolution."""
    normalized = _normalize_host(host)
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        if normalized in _LOOPBACK_NAMES:
            return AddressClass.LOOPBACK
        return AddressClass.UNRESOLVED
    if address.is_loopback:
        return AddressClass.LOOPBACK
    if address.version == 4:
        if any(address in network for network in _RFC1918):
            return AddressClass.PRIVATE
        if address in _RFC6598:
            return AddressClass.CGNAT
    elif address in _UNIQUE_LOCAL:
        return AddressClass.UNIQUE_LOCAL
    if address.is_link_local:
        return AddressClass.LINK_LOCAL
    return AddressClass.PUBLIC


@dataclass(frozen=True, slots=True)
class Endpoint:
    """One validated `scheme://host:port` model-server address."""

    scheme: str = DEFAULT_SCHEME
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT

    def __post_init__(self) -> None:
        object.__setattr__(self, "scheme", _normalize_scheme(self.scheme))
        object.__setattr__(self, "host", _normalize_host(self.host))
        object.__setattr__(self, "port", _normalize_port(self.port))

    @property
    def address_class(self) -> AddressClass:
        """Return this endpoint's host classification."""
        return classify_host(self.host)

    @property
    def is_loopback(self) -> bool:
        """Return whether this endpoint names the local machine."""
        return self.address_class is AddressClass.LOOPBACK

    @property
    def base_url(self) -> str:
        """Return the canonical `scheme://host:port` string."""
        host = self.host
        if ":" in host:
            host = f"[{host}]"
        return f"{self.scheme}://{host}:{self.port}"

    def urls(self) -> OllamaUrls:
        """Return the four native Ollama URLs for this endpoint."""
        return ollama_urls(self)

    def __str__(self) -> str:
        return self.base_url


LOOPBACK_ENDPOINT: Final = Endpoint()


def parse_endpoint(value: object) -> Endpoint:
    """Parse one `scheme://host[:port]` string into a validated endpoint."""
    if isinstance(value, Endpoint):
        return value
    if type(value) is not str:
        raise EndpointError("endpoint must be a string")
    text = value.strip().rstrip("/")
    if not text or len(text) > MAX_ENDPOINT_CHARS:
        raise EndpointError("endpoint is empty or too long")
    match = _ENDPOINT.fullmatch(text)
    if match is None:
        raise EndpointError("endpoint must look like scheme://host:port")
    port = match.group("port")
    return Endpoint(
        scheme=match.group("scheme"),
        host=match.group("host"),
        port=DEFAULT_PORT if port is None else _normalize_port(int(port)),
    )


def normalize_endpoint(value: object) -> str:
    """Return the canonical string form of one endpoint."""
    return parse_endpoint(value).base_url


def ollama_urls(value: object) -> OllamaUrls:
    """Return the four native Ollama URLs derived from one endpoint."""
    base = parse_endpoint(value).base_url
    return OllamaUrls(
        version=f"{base}/api/version",
        tags=f"{base}/api/tags",
        ps=f"{base}/api/ps",
        chat=f"{base}/api/chat",
    )


def is_loopback_endpoint(value: object) -> bool:
    """Return whether one endpoint names the local machine."""
    try:
        return parse_endpoint(value).is_loopback
    except EndpointError:
        return False


class AddressPolicyError(EndpointError):
    """An endpoint is refused by the transport policy (contract 4.1-4.3)."""


def permits_plaintext(address_class: AddressClass) -> bool:
    """Return whether contract 4.2 admits plaintext to this address class.

    RFC1918, loopback, RFC6598 CGNAT (which covers Tailscale) and fd00::/8.
    Anything else -- including a name nothing has resolved -- is not admitted.
    """
    return address_class in PRIVATE_CLASSES


def check_address_policy(
    value: object,
    *,
    plaintext_ack: bool = False,
    resolved_class: AddressClass | None = None,
) -> Endpoint:
    """Apply contract 4.1-4.3 to one endpoint and return it, or refuse.

    ``resolved_class`` is what the host actually resolves to. It is supplied by
    the caller because resolution is I/O and this module performs none. A
    literal address classifies itself; a name must be resolved first.

    The rule that matters: plaintext to a public address is refused, and there
    is no override. That is the constraint preserving the Phase 1 promise that
    raw port 11434 never becomes a public interface.
    """
    endpoint = parse_endpoint(value)
    if endpoint.is_loopback:
        return endpoint
    if endpoint.scheme == "https":
        # TLS is verified against the OS trust store, or against a pinned
        # certificate on the profile (contract 4.4). Either way the connection
        # is authenticated, so the plaintext rules below do not apply.
        return endpoint
    actual = resolved_class if resolved_class is not None else endpoint.address_class
    if actual is AddressClass.UNRESOLVED:
        raise AddressPolicyError(
            f"{endpoint.host} has not been resolved, so its address class is "
            "unknown. Analyst refuses plaintext to an unknown address."
        )
    if not permits_plaintext(actual):
        raise AddressPolicyError(
            f"{endpoint.base_url} is a {actual.value} address. Analyst refuses "
            "plaintext outside private ranges, and there is no override."
        )
    if not plaintext_ack:
        raise AddressPolicyError(
            f"{endpoint.base_url} is plaintext on a {actual.value} address. "
            "Acknowledge plaintext on the profile to allow it."
        )
    return endpoint


def require_connectable(value: object) -> Endpoint:
    """Return the endpoint, or refuse it under D17.

    N1 parameterises the endpoint but does not write the transport policy.
    Until N3 lands, only a loopback endpoint may be contacted.  This guard is
    the last step before the socket; it is deliberately not in the UI.
    """
    endpoint = parse_endpoint(value)
    if not endpoint.is_loopback:
        raise RemoteNotEnabledError(
            f"Remote servers are not enabled yet. {endpoint.base_url} is not "
            "loopback. Remote support arrives with the security card (N3)."
        )
    return endpoint


__all__ = [
    "AddressClass",
    "AddressPolicyError",
    "DEFAULT_ENDPOINT",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "DEFAULT_SCHEME",
    "Endpoint",
    "EndpointError",
    "LOOPBACK_ENDPOINT",
    "MAX_ENDPOINT_CHARS",
    "MAX_HOST_CHARS",
    "OllamaUrls",
    "PRIVATE_CLASSES",
    "RemoteNotEnabledError",
    "check_address_policy",
    "classify_host",
    "is_loopback_endpoint",
    "normalize_endpoint",
    "permits_plaintext",
    "ollama_urls",
    "parse_endpoint",
    "require_connectable",
]
