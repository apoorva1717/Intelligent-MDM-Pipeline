"""VIES REST response doubles, shaped on live answers recorded 2026-10-15.

Every body here copies a real ``POST /check-vat-number`` /
``/check-vat-test-service`` response shape (field names, the ``"---"``
placeholder, ``errorWrappers`` delivered with HTTP 200). Names and addresses
are obviously fake.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

REQUEST_DATE = "2026-10-15T13:41:07.759Z"


def valid_undisclosed(country: str, number: str) -> dict[str, Any]:
    """A VALID German/Spanish answer: VIES confirms, discloses nothing."""
    return {
        "countryCode": country, "vatNumber": number, "requestDate": REQUEST_DATE,
        "valid": True, "requestIdentifier": "", "name": "---", "address": "---",
    }


def valid_disclosed(
    country: str, number: str, *,
    name: str = "EXAMPLE TRADING SA",
    address: str = "1 RUE D'EXEMPLE\n75000 PARIS",
    request_identifier: str = "",
) -> dict[str, Any]:
    return {
        "countryCode": country, "vatNumber": number, "requestDate": REQUEST_DATE,
        "valid": True, "requestIdentifier": request_identifier,
        "name": name, "address": address,
    }


def invalid(country: str, number: str) -> dict[str, Any]:
    return {
        "countryCode": country, "vatNumber": number, "requestDate": REQUEST_DATE,
        "valid": False, "requestIdentifier": "", "name": "---", "address": "---",
    }


def error(code: str) -> dict[str, Any]:
    return {"actionSucceed": False, "errorWrappers": [{"error": code}]}


class ViesStub:
    """A scripted VIES. ``replies`` is consumed one per request (the last one
    repeats); a dict is sent as a 200 JSON body, exactly as VIES sends errors.
    Every request body is kept in ``requests``."""

    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies)
        self.requests: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content or b"{}"))
        reply = self.replies[min(len(self.requests), len(self.replies)) - 1]
        if callable(reply) and not isinstance(reply, httpx.Response):
            reply = reply(self.requests[-1])
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, httpx.Response):
            return reply
        return httpx.Response(200, json=reply)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


def echo_valid_undisclosed(body: dict[str, Any]) -> dict[str, Any]:
    """Reply builder: VALID-undisclosed for whatever number was asked."""
    return valid_undisclosed(body["countryCode"], body["vatNumber"])

