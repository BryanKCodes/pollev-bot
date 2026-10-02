"""Extension point for a verified Poll Everywhere open-ended workflow.

No open-ended participant route has been confirmed for this fork. A transport
may be supplied only after its fetch and submission requests are verified.
"""

from abc import ABC, abstractmethod
from typing import Mapping

import requests


class OpenEndedTransport(ABC):
    @abstractmethod
    def fetch(self, session: requests.Session, activity_id: str,
              timeout: float) -> Mapping:
        """Fetch a payload containing explicit open-ended type metadata."""

    @abstractmethod
    def submit(self, session: requests.Session, activity_id: str, text: str,
               csrf_token: str, timeout: float) -> requests.Response:
        """Submit text through a verified route without following redirects."""
