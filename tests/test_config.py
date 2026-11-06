from __future__ import annotations

import json
from pathlib import Path

import pytest
from briq_cloud import BriqError
from pydantic import ValidationError

from inspect_briq import BriqSandboxEnvironmentConfig
from inspect_briq._config import resolve_config


def test_defaults() -> None:
    config = resolve_config(None)
    assert config.size is None
    assert config.sleep == "30m"
    assert config.working_dir == "/work"


def test_a_typed_object_is_used_as_it_is() -> None:
    config = BriqSandboxEnvironmentConfig(size="large", env={"A": "1"})
    assert resolve_config(config) is config


def test_a_json_file(tmp_path: Path) -> None:
    path = tmp_path / "briq.json"
    path.write_text(json.dumps({"size": "medium", "sleep": "never"}))
    config = resolve_config(str(path))
    assert (config.size, config.sleep) == ("medium", "never")


@pytest.mark.parametrize("size", ["tiny", "2x4", "Small", ""])
def test_an_unknown_size_is_unsupported(size: str) -> None:
    with pytest.raises(BriqError) as error:
        resolve_config(BriqSandboxEnvironmentConfig(size=size))
    assert error.value.code == "unsupported"


@pytest.mark.parametrize("size", ["small", "medium", "large", "xlarge"])
def test_every_size_is_accepted(size: str) -> None:
    assert resolve_config(BriqSandboxEnvironmentConfig(size=size)).size == size


@pytest.mark.parametrize(
    "fields",
    [
        {"sleep": "soon"},
        {"working_dir": "work"},
        {"default_exec_timeout": 0},
        {"default_exec_timeout": 90_000},
        {"secret_env": ["not a name"]},
        {"env": {"A": "1"}, "secret_env": ["A"]},
    ],
)
def test_a_bad_value_is_invalid_argument(fields: dict[str, object]) -> None:
    with pytest.raises(BriqError) as error:
        resolve_config(BriqSandboxEnvironmentConfig(**fields))  # type: ignore[arg-type]
    assert error.value.code == "invalid_argument"


def test_the_key_is_not_a_field() -> None:
    with pytest.raises(ValidationError):
        BriqSandboxEnvironmentConfig(api_key="secret")  # type: ignore[call-arg]


def test_something_else_is_a_type_error() -> None:
    with pytest.raises(TypeError):
        resolve_config(42)


def test_a_config_is_hashable() -> None:
    a = BriqSandboxEnvironmentConfig(env={"A": "1"})
    assert hash(a) == hash(BriqSandboxEnvironmentConfig(env={"A": "1"}))
    assert len({a, BriqSandboxEnvironmentConfig(env={"A": "2"})}) == 2
