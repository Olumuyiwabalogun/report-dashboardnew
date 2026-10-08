"""Lambda entry point for API Gateway (HTTP API, payload v1 or v2)."""
from apig_wsgi import make_lambda_handler

from app import app

handler = make_lambda_handler(app)
