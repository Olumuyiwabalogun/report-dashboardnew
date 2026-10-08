"""Shared-password gates. Viewers and admins have different passwords.

Local runs with neither variable set are open. In AWS both are set, and a missing or unreadable
password blocks everyone (fails closed).
"""
import hmac
import os

from flask import Response, request

_cache = {}


def _password(param):
    if param not in _cache:  # one SSM call per warm Lambda, not per request
        import boto3
        resp = boto3.client("ssm").get_parameter(Name=param, WithDecryption=True)
        _cache[param] = resp["Parameter"]["Value"]
    return _cache[param]


def gate():
    """Flask before_request hook: return a Response to block, or None to continue."""
    if request.path == "/health":
        return None
    is_admin = request.path.startswith("/admin")
    in_cloud = bool(os.getenv("DASH_PASSWORD_PARAM"))
    param = os.getenv("ADMIN_PASSWORD_PARAM" if is_admin else "DASH_PASSWORD_PARAM")
    if not in_cloud and not param:
        return None
    if not param:
        return Response("Sign-in is unavailable right now.", 503)
    try:
        expected = _password(param)
    except Exception:
        return Response("Sign-in is unavailable right now.", 503)
    given = request.authorization.password if request.authorization else ""
    if given and hmac.compare_digest(given.encode(), expected.encode()):
        return None
    realm = "Register admin" if is_admin else "Report dashboard"
    return Response("Sign in to continue.", 401, {"WWW-Authenticate": f'Basic realm="{realm}"'})
