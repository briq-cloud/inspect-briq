"""The ``briq`` sandbox environment: one briq sandbox per sample."""

from __future__ import annotations

import errno
import logging
import os
import posixpath
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Literal, overload

from briq_cloud import AsyncBriq, AsyncSandbox, BriqError
from inspect_ai.util import (
    ExecResult,
    OutputLimitExceededError,
    SandboxEnvironment,
    SandboxEnvironmentConfigType,
    SandboxEnvironmentLimits,
    SandboxUnavailableError,
    SandboxUserUnsupportedError,
    sandboxenv,
)
from pydantic import BaseModel
from typing_extensions import override

from ._config import BriqSandboxEnvironmentConfig, resolve_config

logger = logging.getLogger(__name__)


@sandboxenv(name="briq")
class BriqSandboxEnvironment(SandboxEnvironment):
    """A sample's sandbox, a briq microVM that is destroyed when the sample ends.

    Not supported, and refused instead of ignored: ``user`` (commands run as the sandbox's own
    user), ``input`` that is not UTF-8, several sandboxes per sample, ``connection()``.
    """

    def __init__(
        self, client: AsyncBriq, sandbox: AsyncSandbox, config: BriqSandboxEnvironmentConfig
    ) -> None:
        super().__init__()
        self._client = client
        self._sandbox = sandbox
        self._config = config

    @property
    def sandbox_id(self) -> str:
        return self._sandbox.id

    # --- the sandbox ---

    @override
    async def exec(
        self,
        cmd: list[str],
        input: str | bytes | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        user: str | None = None,
        timeout: int | None = None,
        timeout_retry: bool = True,
        concurrency: bool = True,
    ) -> ExecResult[str]:
        _refuse_user(user)
        stdin = _text(input) if input is not None else None
        with _inspect_errors():
            result = await self._sandbox.exec(
                cmd,
                cwd=self._path(cwd) if cwd is not None else self._config.working_dir,
                env=env or None,
                stdin=stdin,
                timeout=float(timeout or self._config.default_exec_timeout),
            )
        if result.timed_out:
            raise TimeoutError(
                f"The command did not end within {timeout or self._config.default_exec_timeout} s"
            )
        if result.exit_code is None:
            raise RuntimeError("briq did not report how the command ended")
        for stream in (result.stdout, result.stderr):
            if len(stream.encode()) > SandboxEnvironmentLimits.MAX_EXEC_OUTPUT_SIZE:
                raise OutputLimitExceededError(
                    SandboxEnvironmentLimits.MAX_EXEC_OUTPUT_SIZE_STR, stream[-4096:]
                )
        return ExecResult(
            success=result.exit_code == 0,
            returncode=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
        )

    @override
    async def write_file(
        self, file: str, contents: str | bytes, *, user: str | None = None
    ) -> None:
        _refuse_user(user)
        data = contents.encode() if isinstance(contents, str) else contents
        with _inspect_errors(file):
            await self._sandbox.write_file(self._path(file), data)

    @overload
    async def read_file(
        self, file: str, text: Literal[True] = True, *, user: str | None = None
    ) -> str: ...

    @overload
    async def read_file(
        self, file: str, text: Literal[False], *, user: str | None = None
    ) -> bytes: ...

    @override
    async def read_file(
        self, file: str, text: bool = True, *, user: str | None = None
    ) -> str | bytes:
        _refuse_user(user)
        with _inspect_errors(file):
            data = await self._sandbox.read_file(self._path(file))
        if len(data) > SandboxEnvironmentLimits.MAX_READ_FILE_SIZE:
            raise OutputLimitExceededError(SandboxEnvironmentLimits.MAX_READ_FILE_SIZE_STR, None)
        return data.decode("utf-8") if text else data

    def _path(self, path: str) -> str:
        """Relative paths resolve against ``working_dir``, as the Inspect contract says."""
        return posixpath.join(self._config.working_dir, path)

    # --- the lifecycle ---

    @override
    @classmethod
    async def task_init(cls, task_name: str, config: SandboxEnvironmentConfigType | None) -> None:
        """Fail before the first sample when the configuration or the API key is wrong."""
        resolved = resolve_config(config)
        client = AsyncBriq(base_url=resolved.base_url)  # no key, or a bad one: raises here
        await client.close()
        _secrets(resolved)

    @override
    @classmethod
    async def sample_init(
        cls,
        task_name: str,
        config: SandboxEnvironmentConfigType | None,
        metadata: dict[str, str],
    ) -> dict[str, SandboxEnvironment]:
        resolved = resolve_config(config)
        secrets = _secrets(resolved)
        client = AsyncBriq(base_url=resolved.base_url)
        try:
            sandbox = await client.create_sandbox(
                size=resolved.size,
                image=resolved.image,
                env=resolved.env or None,
                secret_env=secrets or None,
                sleep=resolved.sleep,
                wait_timeout=resolved.wait_timeout,
            )
        except BaseException:
            await _close(client)
            raise
        return {"default": cls(client, sandbox, resolved)}

    @override
    @classmethod
    async def sample_cleanup(
        cls,
        task_name: str,
        config: SandboxEnvironmentConfigType | None,
        environments: dict[str, SandboxEnvironment],
        interrupted: bool,
    ) -> None:
        for environment in environments.values():
            if not isinstance(environment, BriqSandboxEnvironment):
                continue
            try:
                await environment._sandbox.destroy()
            except Exception as error:
                # A failed cleanup must not fail the sample. An idle sandbox sleeps, unbilled.
                logger.warning(
                    "Could not destroy briq sandbox %s: %s", environment.sandbox_id, error
                )
            finally:
                await _close(environment._client)

    @override
    @classmethod
    def config_deserialize(cls, config: dict[str, Any]) -> BaseModel:
        return BriqSandboxEnvironmentConfig.model_validate(config)


def _secrets(config: BriqSandboxEnvironmentConfig) -> dict[str, str]:
    """The values of ``secret_env``, read from this process."""
    missing = [name for name in config.secret_env if name not in os.environ]
    if missing:
        raise BriqError(
            "invalid_argument", f"secret_env names variables that are not set: {', '.join(missing)}"
        )
    return {name: os.environ[name] for name in config.secret_env}


def _refuse_user(user: str | None) -> None:
    if user is not None:
        raise SandboxUserUnsupportedError(
            "briq runs commands as the sandbox's own user: it cannot run them as another"
        )


def _text(value: str | bytes) -> str:
    if isinstance(value, str):
        return value
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        raise BriqError(
            "unsupported", "briq takes text for stdin: the input is not UTF-8"
        ) from None


async def _close(client: AsyncBriq) -> None:
    try:
        await client.close()
    except Exception:
        logger.debug("Could not close the briq client", exc_info=True)


@contextmanager
def _inspect_errors(file: str | None = None) -> Iterator[None]:
    """A ``BriqError`` as the exception Inspect's contract names, where there is one."""
    try:
        yield
    except BriqError as error:
        if error.code == "file_not_found":
            raise FileNotFoundError(errno.ENOENT, "No such file or directory.", file) from error
        if error.code in ("unavailable", "not_found"):
            raise SandboxUnavailableError(str(error)) from error
        if error.code == "timeout":
            raise TimeoutError(str(error)) from error
        raise
