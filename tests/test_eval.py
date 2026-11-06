"""A whole evaluation through Inspect, with a fake briq: the lifecycle as Inspect drives it."""

from __future__ import annotations

from pathlib import Path

from inspect_ai import Task, eval_async
from inspect_ai.dataset import Sample
from inspect_ai.scorer import Score, Target, accuracy, scorer
from inspect_ai.solver import Generate, TaskState, solver
from inspect_ai.util import SandboxEnvironmentSpec, sandbox

from inspect_briq import BriqSandboxEnvironmentConfig

from .conftest import FakeClient


@solver
def read_back():  # type: ignore[no-untyped-def]
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        box = sandbox()
        await box.write_file("out.txt", "written by the solver")
        state.metadata["file"] = await box.read_file("in.txt")
        state.metadata["out"] = (await box.exec(["cat", "out.txt"])).stdout
        return state

    return solve


@scorer(metrics=[accuracy()])
def file_is_there():  # type: ignore[no-untyped-def]
    async def score(state: TaskState, target: Target) -> Score:
        return Score(value=1.0 if state.metadata["file"] == "from the sample" else 0.0)

    return score


async def test_an_evaluation_runs_each_sample_in_its_own_sandbox(
    fake_client: type[FakeClient], tmp_path: Path
) -> None:
    task = Task(
        dataset=[
            Sample(input="a", files={"in.txt": "from the sample"}, setup="touch ready"),
            Sample(input="b", files={"in.txt": "from the sample"}),
        ],
        solver=read_back(),
        scorer=file_is_there(),
        sandbox=SandboxEnvironmentSpec("briq", BriqSandboxEnvironmentConfig(size="medium")),
    )
    (log,) = await eval_async(task, model="mockllm/model", log_dir=str(tmp_path))
    assert log.status == "success"
    assert log.results is not None
    assert log.results.scores[0].metrics["accuracy"].value == 1.0
    # One client per sample: each sandbox is destroyed and each client closed at the end.
    clients = [c for c in fake_client.instances if c.created]
    assert len(clients) == 2
    assert all(c.created[0]["size"] == "medium" for c in clients)
    assert all(c.sandbox.destroyed and c.closed for c in clients)
    # The setup script is written as a file and run through exec, in the working directory.
    scripts = [d for c in clients for d in c.sandbox.files.values() if b"touch ready" in d]
    assert len(scripts) == 1
    assert all(e["cwd"] == "/work" for c in clients for e in c.sandbox.execs)
