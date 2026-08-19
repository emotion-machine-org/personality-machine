"""Behavior decorator and deployment utilities."""

from __future__ import annotations

import inspect
import textwrap
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from cron_validator import CronValidator

if TYPE_CHECKING:
    import httpx

# Global registry of decorated behaviors
_behavior_registry: dict[str, BehaviorSpec] = {}


@dataclass
class BehaviorSpec:
    """Specification for a behavior."""

    key: str
    func: Callable
    source_code: str
    triggers: list[str]
    priority: bool
    enabled: bool
    classifier_eligible: bool
    metadata: dict[str, Any] = field(default_factory=dict)


class BehaviorValidationError(ValueError):
    """Raised when behavior configuration is invalid."""
    pass


def _validate_triggers(triggers: list[str], behavior_key: str) -> None:
    """Validate trigger syntax at decoration time.

    Args:
        triggers: List of trigger strings to validate
        behavior_key: Name of the behavior (for error messages)

    Raises:
        BehaviorValidationError: If any trigger is invalid

    Supported formats:
        - "always": Runs on every message
        - "every:N": Runs every Nth message (N must be positive integer)
        - "turn:1,5,10": Runs on specific turn numbers (comma-separated positive integers)
        - "keyword:word1,word2": Runs when keywords detected (comma-separated words)
        - "cron:0 0 * * *": Runs on cron schedule (valid cron expression)
        - "idle:N": Runs after N minutes of inactivity (N must be positive integer)
    """
    for trigger in triggers:
        trigger = trigger.strip()

        if not trigger:
            raise BehaviorValidationError(
                f"Behavior '{behavior_key}': Empty trigger string is not allowed"
            )

        # Special case: "always" has no colon separator
        if trigger == "always":
            continue

        # All other triggers must have format "type:value"
        if ":" not in trigger:
            raise BehaviorValidationError(
                f"Behavior '{behavior_key}': Invalid trigger '{trigger}'\n"
                f"Expected format: 'type:value' (e.g., 'every:5', 'cron:0 9 * * *')\n"
                f"Valid trigger formats:\n"
                f"  - 'always': Run on every message\n"
                f"  - 'every:N': Run every Nth message\n"
                f"  - 'turn:1,2,3': Run on specific turn numbers\n"
                f"  - 'keyword:word1,word2': Run when keywords detected\n"
                f"  - 'cron:MIN HOUR DAY MONTH WEEKDAY': Run on schedule\n"
                f"  - 'idle:N': Run after N minutes of inactivity"
            )

        # Split into type and value
        trigger_type, trigger_value = trigger.split(":", 1)
        trigger_type = trigger_type.strip().lower()
        trigger_value = trigger_value.strip()

        # Validate based on trigger type using match statement
        match trigger_type:
            case "every":
                _validate_every_trigger(trigger, trigger_value, behavior_key)

            case "turn":
                _validate_turn_trigger(trigger, trigger_value, behavior_key)

            case "keyword":
                _validate_keyword_trigger(trigger, trigger_value, behavior_key)

            case "cron":
                _validate_cron_trigger(trigger, trigger_value, behavior_key)

            case "idle":
                _validate_idle_trigger(trigger, trigger_value, behavior_key)

            case _:
                raise BehaviorValidationError(
                    f"Behavior '{behavior_key}': Unknown trigger type: '{trigger_type}'\n"
                    f"Valid trigger types: always, every, turn, keyword, cron, idle"
                )


def _validate_every_trigger(trigger: str, value: str, behavior_key: str) -> None:
    """Validate 'every:N' trigger format."""
    try:
        count = int(value)
        if count < 1:
            raise ValueError("Count must be positive")
    except ValueError as e:
        raise BehaviorValidationError(
            f"Behavior '{behavior_key}': Invalid trigger '{trigger}'\n"
            f"Expected format: 'every:N' where N is a positive integer\n"
            f"Example: 'every:5' (runs every 5th message)\n"
            f"Error: {e}"
        ) from e


def _validate_turn_trigger(trigger: str, value: str, behavior_key: str) -> None:
    """Validate 'turn:1,5,10' trigger format."""
    try:
        turn_nums = [int(t.strip()) for t in value.split(",")]
        if not turn_nums:
            raise ValueError("At least one turn number required")
        if any(t < 1 for t in turn_nums):
            raise ValueError("Turn numbers must be positive")
    except ValueError as e:
        raise BehaviorValidationError(
            f"Behavior '{behavior_key}': Invalid trigger '{trigger}'\n"
            f"Expected format: 'turn:1,2,3' with comma-separated positive integers\n"
            f"Example: 'turn:1,5,10' (runs on turns 1, 5, and 10)\n"
            f"Error: {e}"
        ) from e


