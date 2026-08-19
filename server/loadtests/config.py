"""
Load test configuration.

Set environment variables or modify defaults here.
Supports .env file in the loadtests directory.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env file from loadtests directory
load_dotenv(Path(__file__).parent / ".env")


class LoadTestConfig:
    """Configuration for load tests."""

    def __init__(self):
        # Base URL for the API
        self.base_url = os.getenv("EM_BASE_URL", "https://api.emotionmachine.ai/api/")

        # Authentication - set via environment variable
        # Example: export EM_API_KEY="em_live_..."
        self.api_key = os.getenv("EM_API_KEY", "")

        # Test companion and user IDs
        # These should be pre-created test fixtures
        self.companion_id = os.getenv("EM_TEST_COMPANION_ID", "")
        self.test_user_prefix = os.getenv("EM_TEST_USER_PREFIX", "loadtest_user_")

        # Rate limiting awareness
        self.requests_per_second_limit = int(os.getenv("EM_RATE_LIMIT", "100"))

    @property
    def auth_header(self) -> dict[str, str]:
        """Return authorization header."""
        if not self.api_key:
            return {}
        return {"Authorization": f"Bearer {self.api_key}"}

    def validate(self) -> list[str]:
        """Validate configuration, return list of errors."""
        errors = []
        if not self.api_key:
            errors.append("EM_API_KEY environment variable is not set")
        if not self.companion_id:
            errors.append("EM_TEST_COMPANION_ID environment variable is not set")
        return errors


# Global config instance
config = LoadTestConfig()
