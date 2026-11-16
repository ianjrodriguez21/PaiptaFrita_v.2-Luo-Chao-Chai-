"""Inicio de sesión local con Cognito, Authorization Code y PKCE."""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from http.cookies import CookieError, SimpleCookie
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DOMAIN = os.getenv("COGNITO_LOGIN_DOMAIN", "https://arquifusion-vannia-dev.auth.us-east-1.amazoncognito.com").rstrip("/")
CLIENT_ID = os.getenv("COGNITO_CLIENT_ID", "4cphmg0npd6l26e35757ujfc3r")
CALLBACK = "http://localhost:8080/auth/callback"
SIGNED_OUT_URL = "http://localhost:8080/"
SIGNING_KEY = secrets.token_bytes(32)
FLOW_COOKIE = "arquifusion_oauth"
SESSION_COOKIE = "arquifusion_session"


def cookie(name, value, max_age):
    return f"{name}={value}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Lax"


def read_cookie(headers, name):
    try:
        value = SimpleCookie(headers.get("Cookie", "")).get(name)
    except CookieError:
        return ""
    return value.value if value else ""


def signed(value):
    signature = hmac.new(SIGNING_KEY, value.encode(), hashlib.sha256).hexdigest()
    return f"{value}.{signature}"


def verified(value):
    payload, separator, _ = value.rpartition(".")
    return payload if separator and value.isascii() and hmac.compare_digest(value, signed(payload)) else ""


def start(sign_up=False):
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    parameters = urlencode({
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": CALLBACK,
        "scope": "openid email",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    flow = signed(f"{state}.{verifier}.{int(time.time()) + 600}")
    endpoint = "/signup" if sign_up else "/oauth2/authorize"
    return f"{DOMAIN}{endpoint}?{parameters}", cookie(FLOW_COOKIE, flow, 600)


def complete(headers, parameters):
    flow = verified(read_cookie(headers, FLOW_COOKIE))
    code = parameters.get("code", [""])[0]
    received_state = parameters.get("state", [""])[0]
    if not flow or not code or not received_state.isascii():
        raise ValueError("Flujo incompleto")
    state, verifier, expires = flow.split(".")
    if not hmac.compare_digest(state, received_state) or int(expires) <= time.time():
        raise ValueError("Flujo inválido o vencido")
    request = Request(f"{DOMAIN}/oauth2/token", data=urlencode({
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "redirect_uri": CALLBACK,
        "code": code,
        "code_verifier": verifier,
    }).encode(), headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urlopen(request, timeout=10) as response:
        tokens = json.load(response)
    lifetime = min(int(tokens["expires_in"]), 3600)
    if lifetime <= 0 or not tokens.get("access_token"):
        raise ValueError("Respuesta de Cognito incompleta")
    return cookie(SESSION_COOKIE, signed(str(int(time.time()) + lifetime)), lifetime)


def active(headers):
    expires = verified(read_cookie(headers, SESSION_COOKIE))
    return expires.isdecimal() and int(expires) > time.time()


def sign_out_url():
    parameters = urlencode({"client_id": CLIENT_ID, "logout_uri": SIGNED_OUT_URL})
    return f"{DOMAIN}/logout?{parameters}"
