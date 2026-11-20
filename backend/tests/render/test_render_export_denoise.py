"""F-33b exit check: an export with a denoise span, no GPU.

The clip is made here with PyAV (already a dependency) rather than the ffmpeg CLI, so these
tests run on every machine: a small picture plus a 440 Hz tone that's on for half of every
second, with hiss throughout, as 48 kHz stereo AAC.
"""

from pathlib import Path

import av
import numpy as np
import pytest

from vibe.contract import AddAudioFilter, Cut, Op, OpLog, TimelineState, TimeRange
from vibe.modules.cache import cached_keys
from vibe.modules.denoise import NoiseProfile, learn_noise_from_file
from vibe.render import RenderError, export
from vibe.render.__main__ import main
from vibe.storage import Storage
from vibe.video import extract_audio

RATE, SECONDS, FPS = 48000, 4, 30
TONE_ON = 0.5  # seconds of tone at the start of every second


def noisy_sound() -> np.ndarray:
    t = np.arange(RATE * SECONDS) / RATE
    tone = 0.3 * np.sin(2 * np.pi * 440 * t) * ((t % 1.0) < TONE_ON)
    hiss = 0.03 * np.random.default_rng(0).standard_normal((len(t), 2))
    return (tone[:, None] + hiss).astype(np.float32)


@pytest.fixture(scope="module")
def noisy_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("denoise") / "noisy.mp4"
    sound = noisy_sound()
    with av.open(str(path), "w") as out:
        video = out.add_stream("libx264", rate=FPS)
        video.width, video.height, video.pix_fmt = 64, 48, "yuv420p"
        audio = out.add_stream("aac", rate=RATE, layout="stereo")
        image = np.full((48, 64, 3), 128, dtype=np.uint8)
        for _ in range(FPS * SECONDS):
            out.mux(video.encode(av.VideoFrame.from_ndarray(image, format="rgb24")))
        out.mux(video.encode())
        for start in range(0, len(sound), 1024):
            block = np.ascontiguousarray(sound[start : start + 1024].T)
            frame = av.AudioFrame.from_ndarray(block, format="fltp", layout="stereo")
            frame.sample_rate, frame.pts = RATE, start
            out.mux(audio.encode(frame))
        out.mux(audio.encode())
    return path


def state_of(*ops: Op) -> TimelineState:
    log = OpLog()
    for i, op in enumerate(ops):
        log.append(op.model_copy(update={"request_id": f"r_{i}"}))
    return log.state()


def denoise(start: float = 0.0, end: float | None = None) -> AddAudioFilter:
    return AddAudioFilter(
        request_id="r", source="agent", filter="denoise", time_range=TimeRange(start=start, end=end)
    )


def sound_of(path: Path) -> np.ndarray:
    return extract_audio(path, RATE, mono=True)[0]


def level(x: np.ndarray) -> float:
    return float(10 * np.log10(np.mean(np.square(x, dtype=np.float64)) + 1e-20))


def pauses(start: float, end: float) -> np.ndarray:
    """Sample numbers in the tone's pauses between ``start`` and ``end`` seconds, kept 50 ms
    clear of the tone's edges (AAC smears them)."""
    t = np.arange(round(end * RATE)) / RATE
    phase = t % 1.0
    return np.flatnonzero((t >= start) & (phase > TONE_ON + 0.05) & (phase < 0.95))


def test_exit_check_export_turns_the_hiss_down_inside_the_span(
    noisy_clip: Path, tmp_path: Path
) -> None:
    plain = sound_of(export(noisy_clip, state_of(), [], tmp_path / "plain.mp4").path)
    clean = sound_of(export(noisy_clip, state_of(denoise(2.0)), [], tmp_path / "dn.mp4").path)

    before, after = pauses(0.0, 1.95), pauses(2.0, SECONDS - 0.1)
    assert level(clean[after]) < level(plain[after]) - 6
    assert abs(level(clean[before]) - level(plain[before])) < 0.5  # outside: unchanged


def test_denoised_sound_stays_in_sync(noisy_clip: Path, tmp_path: Path) -> None:
    """The gate holds back about one FFT frame; the export must not shift the sound by it."""
    plain = sound_of(export(noisy_clip, state_of(), [], tmp_path / "plain.mp4").path)
    clean = sound_of(export(noisy_clip, state_of(denoise()), [], tmp_path / "dn.mp4").path)

    n = min(len(plain), len(clean))
    window = 2048
    lags = range(-window, window + 1, 16)
    best = max(
        lags,
        key=lambda lag: float(
            np.dot(plain[window : n - window], np.roll(clean, -lag)[window : n - window])
        ),
    )
    assert abs(best) <= 16


def test_denoise_with_a_cut_keeps_the_length(noisy_clip: Path, tmp_path: Path) -> None:
    cut = Cut(request_id="r", source="agent", time_range=TimeRange(start=1.0, end=2.0))
    result = export(noisy_clip, state_of(denoise(), cut), [], tmp_path / "out.mp4")

    assert result.duration == pytest.approx(SECONDS - 1.0, abs=1 / FPS)
    assert len(sound_of(result.path)) == pytest.approx((SECONDS - 1.0) * RATE, abs=2048)


def test_export_uses_the_profile_its_given(noisy_clip: Path, tmp_path: Path) -> None:
    asked: list[tuple[int, str]] = []

    def source(rate: int, layout: str) -> NoiseProfile:
        asked.append((rate, layout))
        return learn_noise_from_file(noisy_clip, rate, layout)

    export(noisy_clip, state_of(denoise()), [], tmp_path / "out.mp4", noise_profile=source)

    assert asked == [(RATE, "stereo")]


def test_a_profile_for_another_format_is_refused(noisy_clip: Path, tmp_path: Path) -> None:
    wrong = learn_noise_from_file(noisy_clip, 16000, "mono")

    with pytest.raises(RenderError, match="16000 Hz"):
        export(
            noisy_clip, state_of(denoise()), [], tmp_path / "out.mp4", lambda rate, layout: wrong
        )
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("backend", ["local", "remote"])
def test_a_backend_that_cant_denoise_fails_before_writing(
    noisy_clip: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    monkeypatch.setenv("VIBE_BACKEND", backend)

    with pytest.raises(RenderError, match="isn't built yet"):
        export(noisy_clip, state_of(denoise()), [], tmp_path / "out.mp4")
    assert not list(tmp_path.iterdir())


def test_project_export_caches_the_noise_profile(
    noisy_clip: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VIBE_DATA_DIR", str(tmp_path))
    storage = Storage()
    project = storage.create_project(noisy_clip)
    project.append(denoise().model_copy(update={"request_id": "r_1"}))
    sha = project.info.video.sha256

    assert main(["export", "--project", project.id]) == 0
    keys = cached_keys(storage, sha, "noise_profile")
    assert main(["export", "--project", project.id]) == 0

    assert len(keys) == 1
    assert cached_keys(storage, sha, "noise_profile") == keys
    assert len(list(project.exports_dir.glob("*.mp4"))) == 2
