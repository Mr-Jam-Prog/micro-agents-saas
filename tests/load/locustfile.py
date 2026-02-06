"""Locust smoke load test."""

from locust import HttpUser, task, between


class SmokeUser(HttpUser):
    """Minimal Locust user for CI."""

    wait_time = between(1, 2)

    @task
    def health(self) -> None:
        """Hit the health endpoint."""

        self.client.get("/health")
