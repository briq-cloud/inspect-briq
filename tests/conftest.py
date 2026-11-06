"""A fake of the briq client: the tests never touch the network."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest
from briq_cloud import BriqError

from inspect_briq import _environment


@dataclass
class FakeSandbox:
    id: str = "bq_test"
    files: dict[str, bytes] = field(default_factory=dict)
    execs: list[dict[str, Any]] = field(default_factory=list)
    destroyed: bool = False
    #: What the next exec does: a result, or an error to raise.
    next_exec: Any = None
    destroy_error: BriqError | None = None

    async def exec(self, command: Any, **kwargs: Any) -> Any:
        self.execs.append({"command": command, **kwargs})
        outcome = self.next_exec or SimpleNamespace(
            stdout="ok\n", stderr="", exit_code=0, timed_out=False
        )
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def write_file(self, path: str, data: bytes, **_: Any) -> None:
        self.files[path] = data

    async def read_file(self, path: str) -> bytes:
        if path not in self.files:
            raise BriqError("file_not_found", "No such file")
        return self.files[path]

    async def destroy(self) -> None:
        if self.destroy_error:
            raise self.destroy_error
        self.destroyed = True


class FakeClient:
    instances: list[FakeClient] = []  # noqa: RUF012

    def __init__(self, api_key: str | None = None, *, base_url: str | None = None) -> None:
        import os

        if not (api_key or os.environ.get("BRIQ_API_KEY")):
            raise BriqError("auth", "A briq API key is required")
        self.base_url = base_url
        self.closed = False
        self.created: list[dict[str, Any]] = []
        self.sandbox = FakeSandbox()
        self.create_error: BaseException | None = None
        FakeClient.instances.append(self)

    async def create_sandbox(self, **kwargs: Any) -> FakeSandbox:
        self.created.append(kwargs)
        if self.create_error:
            raise self.create_error
        return self.sandbox

    async def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def fake_client(monkeypatch: pytest.MonkeyPatch) -> type[FakeClient]:
    FakeClient.instances = []
    monkeypatch.setenv("BRIQ_API_KEY", "test-key")
    monkeypatch.setattr(_environment, "AsyncBriq", FakeClient)
    return FakeClient
