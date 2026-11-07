"""Redaction corpus shared by the filter tests and the property tests (PRD-001 §4).

Each entry is ``(label, build, must_not_contain)``: ``build(token)`` returns the
value to log (as a %-arg or an ``extra`` field) with ``token`` planted as the
secret. The token is random per test run so a case cannot pass by coincidence.
``NEGATIVE`` entries must *survive* — they guard against the filter redacting
everything.
"""

from __future__ import annotations

import dataclasses
import secrets
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel


def random_token() -> str:
    # Hex only: no regex metacharacters, no separators the assignment rule
    # treats as value terminators.
    return secrets.token_hex(10)


@dataclasses.dataclass
class Creds:
    user: str
    password: str


class ApiModel(BaseModel):
    endpoint: str
    api_key: str


class _Plain:
    def __init__(self, token: str) -> None:
        self.name = "svc"
        self.auth_token = token


Case = tuple[str, Callable[[str], Any]]

# fmt: off
POSITIVE: list[Case] = [
    # compound keys
    ("key aws_secret_access_key", lambda t: {"aws_secret_access_key": t}),
    ("key DB_PASSWORD", lambda t: {"DB_PASSWORD": t}),
    ("key apiToken", lambda t: {"apiToken": t}),
    ("key client_secret", lambda t: {"client_secret": t}),
    ("key x-auth-token", lambda t: {"x-auth-token": t}),
    ("key authorization_header", lambda t: {"authorization_header": t}),
    ("key X-Api-Key", lambda t: {"X-Api-Key": t}),
    ("key Cookie", lambda t: {"Cookie": f"session={t}"}),
    ("key session", lambda t: {"session": t}),
    ("key credentials", lambda t: {"credentials": t}),
    # quoted keys inside strings
    ("json double-quoted", lambda t: f'{{"password":"{t}"}}'),
    ("json single-quoted", lambda t: f"{{'token': '{t}'}}"),
    ("yaml", lambda t: f'password: "{t}"'),
    # spacing variants
    ("spaced =", lambda t: f"password = {t}"),
    ("=space", lambda t: f"password= {t}"),
    ("colon tight", lambda t: f"password:{t}"),
    ("is", lambda t: f"password is {t}"),
    ("is:", lambda t: f"password is: {t}"),
    ("api key with space", lambda t: f"api key={t}"),
    # header values
    ("Authorization Bearer", lambda t: {"Authorization": f"Bearer {t}"}),
    ("bearer in text", lambda t: f"authorization: Bearer {t} for user bob"),
    # URL userinfo
    ("https userinfo", lambda t: f"https://u:{t}@h/"),
    ("postgresql dsn", lambda t: f"postgresql://u:{t}@h:5432/db"),
    ("redis no user", lambda t: f"redis://:{t}@h"),
    ("amqp", lambda t: f"amqp://u:{t}@h"),
    ("s3 style", lambda t: f"s3://AKIA:{t}@bucket/key"),
    # bytes
    ("bytes assignment", lambda t: f"password={t}".encode()),
    ("bytearray colon", lambda t: bytearray(f"token: {t}".encode())),
    ("bytes json", lambda t: f'{{"secret":"{t}"}}'.encode()),
    # containers
    ("depth 4", lambda t: {"a": [{"b": ({"c": {"db_password": t}},)}]}),
    ("list of dicts", lambda t: [{"ok": 1}, {"token": t}]),
    ("set of strings", lambda t: {f"password={t}", "x"}),
    ("frozenset", lambda t: frozenset({f"secret: {t}"})),
    ("tuple in tuple", lambda t: (("api_key", f"api_key={t}"),)),
    # objects
    ("SimpleNamespace", lambda t: SimpleNamespace(password=t)),
    ("dataclass", lambda t: Creds(user="bob", password=t)),
    ("pydantic model", lambda t: ApiModel(endpoint="https://x", api_key=t)),
    ("plain object attr", lambda t: _Plain(t)),
    ("object inside list", lambda t: [SimpleNamespace(client_secret=t)]),
]

NEGATIVE: list[Case] = [
    ("passport is not a keyword", lambda t: {"passport": t}),
    ("tokyo is not token", lambda t: {"city": f"tokyo {t}"}),
    ("plain value", lambda t: {"user": t}),
    ("url without userinfo", lambda t: f"https://host/{t}"),
    ("bytes without keyword", lambda t: f"payload={t}".encode()),
    ("dataclass benign field", lambda t: Creds(user=t, password="x")),
    ("pydantic benign field", lambda t: ApiModel(endpoint=t, api_key="x")),
    ("object benign attr", lambda t: SimpleNamespace(name=t)),
]
# fmt: on


def cycle_case(t: str) -> dict[str, Any]:
    cyclic: dict[str, Any] = {"secret": t}
    cyclic["self"] = cyclic
    cyclic["items"] = [cyclic, {"db_password": t}]
    return cyclic
