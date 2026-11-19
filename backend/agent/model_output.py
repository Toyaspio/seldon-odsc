"""Keep independently readable items from generated research."""
from contextvars import ContextVar
import json
import re
import sqlite3

from pydantic import ValidationError
from pydantic_core import from_json



model_diagnostic_sink = ContextVar('model_diagnostic_sink', default=None)


def retain_model_response(stage, content):
    sink = model_diagnostic_sink.get()
    if sink is None or not isinstance(content, str):
        return
    from backend.settings import runtime_values

    try:
        for key in runtime_values('NEBIUS_API_KEY', 'TAVILY_API_KEY'):
            if key:
                content = content.replace(key, '[redacted]')
        sink(stage, {'response': content[:24000]})
    except (OSError, sqlite3.Error):
        pass


def model_json(text, *, partial=False):
    """Read a model's JSON envelope; the caller still owns semantic validation."""
    decoder = json.JSONDecoder()
    starts = list(re.finditer(r'(?m)^\s*(?:`{3}(?:json)?\s*)?([\[{])', text, re.I))
    if not starts:
        first = re.search(r'[\[{]', text)
        offsets = [first.start()] if first else []
    else:
        offsets = [item.start(1) for item in starts[:16]]
    for start in offsets:
        try:
            return decoder.raw_decode(text, start)[0]
        except ValueError:
            continue
    if partial and offsets:
        return from_json(text[offsets[0]:], allow_partial=True)
    raise ValueError('No readable model JSON')


def prose_paragraphs(text):
    return [part.strip(' #*\r\n') for part in re.split(r'\n\s*\n', text)
            if part.strip(' #*\r\n') and not part.strip().startswith('```')]


def cited_prose(text, references):
    return [(part, [ref for ref in references if re.search(
        rf'(?<![\w:]){re.escape(ref)}(?![\w:])', part)]) for part in prose_paragraphs(text)]


def usable_model_items(items, model, limit):
    if not isinstance(items, list):
        return []
    result = []
    for item in items:
        try:
            result.append(model.model_validate(item))
        except ValidationError:
            continue
        if len(result) == limit:
            break
    return result
