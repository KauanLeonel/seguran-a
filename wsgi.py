"""WSGI transport for the existing request handlers; no built-in HTTP server."""
from email.message import Message
from http import HTTPStatus
from io import BytesIO
import ipaddress
import logging
import os
from types import SimpleNamespace
from urllib.parse import urlsplit

import app


class RequestHandler(app.Handler):
    def __init__(self, environ, settings):
        self.server = settings
        self.path = environ.get('PATH_INFO', '/')
        if environ.get('QUERY_STRING'):
            self.path += '?' + environ['QUERY_STRING']
        self.headers = Message()
        for key, value in environ.items():
            if key.startswith('HTTP_'):
                self.headers[key[5:].replace('_', '-')] = value
        for key in ('CONTENT_TYPE', 'CONTENT_LENGTH'):
            if environ.get(key):
                self.headers[key.replace('_', '-')] = environ[key]
        remote = environ.get('REMOTE_ADDR', '')
        if remote in settings.trusted_proxies:
            candidate = environ.get('HTTP_X_REAL_IP', '')
            try:
                remote = str(ipaddress.ip_address(candidate))
            except ValueError:
                pass
        self.client_address = (remote, 0)
        self.rfile = environ['wsgi.input']
        self.wfile = BytesIO()
        self.response_headers = []
        self.status = 500
        self.new_cookie = None

    def send_response(self, code, message=None):
        self.status = code

    def send_header(self, key, value):
        # Hop-by-hop headers belong to Gunicorn, not the WSGI application.
        if key.lower() != 'connection':
            self.response_headers.append((key, value))

    def end_headers(self):
        pass


def create_application(origin=None, initialize=True):
    origin = origin or os.environ.get('MARKET_ORIGIN', '')
    parsed = urlsplit(origin)
    if parsed.scheme != 'https' or not parsed.netloc or parsed.path or parsed.query or parsed.fragment or parsed.username:
        raise RuntimeError('MARKET_ORIGIN deve ser https://DOMINIO ou https://IP, sem barra final.')
    settings = SimpleNamespace(origin=origin, authority=parsed.netloc, secure=True,
                               trusted_proxies=set(os.environ.get('MARKET_TRUSTED_PROXIES', '127.0.0.1').split(',')))
    if initialize:
        app.init()

    def application(environ, start_response):
        handler = RequestHandler(environ, settings)
        try:
            method = environ.get('REQUEST_METHOD')
            if method == 'GET':
                handler.do_GET()
            elif method == 'POST':
                handler.do_POST()
            else:
                handler.reply(405, 'Método não permitido.')
        except Exception as error:
            # Do not expose form bodies, database errors, or credentials in logs.
            logging.error('Falha na requisição: %s', type(error).__name__)
            handler.response_headers = []
            handler.wfile = BytesIO()
            handler.new_cookie = None
            handler.reply(500, 'Não foi possível concluir. Tente novamente mais tarde.')
        start_response(f'{handler.status} {HTTPStatus(handler.status).phrase}', handler.response_headers)
        return [handler.wfile.getvalue()]
    return application
