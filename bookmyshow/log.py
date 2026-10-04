"""Structured logging: one JSON object per line, tagged with the request id.

Every request gets an id (the caller's ``X-Request-ID`` header if it sent a
sane one, otherwise a new one), returned in the ``X-Request-ID`` response
header and added to every log line written while the request is handled - so
a response, its request line and any error it caused can be matched up.

Each request is logged once, as a single "canonical" line written by
``RequestLogMiddleware`` (bookmyshow/middleware.py) when the response is ready.
Views add what they know to that line with ``bind()`` - the outcome, decline
reason, reservation id - instead of writing lines of their own, which keeps a
20k-request burst at 20k lines.

The id and bound fields live in context variables. gunicorn serves each request
on a single thread from start to finish, so they never leak between requests.
"""

import contextvars
import json
import logging
from datetime import datetime, timezone

request_id_var = contextvars.ContextVar('request_id', default=None)
_fields_var = contextvars.ContextVar('log_fields', default=None)

# Attributes every LogRecord has; anything else on a record came from `extra=`.
_STANDARD_ATTRS = set(vars(logging.makeLogRecord({}))) | {'message', 'asctime'}


def bind(**fields):
    """Add fields to the current request's log line (no-op outside a request)."""
    current = _fields_var.get()
    if current is not None:
        current.update(fields)


def start_request(request_id):
    """Begin collecting log context for a request; returns tokens for end_request."""
    return request_id_var.set(request_id), _fields_var.set({})


def bound_fields():
    return dict(_fields_var.get() or {})


def end_request(tokens):
    request_id_var.reset(tokens[0])
    _fields_var.reset(tokens[1])


def _extras(record):
    return {k: v for k, v in vars(record).items() if k not in _STANDARD_ATTRS and not k.startswith('_')}


class JsonFormatter(logging.Formatter):
    """``{"ts", "level", "logger", "msg", "request_id", ...extra fields}``."""

    def format(self, record):
        entry = {
            'ts': datetime.fromtimestamp(record.created, timezone.utc)
                  .isoformat(timespec='milliseconds').replace('+00:00', 'Z'),
            'level': record.levelname,
            'logger': record.name,
            'msg': record.getMessage(),
        }
        request_id = request_id_var.get()
        if request_id:
            entry['request_id'] = request_id
        entry.update(_extras(record))
        if record.exc_info:
            entry['exc'] = self.formatException(record.exc_info)
        # default=str: Django attaches objects (e.g. the HttpRequest) to some
        # records; they are logged by their repr rather than failing.
        return json.dumps(entry, default=str)


class TextFormatter(logging.Formatter):
    """Human-readable variant for local development (LOG_FORMAT=text)."""

    def format(self, record):
        ts = datetime.fromtimestamp(record.created).strftime('%H:%M:%S.%f')[:-3]
        request_id = request_id_var.get()
        line = f'{ts} {record.levelname:<7} {record.name}'
        if request_id:
            line += f' [{request_id[:8]}]'
        line += f' {record.getMessage()}'
        extras = _extras(record)
        if extras:
            line += ' ' + ' '.join(f'{k}={v}' for k, v in extras.items())
        if record.exc_info:
            line += '\n' + self.formatException(record.exc_info)
        return line
