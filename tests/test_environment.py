from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest
from briq_cloud import BriqError
from inspect_ai.util import (
    OutputLimitExceededError,
    SandboxEnvironmentSpec,
    SandboxUnavailableError,
    SandboxUserUnsupportedError,
)

from inspect_briq import BriqSandboxEnvironment, BriqSandboxEnvironmentConfig

from .conftest import FakeClient


async def start(config: BriqSandboxEnvironmentConfig | None = None) -> BriqSandboxEnvironment:
    environments = await BriqSandboxEnvironment.sample_init("task", config, {})
    environment = environments["default"]
    assert isinstance(environment, BriqSandboxEnvironment)
    return environment


def result(
    stdout: str = "", stderr: str = "", code: int | None = 0, timed_out: bool = False
) -> Any:
    return SimpleNamespace(stdout=stdout, stderr=stderr, exit_code=code, timed_out=timed_out)


# --- lifecycle ---


async def test_a_sample_gets_one_default_sandbox(fake_client: type[FakeClient]) -> None:
    config = BriqSandboxEnvironmentConfig(
        size="large", image="ghcr.io/acme/base:1", env={"MODE": "test"}, sleep="3h"
    )
    environments = await BriqSandboxEnvironment.sample_init("task", config, {})
    assert list(environments) == ["default"]
    (client,) = fake_client.instances
    assert client.created == [
        {
            "size": "large",
            "image": "ghcr.io/acme/base:1",
            "env": {"MODE": "test"},
            "secret_env": None,
            "sleep": "3h",
            "wait_timeout": 120,
        }
    ]


async def test_each_sample_gets_its_own_sandbox(fake_client: type[FakeClient]) -> None:
    await start()
    await start()
    assert len(fake_client.instances) == 2
    assert fake_client.instances[0].sandbox is not fake_client.instances[1].sandbox


