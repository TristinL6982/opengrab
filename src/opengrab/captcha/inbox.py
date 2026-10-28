import time
import uuid
import threading
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class CaptchaRequest:
    id: str
    host: str
    kind: str
    sitekey: Optional[str]
    page_url: str
    created_at: float
    timeout_s: float = 300.0
    status: str = "pending"
    answer: Optional[str] = None

    def is_expired(self, now=None) -> bool:
        if now is None:
            now = time.time()
        return now >= self.created_at + self.timeout_s

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'host': self.host,
            'kind': self.kind,
            'sitekey': self.sitekey,
            'page_url': self.page_url,
            'created_at': self.created_at,
            'timeout_s': self.timeout_s,
            'status': self.status,
            'answer': self.answer
        }


class InboxFull(Exception):
    """Raised when the captcha inbox reaches its maximum capacity."""
    pass


class CaptchaInbox:
    def __init__(self, max_pending: int = 50):
        self._max_pending = max_pending
        self._requests: dict[str, CaptchaRequest] = {}
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)

    def submit(self, host: str, kind: str, page_url: str, sitekey=None, timeout_s=300.0, now=None) -> CaptchaRequest:
        if now is None:
            now = time.time()
        
        # Check for existing pending request with same host and kind
        with self._lock:
            for request in self._requests.values():
                if (request.host == host and 
                    request.kind == kind and 
                    request.status == "pending" and 
                    not request.is_expired(now)):
                    return request
            
            # Create new request
            request_id = uuid.uuid4().hex
            request = CaptchaRequest(
                id=request_id,
                host=host,
                kind=kind,
                sitekey=sitekey,
                page_url=page_url,
                created_at=now,
                timeout_s=timeout_s,
                status="pending"
            )
            
            # Check if we're at max capacity
            pending_count = sum(1 for r in self._requests.values() if r.status == "pending" and not r.is_expired(now))
            if pending_count >= self._max_pending:
                raise InboxFull("Maximum pending captcha requests reached")
                
            self._requests[request_id] = request
            
        return request

    def pending(self, now=None) -> List[CaptchaRequest]:
        if now is None:
            now = time.time()
            
        with self._lock:
            # Get pending, non-expired requests ordered by creation time
            pending_requests = []
            for request in self._requests.values():
                if request.status == "pending" and not request.is_expired(now):
                    pending_requests.append(request)
                    
            # Sort by created_at (oldest first)
            pending_requests.sort(key=lambda r: r.created_at)
            
        return pending_requests

    def get(self, id: str) -> Optional[CaptchaRequest]:
        with self._lock:
            return self._requests.get(id)

    def solve(self, id: str, answer: str) -> bool:
        with self._lock:
            request = self._requests.get(id)
            if (request is not None and 
                request.status == "pending" and 
                not request.is_expired() and
                answer):
                request.answer = answer
                request.status = "solved"
                self._condition.notify_all()
                return True
        return False

    def skip(self, id: str) -> bool:
        with self._lock:
            request = self._requests.get(id)
            if (request is not None and 
                request.status == "pending" and 
                not request.is_expired()):
                request.status = "skipped"
                self._condition.notify_all()
                return True
        return False

    def wait(self, id: str, timeout: float) -> Optional[CaptchaRequest]:
        with self._lock:
            # Check if the request exists
            request = self._requests.get(id)
            if request is None:
                return None
                
            # If it's not pending, return it as-is
            if request.status != "pending":
                return request
            
            # Wait for the condition or timeout
            start_time = time.time()
            remaining_timeout = timeout
            
            while (request.status == "pending" and 
                   not request.is_expired() and 
                   remaining_timeout > 0):
                self._condition.wait(timeout=remaining_timeout)
                remaining_timeout = timeout - (time.time() - start_time)
                
            # Return the updated status
            return self._requests.get(id)

    def purge(self, now=None) -> int:
        if now is None:
            now = time.time()
            
        with self._lock:
            initial_count = len(self._requests)
            expired_requests = []
            for request_id, request in list(self._requests.items()):
                if (request.status != "pending" or 
                    request.is_expired(now)):
                    expired_requests.append(request_id)
            
            for request_id in expired_requests:
                del self._requests[request_id]
                
        return initial_count - len(self._requests)

    def __len__(self) -> int:
        now = time.time()
        with self._lock:
            count = 0
            for request in self._requests.values():
                if request.status == "pending" and not request.is_expired(now):
                    count += 1
            return count