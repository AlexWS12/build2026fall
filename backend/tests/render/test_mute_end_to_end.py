"""F-30 end to end: a typed request goes through the planner, the toolbox, the timeline and the
renderer, and comes out with the right stretch silent.

There's no plan executor yet (it's a follow-up of F-05/F-06/F-64), so ``apply`` below runs the
plan's calls itself with ``call_tool``, filling each ``{"$ref": "sN"}`` with that step's result.
"""

from pathlib import Path

import numpy as np
import pytest
from render_clip import LONG_SECONDS, TONE_RMS

from vibe.agent import Plan, plan_request
from vibe.contract import OpLog
from vibe.render import export
from vibe.toolbox import TOOLS, ToolContext, call_tool
from vibe.video import extract_audio, probe

EDGE = 0.1
"""Seconds kept out of each check at a mute's edges, for the fade and the AAC encoder's smear."""


def rms(samples: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))


def apply(text: str, log: OpLog, request_id: str, duration: float) -> None:
    plan = plan_request(text)
    assert isinstance(plan, Plan), plan
    ctx = ToolContext(request_id=request_id, source="agent", log=log, duration=duration, tracks=[])
    results = {}
    for call in plan.calls:
        args = {k: _resolve(v, results, plan) for k, v in call.args.items()}
        results[call.step_id] = call_tool(call.tool, args, ctx)


def _resolve(value: object, results: dict, plan: Plan) -> object:
    if not (isinstance(value, dict) and set(value) == {"$ref"}):
        return value
    step = next(c for c in plan.calls if c.step_id == value["$ref"])
    return getattr(results[step.step_id], TOOLS[step.tool].returns).model_dump()


@pytest.fixture(scope="module")
def muted(long_clip: Path, tmp_path_factory: pytest.TempPathFactory) -> tuple[np.ndarray, int]:
    duration = probe(long_clip).duration
    log = OpLog()
    apply("mute 0:02 to 0:06", log, "req_1", duration)
    apply("unmute 0:03 to 0:04", log, "req_2", duration)
    apply("silence the last 2 seconds", log, "req_3", duration)  # a resolve_time step and a $ref
    result = export(long_clip, log.state(), [], tmp_path_factory.mktemp("f30") / "out.mp4")
    samples, rate = extract_audio(result.path)
    assert len(samples) / rate == pytest.approx(LONG_SECONDS, abs=0.05)  # mutes cut nothing
    return samples, rate


def span(muted: tuple[np.ndarray, int], start: float, end: float) -> np.ndarray:
    samples, rate = muted
    return samples[round((start + EDGE) * rate) : round((end - EDGE) * rate)]


@pytest.mark.parametrize(("start", "end"), [(2, 3), (4, 6), (10, 12)])
def test_muted_stretches_are_silent(
    muted: tuple[np.ndarray, int], start: float, end: float
) -> None:
    assert rms(span(muted, start, end)) < 0.005


@pytest.mark.parametrize(("start", "end"), [(0, 2), (3, 4), (6, 10)])
def test_the_rest_keeps_its_sound(muted: tuple[np.ndarray, int], start: float, end: float) -> None:
    assert rms(span(muted, start, end)) == pytest.approx(TONE_RMS, rel=0.2)
