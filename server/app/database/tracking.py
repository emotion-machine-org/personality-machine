"""Session tracking for debugging and leak detection.

Inspired by Netflix Dispatch's SessionTracker pattern.
Tracks all active database sessions to help identify:
- Connection leaks
- Long-running queries
- Session lifecycle issues
"""

import logging
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List
from uuid import uuid4

logger = logging.getLogger(__name__)


@dataclass
class TrackedSession:
    """Information about a tracked database session."""

    session_id: str
    context: str
    created_at: float = field(default_factory=time.time)
    stack_trace: str = ""
    last_activity: float = field(default_factory=time.time)
    query_count: int = 0

    @property
    def age_seconds(self) -> float:
        """Time since session was created."""
        return time.time() - self.created_at

    @property
    def idle_seconds(self) -> float:
        """Time since last activity."""
        return time.time() - self.last_activity

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for logging/debugging."""
        return {
            "session_id": self.session_id,
            "context": self.context,
            "age_ms": int(self.age_seconds * 1000),
            "idle_ms": int(self.idle_seconds * 1000),
            "query_count": self.query_count,
            "stack_trace": self.stack_trace[:500] if self.stack_trace else None,
        }


class SessionTracker:
    """Thread-safe tracker for database sessions.

    Usage:
        # Track a session
        session_id = SessionTracker.track_session(session, context="api_request")

        # Record activity
        SessionTracker.record_activity(session_id)

        # Untrack when done
        SessionTracker.untrack_session(session_id)

        # Get stats
        stats = SessionTracker.get_stats()
    """

    _sessions: ClassVar[Dict[str, TrackedSession]] = {}
    _lock: ClassVar[threading.Lock] = threading.Lock()

    # Thresholds for warnings
    WARN_SESSION_AGE_SECONDS = 60  # Warn if session lives > 60s
    WARN_IDLE_SECONDS = 30  # Warn if session idle > 30s
    MAX_TRACKED_SESSIONS = 1000  # Prevent unbounded growth

    @classmethod
    def track_session(
        cls,
        _session: Any,  # Session object - kept for future use (e.g., event listeners)
        context: str = "unknown",
        capture_stack: bool = True,
    ) -> str:
        """Start tracking a database session.

        Args:
            _session: The SQLAlchemy session object (for future event listeners)
            context: Description of where session was created (e.g., "api_request")
            capture_stack: Whether to capture stack trace for debugging

        Returns:
            Unique session ID for tracking
        """
        session_id = str(uuid4())

        stack_trace = ""
        if capture_stack:
            stack_trace = "".join(traceback.format_stack(limit=10))

        tracked = TrackedSession(
            session_id=session_id,
            context=context,
            stack_trace=stack_trace,
        )

        with cls._lock:
            # Prevent unbounded growth
            if len(cls._sessions) >= cls.MAX_TRACKED_SESSIONS:
                logger.warning(
                    "SessionTracker at capacity (%d sessions). Possible session leak detected.",
                    len(cls._sessions),
                )
                # Log oldest sessions for debugging
                oldest = sorted(cls._sessions.values(), key=lambda s: s.created_at)[:5]
                for s in oldest:
                    logger.warning("Long-lived session: %s", s.to_dict())

            cls._sessions[session_id] = tracked

        logger.debug(
            "Tracking session %s (context=%s, total=%d)",
            session_id[:8],
            context,
            len(cls._sessions),
        )

        return session_id

    @classmethod
    def untrack_session(cls, session_id: str) -> TrackedSession | None:
        """Stop tracking a session.

        Args:
            session_id: The session ID returned from track_session

        Returns:
            The tracked session info, or None if not found
        """
        with cls._lock:
            tracked = cls._sessions.pop(session_id, None)

        if tracked:
            # Warn if session was unusually long-lived
            if tracked.age_seconds > cls.WARN_SESSION_AGE_SECONDS:
                logger.warning(
                    "Long-lived session closed after %.1fs: %s",
                    tracked.age_seconds,
                    tracked.to_dict(),
                )
            else:
                logger.debug(
                    "Untracked session %s (age=%.1fs, queries=%d)",
                    session_id[:8],
                    tracked.age_seconds,
                    tracked.query_count,
                )

        return tracked

    @classmethod
    def record_activity(cls, session_id: str) -> None:
        """Record activity on a session (resets idle timer)."""
        with cls._lock:
            if session_id in cls._sessions:
                cls._sessions[session_id].last_activity = time.time()
                cls._sessions[session_id].query_count += 1

    @classmethod
    def get_session(cls, session_id: str) -> TrackedSession | None:
        """Get info about a tracked session."""
        with cls._lock:
            return cls._sessions.get(session_id)

    @classmethod
    def get_all_sessions(cls) -> List[TrackedSession]:
        """Get all currently tracked sessions."""
        with cls._lock:
            return list(cls._sessions.values())

    @classmethod
    def get_stats(cls) -> Dict[str, Any]:
        """Get aggregate statistics about tracked sessions."""
        with cls._lock:
            sessions = list(cls._sessions.values())

        if not sessions:
            return {
                "total_sessions": 0,
                "by_context": {},
            }

        by_context: Dict[str, int] = {}
        total_age = 0.0
        max_age = 0.0
        long_lived = 0

        for s in sessions:
            by_context[s.context] = by_context.get(s.context, 0) + 1
            total_age += s.age_seconds
            max_age = max(max_age, s.age_seconds)
            if s.age_seconds > cls.WARN_SESSION_AGE_SECONDS:
                long_lived += 1

        return {
            "total_sessions": len(sessions),
            "by_context": by_context,
            "avg_age_seconds": total_age / len(sessions) if sessions else 0,
            "max_age_seconds": max_age,
            "long_lived_count": long_lived,
        }

    @classmethod
    def clear_all(cls) -> int:
        """Clear all tracked sessions. Returns count of cleared sessions."""
        with cls._lock:
            count = len(cls._sessions)
            cls._sessions.clear()
        return count

    @classmethod
    def cleanup_stale(cls, max_age_seconds: float = 300) -> List[TrackedSession]:
        """Remove and return sessions older than threshold.

        Useful for periodic cleanup of leaked sessions.
        """
        stale = []
        with cls._lock:
            stale_ids = [sid for sid, s in cls._sessions.items() if s.age_seconds > max_age_seconds]
            for sid in stale_ids:
                stale.append(cls._sessions.pop(sid))

        if stale:
            logger.warning(
                "Cleaned up %d stale sessions (age > %ds)",
                len(stale),
                max_age_seconds,
            )
            for s in stale:
                logger.warning("Stale session: %s", s.to_dict())

        return stale
