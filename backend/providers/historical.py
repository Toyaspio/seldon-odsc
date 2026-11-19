"""Conservative publication checks for historical search leads."""
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from time import monotonic
from urllib.parse import urlsplit
from urllib.request import Request

from backend.providers.live import open_provider_request
from backend.providers.transport import read_bounded


def publication_day(value: object) -> date | None:
    if not isinstance(value, str) or len(value) > 120:
        return None
    value = value.strip()
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        try:
            return parsedate_to_datetime(value).date()
        except (TypeError, ValueError, OverflowError):
            return None


class PublicationMetadata(HTMLParser):
    def __init__(self):
        super().__init__()
        self.published = []
        self.modified = []

    def handle_starttag(self, tag, attrs):
        if tag != 'meta':
            return
        fields = dict(attrs)
        key = (fields.get('property') or fields.get('name') or fields.get('itemprop') or '').lower()
        if key in {'article:published_time', 'datepublished'}:
            self.published.append(publication_day(fields.get('content')))
        elif key in {'article:modified_time', 'datemodified'}:
            self.modified.append(publication_day(fields.get('content')))


def source_publication(url: str, allowed_hosts: set[str], cutoff: date, timeout: float) -> date | None:
    """Use publisher metadata, with no redirects or request to an arbitrary search host."""
    try:
        address = urlsplit(url)
        if (address.scheme != 'https' or address.hostname not in allowed_hosts
                or address.username is not None or address.password is not None
                or address.port not in {None, 443}):
            return None
        request = Request(url, headers={'Accept': 'text/html,application/xhtml+xml',
                                        'User-Agent': 'Seldon Research Agent'})
        budget = min(timeout, 3)
        deadline = monotonic() + budget
        with open_provider_request(request, budget) as response:
            if response.headers.get_content_type() not in {'text/html', 'application/xhtml+xml'}:
                return None
            raw = read_bounded(response, 1024 * 1024, deadline)
        metadata = PublicationMetadata()
        metadata.feed(raw.decode('utf-8', errors='replace'))
    except (OSError, ValueError):
        return None
    if not metadata.published or len(set(metadata.published)) != 1 or None in metadata.published:
        return None
    if any(day is None or day > cutoff for day in metadata.modified):
        return None
    return metadata.published[0]
