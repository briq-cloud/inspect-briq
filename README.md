# inspect-briq

[![ci](https://github.com/briq-cloud/inspect-briq/actions/workflows/ci.yml/badge.svg)](https://github.com/briq-cloud/inspect-briq/actions/workflows/ci.yml) [![PyPI](https://img.shields.io/pypi/v/inspect-briq)](https://pypi.org/project/inspect-briq/) [![license: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

Run [Inspect AI](https://inspect.aisi.org.uk/) sandboxes on [briq](https://briq.cloud). Each sample gets its own microVM, destroyed when the sample ends.

```python
from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.scorer import includes
from inspect_ai.solver import generate, use_tools
from inspect_ai.tool import bash
from inspect_ai.util import SandboxEnvironmentSpec
from inspect_briq import BriqSandboxEnvironmentConfig


@task
def kernel():
    return Task(
        dataset=[
            Sample(
                input="Run `uname -s` with the bash tool and tell me its output.",
                target="Linux",
                files={"notes.txt": "one file per sample, copied in before the first message"},
            )
        ],
        solver=[use_tools(bash(timeout=30)), generate()],
        scorer=includes(),
        sandbox=SandboxEnvironmentSpec("briq", BriqSandboxEnvironmentConfig(size="medium")),
    )
```

```sh
pip install inspect-briq
export BRIQ_API_KEY=...
inspect eval kernel.py --model anthropic/claude-sonnet-4-5
```

`sandbox="briq"` uses the defaults. Python 3.11 or later (what Inspect needs), asyncio backend.

## Configure

`BriqSandboxEnvironmentConfig`, or a JSON file with the same fields: `sandbox=("briq", "briq.json")`.

| Field                  | Default                  | Means                                                                     |
| ---------------------- | ------------------------ | ------------------------------------------------------------------------- |
| `size`                 | the API's default        | `small`, `medium`, `large` or `xlarge`. Anything else fails `unsupported`. |
| `image`                | briq's sandbox image     | An OCI image.                                                             |
| `sleep`                | `30m`                    | Idle time before the sandbox sleeps: `30m`, `3h`, `7d`, `never`.          |
| `working_dir`          | `/work`                  | Where commands run and relative paths resolve.                            |
| `env`                  | none                     | Variables of the sandbox. They show in its status and in the eval log.    |
| `secret_env`           | none                     | Names of variables of the eval process, sent write-only. Needs API support. |
| `default_exec_timeout` | `600`                    | Seconds for a command that has no timeout. It raises `TimeoutError`.      |
| `wait_timeout`         | `120`                    | Seconds to wait for a sandbox to start.                                   |
| `base_url`             | `BRIQ_API_URL`, or the public API | The briq API.                                                    |

The API key is read from `BRIQ_API_KEY` only: a key in the configuration would end up in the eval log.

```python
BriqSandboxEnvironmentConfig(
    size="large",
    env={"MODE": "test"},
    secret_env=["GITHUB_TOKEN"],  # read from os.environ, never stored in the log
)
```

`secret_env` fails with `unsupported` when the API cannot keep a value write-only. It never falls back to a plain variable.

## What is supported

| Inspect                                | On briq                                                                   |
| -------------------------------------- | ------------------------------------------------------------------------- |
| `exec` with `cwd`, `env`, `timeout`, `input` | Yes. `input` is text: bytes that are not UTF-8 fail `unsupported`.  |
| `exec(user=...)`, `read_file(user=...)`, `write_file(user=...)` | No: `SandboxUserUnsupportedError`. Commands run as the sandbox's own user. |
| `read_file`, `write_file`              | Yes. Missing directories are created. A file over 100 MiB fails.          |
| Sample `files` and `setup`             | Yes, through `write_file` and `exec`.                                     |
| One sandbox per sample                 | Yes, named `default`.                                                     |
| Several named sandboxes per sample     | No.                                                                       |
| `connection()` (`inspect sandbox shell`) | No.                                                                     |
| Docker `compose.yaml` and `Dockerfile` | No. Use `image`.                                                          |

`timeout_retry` and `concurrency` have no effect: a command that outlives its timeout is stopped and raises `TimeoutError` at once. Limit parallel sandboxes with `--max-sandboxes`.

Errors:

| Raised                       | When                                                         |
| ---------------------------- | ------------------------------------------------------------ |
| `FileNotFoundError`          | The file does not exist.                                     |
| `TimeoutError`               | A command or a call outlived its timeout.                    |
| `SandboxUnavailableError`    | The sandbox is gone or briq could not take the call.         |
| `OutputLimitExceededError`   | Output or a file is over Inspect's limit.                    |
| `briq_cloud.BriqError`       | Anything else: quota, credits, plan, a wrong key. It has a stable `code`. |

A command whose end is not known raises `BriqError` with `code="outcome_unknown"`. It is never repeated.

## Clean up

A sample's sandbox is destroyed when the sample ends, also when it fails or is cancelled. If the evaluation is killed, its sandboxes stay: they sleep after `sleep` and are not billed while asleep. Destroy them from the dashboard, or with `briq_cloud`: `AsyncBriq().sandbox(id).destroy()`.

## Develop

Needs [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run pytest          # offline, the briq client is faked
uv run ruff check . && uv run ruff format --check .
uv run mypy
```

## License

MIT
