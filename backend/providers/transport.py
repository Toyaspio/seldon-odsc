from http.client import HTTPSConnection
import logging
from socket import SHUT_RDWR
from threading import Timer
from time import monotonic
from urllib.request import HTTPSHandler


logger = logging.getLogger(__name__)


class ProviderTimeout(TimeoutError):
    def __init__(self, phase: str):
        super().__init__('Provider request timed out')
        self.phase = phase


def remaining_time(deadline: float, phase: str) -> float:
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise ProviderTimeout(phase)
    return remaining


class DeadlineHTTPSHandler(HTTPSHandler):
    def https_open(self, request):
        deadline = monotonic() + request.timeout
        return self.do_open(DeadlineConnection, request, deadline=deadline, context=self._context)


class DeadlineConnection(HTTPSConnection):
    def __init__(self, *args, deadline: float, **kwargs):
        super().__init__(*args, **kwargs)
        self.deadline = deadline

    def connect(self):
        phase = 'connect'
        self.timeout = min(10, remaining_time(self.deadline, phase))
        try:
            super().connect()
        except TimeoutError:
            raise ProviderTimeout(phase) from None
        self.sock.settimeout(remaining_time(self.deadline, 'request_write'))

    def request(self, *args, **kwargs):
        try:
            super().request(*args, **kwargs)
        except ProviderTimeout:
            raise
        except TimeoutError:
            raise ProviderTimeout('request_write') from None
        logger.info('host=%s phase=request_sent', self._tunnel_host or self.host)

    def getresponse(self):
        phase = 'response_headers'
        sock = self.sock
        remaining = remaining_time(self.deadline, phase)
        sock.settimeout(remaining)

        def interrupt_read():
            try:
                sock.shutdown(SHUT_RDWR)
            except OSError:
                pass

        # Response headers are read inside http.client. Interrupt a peer that sends
        # bytes slowly enough to keep the socket's idle timeout from firing.
        timer = Timer(remaining, interrupt_read)
        timer.daemon = True
        timer.start()
        try:
            response = super().getresponse()
        except OSError as exc:
            if isinstance(exc, TimeoutError) or monotonic() >= self.deadline:
                raise ProviderTimeout(phase) from None
            raise
        finally:
            timer.cancel()
        try:
            remaining_time(self.deadline, phase)
        except ProviderTimeout:
            response.close()
            raise
        response.set_read_timeout = sock.settimeout
        return response


def read_bounded(response, limit: int, deadline: float) -> bytes:
    # read1 returns at most one buffered/raw read so a slow stream cannot keep resetting
    # the effective job deadline while accumulating a full response.
    reader = getattr(response, 'read1', response.read)
    parts = []
    count = 0
    while True:
        remaining = remaining_time(deadline, 'response_body')
        set_timeout = getattr(response, 'set_read_timeout', None)
        if set_timeout is not None:
            if response.isclosed():
                return b''.join(parts)
            set_timeout(remaining)
        try:
            chunk = reader(min(65536, limit - count + 1))
        except TimeoutError:
            raise ProviderTimeout('response_body') from None
        remaining_time(deadline, 'response_body')
        if not chunk:
            return b''.join(parts)
        count += len(chunk)
        if count > limit:
            raise ValueError('Provider response exceeds size limit')
        parts.append(chunk)