async def test_secret_env_is_read_from_the_process(
    fake_client: type[FakeClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MY_TOKEN", "s3cret")
    config = BriqSandboxEnvironmentConfig(secret_env=["MY_TOKEN"])
    await start(config)
    assert fake_client.instances[0].created[0]["secret_env"] == {"MY_TOKEN": "s3cret"}
    assert "s3cret" not in config.model_dump_json()
    assert "s3cret" not in SandboxEnvironmentSpec("briq", config).model_dump_json()


async def test_a_missing_secret_variable_fails_before_any_request(
    fake_client: type[FakeClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NOPE", raising=False)
    with pytest.raises(BriqError, match="NOPE") as error:
        await start(BriqSandboxEnvironmentConfig(secret_env=["NOPE"]))
    assert error.value.code == "invalid_argument"
    assert fake_client.instances == []


async def test_a_bad_size_fails_before_any_request(fake_client: type[FakeClient]) -> None:
    with pytest.raises(BriqError) as error:
        await start(BriqSandboxEnvironmentConfig(size="tiny"))
    assert error.value.code == "unsupported"
    assert fake_client.instances == []


async def test_a_failed_create_closes_the_client(
    fake_client: type[FakeClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    original = FakeClient.__init__

    def init(self: FakeClient, *args: Any, **kwargs: Any) -> None:
        original(self, *args, **kwargs)
        self.create_error = BriqError("quota", "no credits")

    monkeypatch.setattr(FakeClient, "__init__", init)
    with pytest.raises(BriqError) as error:
        await start()
    assert error.value.code == "quota"
    assert fake_client.instances[0].closed


async def test_cleanup_destroys_the_sandbox_and_closes_the_client(
    fake_client: type[FakeClient],
) -> None:
    environments = await BriqSandboxEnvironment.sample_init("task", None, {})
    await BriqSandboxEnvironment.sample_cleanup("task", None, environments, False)
    client = fake_client.instances[0]
    assert client.sandbox.destroyed
    assert client.closed


async def test_a_failed_destroy_does_not_fail_the_sample(
    fake_client: type[FakeClient], caplog: pytest.LogCaptureFixture
) -> None:
    environments = await BriqSandboxEnvironment.sample_init("task", None, {})
    client = fake_client.instances[0]
    client.sandbox.destroy_error = BriqError("unavailable", "down")
    with caplog.at_level(logging.WARNING):
        await BriqSandboxEnvironment.sample_cleanup("task", None, environments, True)
    assert "bq_test" in caplog.text
    assert client.closed


async def test_task_init_needs_a_key(
    fake_client: type[FakeClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    await BriqSandboxEnvironment.task_init("task", None)
    assert fake_client.instances[0].closed
    monkeypatch.delenv("BRIQ_API_KEY")
    with pytest.raises(BriqError) as error:
        await BriqSandboxEnvironment.task_init("task", None)
    assert error.value.code == "auth"


async def test_task_init_rejects_a_bad_configuration() -> None:
    with pytest.raises(BriqError) as error:
        await BriqSandboxEnvironment.task_init("task", BriqSandboxEnvironmentConfig(size="x"))
    assert error.value.code == "unsupported"


def test_config_round_trips() -> None:
    config = BriqSandboxEnvironmentConfig(size="medium", env={"A": "1"})
    again = BriqSandboxEnvironment.config_deserialize(config.model_dump())
    assert again == config


# --- exec ---


async def test_exec_sends_the_command_in_the_working_directory(
    fake_client: type[FakeClient],
) -> None:
    environment = await start()
    fake_client.instances[0].sandbox.next_exec = result("hi\n", "warn", 3)
    out = await environment.exec(["echo", "hi"], env={"A": "1"}, timeout=5)
    assert (out.success, out.returncode, out.stdout, out.stderr) == (False, 3, "hi\n", "warn")
    (call,) = fake_client.instances[0].sandbox.execs
    assert call == {
        "command": ["echo", "hi"],
        "cwd": "/work",
        "env": {"A": "1"},
        "stdin": None,
        "timeout": 5.0,
    }


async def test_exec_success_and_relative_cwd(fake_client: type[FakeClient]) -> None:
    environment = await start()
    out = await environment.exec(["ls"], cwd="sub/dir", input="data")
    assert out.success
    call = fake_client.instances[0].sandbox.execs[0]
    assert call["cwd"] == "/work/sub/dir"
    assert call["stdin"] == "data"
    await environment.exec(["ls"], cwd="/tmp")
    assert fake_client.instances[0].sandbox.execs[1]["cwd"] == "/tmp"


async def test_exec_without_timeout_uses_the_default(fake_client: type[FakeClient]) -> None:
    environment = await start(
        BriqSandboxEnvironmentConfig(default_exec_timeout=90, working_dir="/w")
    )
    await environment.exec(["true"])
    call = fake_client.instances[0].sandbox.execs[0]
    assert (call["timeout"], call["cwd"], call["env"]) == (90.0, "/w", None)


async def test_exec_takes_utf8_bytes_as_input(fake_client: type[FakeClient]) -> None:
    environment = await start()
    await environment.exec(["cat"], input="héllo".encode())
    assert fake_client.instances[0].sandbox.execs[0]["stdin"] == "héllo"


async def test_exec_refuses_input_that_is_not_text(fake_client: type[FakeClient]) -> None:
    environment = await start()
    with pytest.raises(BriqError) as error:
        await environment.exec(["cat"], input=b"\xff\xfe")
    assert error.value.code == "unsupported"
    assert fake_client.instances[0].sandbox.execs == []


async def test_exec_refuses_a_user(fake_client: type[FakeClient]) -> None:
    environment = await start()
    with pytest.raises(SandboxUserUnsupportedError):
        await environment.exec(["id"], user="root")
    assert fake_client.instances[0].sandbox.execs == []


async def test_a_command_past_its_timeout_raises_timeout_error(
    fake_client: type[FakeClient],
) -> None:
    environment = await start()
    fake_client.instances[0].sandbox.next_exec = result(code=None, timed_out=True)
    with pytest.raises(TimeoutError):
        await environment.exec(["sleep", "99"], timeout=1)


async def test_a_command_without_an_exit_status_is_an_error(
    fake_client: type[FakeClient],
) -> None:
    environment = await start()
    fake_client.instances[0].sandbox.next_exec = result(code=None)
    with pytest.raises(RuntimeError):
        await environment.exec(["x"])


@pytest.mark.parametrize("code", ["unavailable", "not_found"])
async def test_a_sandbox_that_cannot_take_the_call_is_unavailable(
    fake_client: type[FakeClient], code: Any
) -> None:
    environment = await start()
    fake_client.instances[0].sandbox.next_exec = BriqError(code, "down")
    with pytest.raises(SandboxUnavailableError):
        await environment.exec(["x"])


async def test_other_briq_errors_are_not_hidden(fake_client: type[FakeClient]) -> None:
    environment = await start()
    fake_client.instances[0].sandbox.next_exec = BriqError("outcome_unknown", "no answer")
    with pytest.raises(BriqError) as error:
        await environment.exec(["x"])
    assert error.value.code == "outcome_unknown"
    assert len(fake_client.instances[0].sandbox.execs) == 1


async def test_output_over_the_limit_raises(
    fake_client: type[FakeClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INSPECT_SANDBOX_MAX_EXEC_OUTPUT_SIZE", "10")
    from inspect_ai.util._sandbox.limits import reset_sandbox_limits, set_sandbox_limits

    tokens = set_sandbox_limits()
    try:
        environment = await start()
        fake_client.instances[0].sandbox.next_exec = result("x" * 11)
        with pytest.raises(OutputLimitExceededError):
            await environment.exec(["x"])
    finally:
        reset_sandbox_limits(tokens)


# --- files ---


async def test_write_and_read_files(fake_client: type[FakeClient]) -> None:
    environment = await start()
    await environment.write_file("notes/a.txt", "héllo\r\nworld")
    await environment.write_file("/tmp/b.bin", b"\x00\xff")
    files = fake_client.instances[0].sandbox.files
    assert files["/work/notes/a.txt"] == "héllo\r\nworld".encode()
    assert files["/tmp/b.bin"] == b"\x00\xff"
    assert await environment.read_file("notes/a.txt") == "héllo\r\nworld"  # newlines kept
    assert await environment.read_file("/tmp/b.bin", text=False) == b"\x00\xff"


async def test_a_missing_file_is_file_not_found() -> None:
    environment = await start()
    with pytest.raises(FileNotFoundError):
        await environment.read_file("nope.txt")


async def test_a_file_that_is_not_text_fails_to_decode(fake_client: type[FakeClient]) -> None:
    environment = await start()
    await environment.write_file("b", b"\xff")
    with pytest.raises(UnicodeDecodeError):
        await environment.read_file("b")


async def test_file_calls_refuse_a_user() -> None:
    environment = await start()
    with pytest.raises(SandboxUserUnsupportedError):
        await environment.read_file("a", user="root")
    with pytest.raises(SandboxUserUnsupportedError):
        await environment.write_file("a", "x", user="root")


async def test_a_file_call_to_a_gone_sandbox_is_unavailable(fake_client: type[FakeClient]) -> None:
    environment = await start()

    async def gone(path: str) -> bytes:
        raise BriqError("not_found", "no such sandbox")

    fake_client.instances[0].sandbox.read_file = gone  # type: ignore[method-assign]
    with pytest.raises(SandboxUnavailableError):
        await environment.read_file("a")