def _validate_keyword_trigger(trigger: str, value: str, behavior_key: str) -> None:
    """Validate 'keyword:word1,word2' trigger format."""
    try:
        keywords = [k.strip() for k in value.split(",")]
        if not keywords or all(not k for k in keywords):
            raise ValueError("At least one keyword required")
    except ValueError as e:
        raise BehaviorValidationError(
            f"Behavior '{behavior_key}': Invalid trigger '{trigger}'\n"
            f"Expected format: 'keyword:word1,word2' with comma-separated keywords\n"
            f"Example: 'keyword:help,urgent,crisis'\n"
            f"Error: {e}"
        ) from e


def _validate_cron_trigger(trigger: str, value: str, behavior_key: str) -> None:
    """Validate 'cron:...' trigger format using cron-validator library."""
    if not value:
        raise BehaviorValidationError(
            f"Behavior '{behavior_key}': Invalid trigger '{trigger}'\n"
            f"Cron expression cannot be empty"
        )

    try:
        # CronValidator.parse() validates and raises ValueError if invalid
        CronValidator.parse(value)

    except ValueError as e:
        raise BehaviorValidationError(
            f"Behavior '{behavior_key}': Invalid trigger '{trigger}'\n"
            f"Invalid cron expression: '{value}'\n"
            f"Expected format: 'cron:MIN HOUR DAY MONTH WEEKDAY'\n"
            f"Example: 'cron:0 9 * * *' (daily at 9 AM)\n"
            f"Example: 'cron:0 0 * * 0' (weekly on Sunday midnight)\n"
            f"Error: {e}"
        ) from e


def _validate_idle_trigger(trigger: str, value: str, behavior_key: str) -> None:
    """Validate 'idle:N' trigger format."""
    try:
        minutes = int(value)
        if minutes < 1:
            raise ValueError("Idle minutes must be positive")
    except ValueError as e:
        raise BehaviorValidationError(
            f"Behavior '{behavior_key}': Invalid trigger '{trigger}'\n"
            f"Expected format: 'idle:N' where N is positive integer (minutes)\n"
            f"Example: 'idle:30' (runs after 30 minutes of inactivity)\n"
            f"Error: {e}"
        ) from e


def behavior(
    triggers: list[str] | None = None,
    *,
    priority: bool = False,
    enabled: bool = True,
    classifier_eligible: bool = True,
    key: str | None = None,
) -> Callable:
    """Decorator to register a behavior function.

    Args:
        triggers: List of trigger conditions (e.g., ["always"], ["idle:30"], ["every:5"])
        priority: If True, runs synchronously before LLM response
        enabled: If True, behavior is active
        classifier_eligible: If True, behavior can be selected by classifier
        key: Custom behavior key (defaults to function name)

    Trigger formats:
        - "always": Runs on every message
        - "every:N": Runs every Nth message
        - "turn:1,5,10": Runs on specific turn numbers
        - "keyword:word1,word2": Runs when keywords detected
        - "cron:0 0 * * *": Runs on cron schedule
        - "idle:30": Runs after 30 minutes of inactivity

    Usage:
        @behavior(triggers=["always"], priority=True)
        async def mood_tracker(ctx):
            if "anxious" in ctx.message.lower():
                ctx.profile.set("user.mood", "anxious")
                return "User seems anxious."

        @behavior(triggers=["idle:30"])
        async def idle_checkin(ctx):
            ctx.send_message("Hey! Just checking in.")
    """
    triggers = triggers or []

    def decorator(func: Callable) -> Callable:
        behavior_key = key or func.__name__

        try:
            _validate_triggers(triggers, behavior_key)
        except BehaviorValidationError:
            raise
        except Exception as e:
            raise BehaviorValidationError(
                f"Behavior '{behavior_key}': Unexpected error validating triggers: {e}"
            ) from e

        # Extract source code
        try:
            source = inspect.getsource(func)
            # Dedent to handle indented functions
            source = textwrap.dedent(source)
            # Remove the decorator lines
            lines = source.split("\n")
            # Find the first line that starts with 'async def' or 'def'
            start_idx = 0
            for i, line in enumerate(lines):
                stripped = line.lstrip()
                if stripped.startswith("async def ") or stripped.startswith("def "):
                    start_idx = i
                    break
            source = "\n".join(lines[start_idx:])
        except (OSError, TypeError):
            # If we can't get source, use a placeholder
            source = f"# Source unavailable for {behavior_key}"

        spec = BehaviorSpec(
            key=behavior_key,
            func=func,
            source_code=source,
            triggers=triggers,
            priority=priority,
            enabled=enabled,
            classifier_eligible=classifier_eligible,
        )

        _behavior_registry[behavior_key] = spec

        # Return the original function unchanged
        return func

    return decorator


def get_registered_behaviors() -> dict[str, BehaviorSpec]:
    """Get all registered behaviors."""
    return _behavior_registry.copy()


