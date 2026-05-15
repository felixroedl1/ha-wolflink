"""Authentication helpers for Wolf SmartSet."""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import parse_qs, urlparse

from httpx import AsyncClient, RequestError, Response
from lxml import html
import pkce
import shortuuid
from wolf_comm import constants
from wolf_comm.token_auth import InvalidAuth, PasswordToLong, Tokens

_LOGGER = logging.getLogger(__name__)

_RETRY_DELAYS = (2, 5)
_LOGIN_ERROR_KEYWORDS = (
    "invalid",
    "password",
    "user name",
    "username",
    "blocked",
    "lock",
    "captcha",
    "too many",
)


def _extract_authorization_code(response: Response) -> str | None:
    """Extract OAuth code from final URL or redirect history."""
    code = response.url.params.get("code")
    if code:
        return code

    for historic in reversed(response.history):
        code = historic.url.params.get("code")
        if code:
            return code

        location = historic.headers.get("location")
        if not location:
            continue

        parsed = urlparse(location)
        query_code = parse_qs(parsed.query).get("code")
        if query_code and query_code[0]:
            return query_code[0]

    return None


def _extract_verification_token(response_text: str) -> str | None:
    """Extract anti-forgery token from login form."""
    tree = html.document_fromstring(response_text)
    tokens = tree.xpath('//form//input[@name="__RequestVerificationToken"]/@value')
    for token in tokens:
        if token:
            return token
    return None


def _extract_login_error(response_text: str) -> str | None:
    """Extract likely login error message from returned HTML."""
    tree = html.document_fromstring(response_text)
    candidates = tree.xpath(
        (
            '//*[contains(@class,"validation-summary-errors") '
            'or contains(@class,"field-validation-error") '
            'or contains(@class,"text-danger") '
            'or contains(@class,"alert")]//text()'
        )
    )
    for raw in candidates:
        text = " ".join(raw.split())
        if not text:
            continue
        lower = text.casefold()
        if any(keyword in lower for keyword in _LOGIN_ERROR_KEYWORDS):
            return text
    return None


class WolflinkTokenAuth:
    """Patched TokenAuth with robust token extraction and retries."""

    def __init__(self, username: str, password: str):
        if len(password) > 30:
            raise PasswordToLong(
                f"Your password is {len(password)} long, but maximum is 30"
            )
        self.username = username
        self.password = password

    async def _token_once(self, client: AsyncClient) -> Tokens:
        code_verifier, code_challenge = pkce.generate_pkce_pair()
        state = shortuuid.uuid()

        verification_response = await client.get(
            url=f"{constants.AUTHENTICATION_BASE_URL}/Account/Login",
            params={
                "ReturnUrl": "/idsrv/connect/authorize/callback",
                "client_id": constants.AUTHENTICATION_CLIENT,
                "redirect_uri": f"{constants.BASE_URL}/signin-callback.html",
                "response_type": "code",
                "scope": "openid profile api role",
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "response_mode": "query",
                "lang": "de-DE",
            },
        )
        if verification_response.status_code >= 400:
            raise InvalidAuth(
                f"login_page_http_{verification_response.status_code}"
            )

        verification_token = _extract_verification_token(verification_response.text)
        if not verification_token:
            raise InvalidAuth("missing_verification_token")

        login_response = await client.post(
            url=f"{constants.AUTHENTICATION_BASE_URL}/Account/Login",
            params={
                "ReturnUrl": (
                    f"{constants.AUTHENTICATION_URL}/connect/authorize/callback?"
                    f"client_id={constants.AUTHENTICATION_CLIENT}"
                    f"&redirect_uri={constants.BASE_URL}/signin-callback.html"
                    "&response_type=code"
                    "&scope=openid profile api role"
                    f"&state={state}"
                    f"&code_challenge={code_challenge}"
                    "&code_challenge_method=S256"
                    "&response_mode=query"
                    "&lang=de-DE"
                )
            },
            headers={
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
            },
            data={
                "Input.Username": self.username,
                "Input.Password": self.password,
                "__RequestVerificationToken": verification_token,
            },
            cookies=verification_response.cookies,
            follow_redirects=True,
        )

        code = _extract_authorization_code(login_response)
        if not code:
            login_error = _extract_login_error(login_response.text)
            _LOGGER.debug(
                "Missing auth code from Wolf SmartSet login flow. final_url=%s status=%s history=%s login_error=%s",
                login_response.url,
                login_response.status_code,
                [str(item.url) for item in login_response.history],
                login_error,
            )
            if login_error:
                raise InvalidAuth(f"missing_authorization_code: {login_error}")
            raise InvalidAuth("missing_authorization_code")

        token_response = await client.post(
            f"{constants.AUTHENTICATION_BASE_URL}/connect/token",
            headers={
                "Cache-control": "no-cache",
                "User-Agent": "Mozilla/5.0",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "de-DE,de;q=0.8,en-US;q=0.5,en;q=0.3",
                "Referer": constants.BASE_URL + "/",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "same-origin",
                "TE": "trailers",
            },
            data={
                "client_id": "smartset.web",
                "code": code,
                "redirect_uri": constants.BASE_URL + "/signin-callback.html",
                "code_verifier": code_verifier,
                "grant_type": "authorization_code",
            },
        )

        try:
            json_data = token_response.json()
        except Exception as err:
            raise InvalidAuth("token_response_not_json") from err
        if "error" in json_data:
            error = json_data.get("error")
            error_description = json_data.get("error_description")
            if error_description:
                raise InvalidAuth(f"token_error:{error}:{error_description}")
            raise InvalidAuth(f"token_error:{error}")

        return Tokens(json_data.get("access_token"), json_data.get("expires_in"))

    async def token(self, client: AsyncClient) -> Tokens:
        """Fetch access token."""
        for attempt, delay in enumerate((0, *_RETRY_DELAYS), start=1):
            if delay:
                await asyncio.sleep(delay)
            try:
                return await self._token_once(client)
            except (InvalidAuth, RequestError) as err:
                err_text = str(err) or err.__class__.__name__
                if attempt >= len(_RETRY_DELAYS) + 1:
                    _LOGGER.error("An error occurred: %s", err_text)
                    raise InvalidAuth from err
                _LOGGER.debug(
                    "Authentication retry %s/%s after error: %s",
                    attempt,
                    len(_RETRY_DELAYS) + 1,
                    err_text,
                )
            except Exception as err:  # pragma: no cover - safety net
                _LOGGER.error("An error occurred: %s", err)
                raise InvalidAuth from err

        raise InvalidAuth
