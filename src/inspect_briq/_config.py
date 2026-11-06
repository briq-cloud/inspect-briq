"""The configuration of a ``briq`` sandbox: a typed object, or a JSON file that holds one."""

from __future__ import annotations

import json
import re
from pathlib import Path

from briq_cloud import BriqError
from pydantic import BaseModel, ConfigDict, Field

SIZES = ("small", "medium", "large", "xlarge")

_SLEEP = re.compile(r"\d+[mhd]|never")
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class BriqSandboxEnvironmentConfig(BaseModel):
    """How each sample's sandbox is made.

    The API key is never part of the configuration (it would end up in the eval log): set
    ``BRIQ_API_KEY``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    base_url: str | None = None
    """The briq API. Default: ``BRIQ_API_URL``, then ``https://api.briq.cloud``."""

    size: str | None = None
    """``small``, ``medium``, ``large`` or ``xlarge``. Default: the API's default (``small``)."""

    image: str | None = None
    """An OCI image for the sandbox. Default: briq's sandbox image."""

    sleep: str = "30m"
    """Idle time before the sandbox sleeps (``30m``, ``3h``, ``7d`` or ``never``). A killed
    evaluation leaves its sandboxes to sleep, which is not billed."""

    working_dir: str = "/work"
    """Where commands run and relative paths resolve."""

    env: dict[str, str] = Field(default_factory=dict)
    """Environment variables of the sandbox. They show in the sandbox's status and in the eval
    log: no secrets here."""

    secret_env: list[str] = Field(default_factory=list)
    """Names of variables of *this* process whose values go to the sandbox write-only. The values
    are not stored in the configuration or the log. The API must support it: if it does not,
    creating the sandbox fails with ``unsupported``."""

    default_exec_timeout: int = 600
    """Seconds a command may run when ``exec`` is given no timeout. Longer commands are stopped
    and raise ``TimeoutError``. 1 second to 24 hours."""

    wait_timeout: int = 120
    """Seconds to wait for a sandbox to start."""

    def __hash__(self) -> int:
        # Inspect keeps configurations in dicts, and the dict and list fields are not hashable.
        return hash(self.model_dump_json())

    def check(self) -> None:
        """Fail with ``unsupported`` or ``invalid_argument`` before any request is made."""
        if self.size is not None and self.size not in SIZES:
            raise BriqError("unsupported", f"size is one of {', '.join(SIZES)}, not {self.size!r}")
        if not _SLEEP.fullmatch(self.sleep):
            raise BriqError("invalid_argument", "sleep is like '30m', '3h', '7d' or 'never'")
        if not self.working_dir.startswith("/"):
            raise BriqError("invalid_argument", "working_dir is an absolute path")
        if not 1 <= self.default_exec_timeout <= 86_400:
            raise BriqError("invalid_argument", "default_exec_timeout is 1 second to 24 hours")
        for name in self.secret_env:
            if not _ENV_NAME.fullmatch(name):
                raise BriqError("invalid_argument", f"{name!r} is not a variable name")
        if set(self.env) & set(self.secret_env):
            raise BriqError("invalid_argument", "A variable is in both env and secret_env")


def resolve_config(config: object) -> BriqSandboxEnvironmentConfig:
    """What Inspect passes as ``config``: nothing, our object, or the path of a JSON file."""
    if config is None:
        resolved = BriqSandboxEnvironmentConfig()
    elif isinstance(config, BriqSandboxEnvironmentConfig):
        resolved = config
    elif isinstance(config, str):
        resolved = BriqSandboxEnvironmentConfig.model_validate(json.loads(Path(config).read_text()))
    else:
        raise TypeError(
            "The briq sandbox takes a BriqSandboxEnvironmentConfig or the path of a JSON file, "
            f"not {type(config).__name__}"
        )
    resolved.check()
    return resolved