def clear_behavior_registry() -> None:
    """Clear the behavior registry (useful for testing)."""
    _behavior_registry.clear()


class BehaviorAPI:
    """API for managing behaviors."""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        base_url: str,
        headers: dict[str, str],
    ) -> None:
        self._http_client = http_client
        self._base_url = base_url
        self._headers = headers

    async def deploy(
        self,
        companion_id: str,
        *,
        behaviors: dict[str, BehaviorSpec] | None = None,
        delete_existing: bool = False,
    ) -> list[dict[str, Any]]:
        """Deploy registered behaviors to a companion.

        Args:
            companion_id: The companion to deploy to
            behaviors: Specific behaviors to deploy (defaults to all registered)
            delete_existing: If True, delete existing behaviors before deploying

        Returns:
            List of created/updated behavior responses
        """
        from .exceptions import APIError

        behaviors = behaviors or get_registered_behaviors()
        results = []

        for key, spec in behaviors.items():
            # Delete existing behavior if requested or to avoid conflicts
            if delete_existing:
                try:
                    await self._http_client.delete(
                        f"{self._base_url}/v2/companions/{companion_id}/behaviors/{key}",
                        headers=self._headers,
                    )
                except Exception:
                    pass

            # Create behavior link
            response = await self._http_client.post(
                f"{self._base_url}/v2/companions/{companion_id}/behaviors",
                headers=self._headers,
                json={
                    "behavior_key": key,
                    "triggers": spec.triggers,
                    "priority": spec.priority,
                    "enabled": spec.enabled,
                    "classifier_eligible": spec.classifier_eligible,
                },
            )

            if response.status_code >= 400 and response.status_code != 409:
                raise APIError(
                    f"Failed to create behavior {key}: {response.text}",
                    status_code=response.status_code,
                )

            # Update source code
            patch_response = await self._http_client.patch(
                f"{self._base_url}/v2/companions/{companion_id}/behaviors/{key}/definition",
                headers=self._headers,
                json={"source_code": spec.source_code},
            )

            if patch_response.status_code >= 400:
                raise APIError(
                    f"Failed to update behavior source {key}: {patch_response.text}",
                    status_code=patch_response.status_code,
                )

            results.append(patch_response.json())

        return results

    async def create(
        self,
        companion_id: str,
        behavior_key: str,
        source_code: str,
        *,
        triggers: list[str] | None = None,
        priority: bool = False,
        enabled: bool = True,
        classifier_eligible: bool = True,
    ) -> dict[str, Any]:
        """Create a single behavior.

        Args:
            companion_id: The companion to attach the behavior to
            behavior_key: Unique key for the behavior
            source_code: Python source code for the behavior
            triggers: List of trigger conditions
            priority: If True, runs synchronously
            enabled: If True, behavior is active
            classifier_eligible: If True, can be selected by classifier

        Returns:
            Behavior response
        """
        from .exceptions import APIError

        # Create behavior link
        response = await self._http_client.post(
            f"{self._base_url}/v2/companions/{companion_id}/behaviors",
            headers=self._headers,
            json={
                "behavior_key": behavior_key,
                "triggers": triggers or [],
                "priority": priority,
                "enabled": enabled,
                "classifier_eligible": classifier_eligible,
            },
        )

        if response.status_code >= 400:
            raise APIError(
                f"Failed to create behavior {behavior_key}: {response.text}",
                status_code=response.status_code,
            )

        # Update source code
        patch_response = await self._http_client.patch(
            f"{self._base_url}/v2/companions/{companion_id}/behaviors/{behavior_key}/definition",
            headers=self._headers,
            json={"source_code": source_code},
        )

        if patch_response.status_code >= 400:
            raise APIError(
                f"Failed to update behavior source {behavior_key}: {patch_response.text}",
                status_code=patch_response.status_code,
            )

        return patch_response.json()

    async def get(self, companion_id: str, behavior_key: str) -> dict[str, Any]:
        """Get a behavior definition."""
        from .exceptions import APIError

        response = await self._http_client.get(
            f"{self._base_url}/v2/companions/{companion_id}/behaviors/{behavior_key}",
            headers=self._headers,
        )

        if response.status_code >= 400:
            raise APIError(
                f"Failed to get behavior {behavior_key}: {response.text}",
                status_code=response.status_code,
            )

        return response.json()

    async def list(self, companion_id: str) -> list[dict[str, Any]]:
        """List all behaviors for a companion."""
        from .exceptions import APIError

        response = await self._http_client.get(
            f"{self._base_url}/v2/companions/{companion_id}/behaviors",
            headers=self._headers,
        )

        if response.status_code >= 400:
            raise APIError(
                f"Failed to list behaviors: {response.text}",
                status_code=response.status_code,
            )

        return response.json().get("behaviors", [])

    async def delete(self, companion_id: str, behavior_key: str) -> None:
        """Delete a behavior."""
        from .exceptions import APIError

        response = await self._http_client.delete(
            f"{self._base_url}/v2/companions/{companion_id}/behaviors/{behavior_key}",
            headers=self._headers,
        )

        if response.status_code >= 400:
            raise APIError(
                f"Failed to delete behavior {behavior_key}: {response.text}",
                status_code=response.status_code,
            )

    async def update(
        self,
        companion_id: str,
        behavior_key: str,
        *,
        source_code: str | None = None,
        triggers: list[str] | None = None,
        priority: bool | None = None,
        enabled: bool | None = None,
    ) -> dict[str, Any]:
        """Update a behavior.

        Args:
            companion_id: The companion ID
            behavior_key: The behavior key
            source_code: New source code (if updating)
            triggers: New triggers (if updating)
            priority: New priority setting (if updating)
            enabled: New enabled setting (if updating)

        Returns:
            Updated behavior response
        """
        from .exceptions import APIError

        # Update link settings if provided
        if triggers is not None or priority is not None or enabled is not None:
            link_payload: dict[str, Any] = {}
            if triggers is not None:
                link_payload["triggers"] = triggers
            if priority is not None:
                link_payload["priority"] = priority
            if enabled is not None:
                link_payload["enabled"] = enabled

            response = await self._http_client.patch(
                f"{self._base_url}/v2/companions/{companion_id}/behaviors/{behavior_key}",
                headers=self._headers,
                json=link_payload,
            )

            if response.status_code >= 400:
                raise APIError(
                    f"Failed to update behavior link {behavior_key}: {response.text}",
                    status_code=response.status_code,
                )

        # Update source code if provided
        if source_code is not None:
            response = await self._http_client.patch(
                f"{self._base_url}/v2/companions/{companion_id}/behaviors/{behavior_key}/definition",
                headers=self._headers,
                json={"source_code": source_code},
            )

            if response.status_code >= 400:
                raise APIError(
                    f"Failed to update behavior source {behavior_key}: {response.text}",
                    status_code=response.status_code,
                )

        return await self.get(companion_id, behavior_key)

    async def test(
        self,
        companion_id: str,
        behavior_key: str,
        *,
        message: str | None = None,
        profile: dict[str, Any] | None = None,
        messages: list[dict[str, Any]] | None = None,
        relationship_id: str | None = None,
    ) -> dict[str, Any]:
        """Test a behavior by triggering it via the API.

        Note: This triggers the behavior in production Modal sandbox, not a local test.
        For local testing during development, use the web dashboard at /behaviors/test.

        Args:
            companion_id: The companion ID
            behavior_key: The behavior to test
            message: Send a message first to provide context (optional)
            profile: Profile data to set before triggering (optional)
            messages: Not used (kept for API compatibility)
            relationship_id: Relationship ID to use (creates temp if not provided)

        Returns:
            Trigger result with job_id
        """
        from .exceptions import APIError

        # If we have a relationship_id, trigger directly
        if relationship_id:
            # Set profile if provided
            if profile:
                response = await self._http_client.patch(
                    f"{self._base_url}/v2/relationships/{relationship_id}/profile",
                    headers=self._headers,
                    json=profile,
                )
                if response.status_code >= 400:
                    raise APIError(
                        f"Failed to set profile: {response.text}",
                        status_code=response.status_code,
                    )

            # Send message if provided to give context
            if message:
                response = await self._http_client.post(
                    f"{self._base_url}/v2/relationships/{relationship_id}/messages",
                    headers=self._headers,
                    json={"content": message},
                )
                if response.status_code >= 400:
                    raise APIError(
                        f"Failed to send message: {response.text}",
                        status_code=response.status_code,
                    )

            # Trigger the behavior
            response = await self._http_client.post(
                f"{self._base_url}/v2/relationships/{relationship_id}/behaviors/{behavior_key}/trigger",
                headers=self._headers,
                json={},
            )
        else:
            # Create a temporary relationship for testing
            test_user_id = f"sdk-test-{behavior_key}"
            response = await self._http_client.put(
                f"{self._base_url}/v2/companions/{companion_id}/relationships/{test_user_id}",
                headers=self._headers,
                json={},
            )
            if response.status_code >= 400:
                raise APIError(
                    f"Failed to create test relationship: {response.text}",
                    status_code=response.status_code,
                )
            rel_id = response.json()["id"]

            # Recursively call with relationship_id
            return await self.test(
                companion_id,
                behavior_key,
                message=message,
                profile=profile,
                relationship_id=rel_id,
            )

        if response.status_code >= 400:
            raise APIError(
                f"Failed to trigger behavior {behavior_key}: {response.text}",
                status_code=response.status_code,
            )

        return response.json()
