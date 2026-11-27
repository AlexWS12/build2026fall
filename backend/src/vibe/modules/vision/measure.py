"""F-13 measurement (D-020): how does a description judge do on labelled clips?

    truth file ──► per case: the clip's labelled person tracks ──► judge ──► right / wrong / unsure
    (clip, description,          crops cut from the video                    and seconds per track
     which tracks match)

D-020 doesn't ask for an average score. It asks one thing: does the model ever say a confident
wrong ``no``? A ``no`` on someone who matches leaves them unmasked under "blur the person in
red"; a ``yes`` on someone who doesn't leaves them unmasked under "everyone except the person in
red". ``unsure`` is always safe (masked) but costs: the more of it, the more of the frame gets
blurred. So the report counts the outcomes apart and never folds them into one percentage.

    uv run python -m vibe.modules.vision.measure TRUTH.json \\
        --hardware "NVIDIA L4 23 GB (HiPerGator)" --runs-on third-party [--json OUT.json]

The judge comes from ``VIBE_BACKEND`` (or ``--backend``), like everywhere: ``stub`` is the colour
judge (a baseline, and it never sends a crop anywhere); ``local`` / ``remote`` use
``VIBE_VLM_MODEL`` and ``VIBE_VLM_URL`` (see :mod:`.vlm`).

The truth file is JSON; the tracks come from the clip's benchmark labels (F-09)::

    {"truth_version": 1, "notes": "...",
     "cases": [{"clip_id": "trio-street-outfits", "description": "in an orange jacket",
                "matches": ["p2"], "ambiguous": []}]}

Every other track with that ``label`` (default ``person``) counts as *not matching*.
``ambiguous`` tracks are ones a person couldn't call (half out of frame, the label notes don't
say): they are run and listed but not scored.

Where the crops go: ``--runs-on`` is required, because the script can't tell a tunnel to a
cluster from this machine, and the report says what you passed.

- ``this-machine`` / ``team-machine``: no gate (D-017 allows media on team machines).
- ``university``: a university cluster such as HiPerGator, when the team has approved it for
  this footage. No gate, but the report says so. That approval is the team's call, made outside
  the code; the manifest's ``consent_cloud`` is about the people in the clip, not about it.
- ``third-party`` (a rented GPU, a hosted API, anything else): refuses any clip whose manifest
  entry doesn't have ``consent_cloud`` (D-017).

What this does and doesn't measure, so nobody reads more into it:

- The judge alone, on the **hand-labelled boxes**, which are better than a detector's. A judge
  that does well here can still do worse on detector tracks.
- Seconds are the judge calls only (crops are cut from the video, which is not timed), over the
  tracks that were really asked. Hardware is whatever you pass in ``--hardware``: say it truly.
- A handful of tracks gives counts, not rates. Report them as counts.
"""

import argparse
import json
import statistics
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import numpy as np
from pydantic import Field, ValidationError

from vibe.benchmark.labels import LABELS_DIR, check_labels, load_labels
from vibe.benchmark.manifest import (
    BenchmarkError,
    ClipEntry,
    ClipManifest,
    clip_status,
    load_manifest,
)
from vibe.config import model_backend
from vibe.contract import Track
from vibe.contract.common import Model
from vibe.modules.cache import ModuleError
from vibe.modules.vision.describe import (
    DEFAULT_SAMPLES,
    MIN_CROPS,
    ColorJudge,
    Judge,
    Verdict,
    describe_tracks,
    make_judge,
)
from vibe.modules.vision.vlm import LOOPBACK_HOSTS, VlmJudge
from vibe.storage import Storage

TRUTH_VERSION = 1
RUNS_ON = ("this-machine", "team-machine", "university", "third-party")
RunsOn = Literal["this-machine", "team-machine", "university", "third-party"]

Truth = Literal["yes", "no", "ambiguous"]


# --- the truth file -----------------------------------------------------------------------


class TruthCase(Model):
    clip_id: str
    description: str = Field(min_length=1, description='In the user\'s words, e.g. "in red".')
    label: str = Field(default="person", description="Which labelled tracks are asked about.")
    matches: list[str] = Field(description="Track ids that match. May be empty.")
    ambiguous: list[str] = Field(default_factory=list, description="Run, listed, not scored.")
    notes: str = ""


class TruthFile(Model):
    truth_version: int = TRUTH_VERSION
    notes: str = ""
    cases: list[TruthCase] = Field(min_length=1)


def load_truth(path: Path) -> TruthFile:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise BenchmarkError(f"can't read {path}: {e}") from e
    except json.JSONDecodeError as e:
        raise BenchmarkError(f"{path.name}: not valid JSON: {e}") from e
    version = data.get("truth_version") if isinstance(data, dict) else None
    if version != TRUTH_VERSION:
        raise BenchmarkError(
            f"{path.name} has truth_version {version!r}; this code reads {TRUTH_VERSION}"
        )
    try:
        return TruthFile.model_validate(data)
    except ValidationError as e:
        raise BenchmarkError(f"{path.name}: {e}") from e


@dataclass(frozen=True)
class Resolved:
    """A case tied to its clip: the video on disk, the tracks, and the right answer for each."""

    case: TruthCase
    entry: ClipEntry
    video: Path
    tracks: list[Track]
    truth: dict[str, Truth]


def resolve(
    truth: TruthFile,
    manifest: ClipManifest,
    storage: Storage,
    labels_dir: Path = LABELS_DIR,
) -> list[Resolved]:
    """Every case tied to a clip that is here, labelled, and agrees with its labels. All the
    problems are reported together, so one run of the script tells you everything to fix."""
    problems: list[str] = []
    out: list[Resolved] = []
    for case in truth.cases:
        where = f"{case.clip_id} / {case.description!r}"
        entry = manifest.get(case.clip_id)
        if entry is None:
            problems.append(f"{where}: no such clip in clips.json")
            continue
        status, advice = clip_status(entry, storage)
        if status != "ok":
            problems.append(f"{where}: the video is {status} ({advice})")
            continue
        labels = load_labels(case.clip_id, labels_dir)
        if labels is None:
            problems.append(f"{where}: the clip has no labels yet")
            continue
        found = check_labels(labels, entry)
        if found:
            problems.append(f"{where}: labels don't match the clip: {'; '.join(found)}")
            continue
        tracks = [t for t in labels.tracks if t.label == case.label]
        ids = {t.track_id for t in tracks}
        unknown = sorted((set(case.matches) | set(case.ambiguous)) - ids)
        both = sorted(set(case.matches) & set(case.ambiguous))
        if not tracks:
            problems.append(f"{where}: the labels have no {case.label!r} tracks")
        elif unknown:
            problems.append(f"{where}: not {case.label} tracks of this clip: {', '.join(unknown)}")
        elif both:
            problems.append(f"{where}: both a match and ambiguous: {', '.join(both)}")
        else:
            answers: dict[str, Truth] = {
                t.track_id: (
                    "ambiguous"
                    if t.track_id in case.ambiguous
                    else "yes"
                    if t.track_id in case.matches
                    else "no"
                )
                for t in tracks
            }
            out.append(Resolved(case, entry, storage.video_path(entry.video), tracks, answers))
    if problems:
        raise BenchmarkError("can't run the truth file:\n  " + "\n  ".join(problems))
    return out


def check_where(resolved: Sequence[Resolved], judge: Judge, runs_on: RunsOn) -> None:
    """Refuse before any crop is sent if the clips' consent doesn't cover where they'd go."""
    if isinstance(judge, ColorJudge):
        return  # the stub never sends a crop anywhere
    if runs_on == "third-party":
        blocked = sorted({r.entry.clip_id for r in resolved if not r.entry.consent_cloud})
        if blocked:
            raise BenchmarkError(
                "won't send these clips' crops to a third party: their manifest entries don't "
                f"have consent_cloud (D-017): {', '.join(blocked)}. Run on this machine or a "
                "team machine (--runs-on), or use clips whose consent covers it."
            )
    if runs_on == "this-machine" and isinstance(judge, VlmJudge):
        host = (urlsplit(judge.url).hostname or "").lower()
        if host not in LOOPBACK_HOSTS:
            raise BenchmarkError(
                f"--runs-on this-machine, but the judge talks to {host!r}. Say where it really "
                "runs: team-machine or third-party."
            )


# --- running ------------------------------------------------------------------------------


class TimedJudge(Judge):
    """Wraps a judge and times every call that really asks (enough crops to ask)."""

    name = "timed"

    def __init__(self, inner: Judge) -> None:
        super().__init__()
        self.inner = inner
        self.seconds: list[float | None] = []  # one per call; None when it wasn't asked

    def judge(self, crops: Sequence[np.ndarray], label: str, description: str) -> Verdict:
        asked = len(crops) >= MIN_CROPS
        start = time.perf_counter()
        try:
            return self.inner.judge(crops, label, description)
        finally:
            self.seconds.append(time.perf_counter() - start if asked else None)


@dataclass(frozen=True)
class TrackResult:
    track_id: str
    truth: Truth
    verdict: Verdict
    seconds: float | None


@dataclass(frozen=True)
class CaseResult:
    clip_id: str
    description: str
    tracks: list[TrackResult]


def run_case(resolved: Resolved, judge: Judge, samples: int) -> CaseResult:
    timed = TimedJudge(judge)
    verdicts = describe_tracks(
        resolved.video, resolved.tracks, resolved.case.description, timed, samples
    )
    rows = [
        TrackResult(t.track_id, resolved.truth[t.track_id], verdicts[t.track_id], s)
        for t, s in zip(resolved.tracks, timed.seconds, strict=True)
    ]
    return CaseResult(resolved.case.clip_id, resolved.case.description, rows)


def tally(results: Sequence[CaseResult]) -> dict:
    """The counts, over every scored track (ambiguous ones are only counted)."""
    rows = [t for r in results for t in r.tracks]
    scored = [t for t in rows if t.truth != "ambiguous"]

    def count(truth: Truth, verdict: Verdict) -> int:
        return sum(1 for t in scored if t.truth == truth and t.verdict == verdict)

    asked = [t.seconds for t in rows if t.seconds is not None]
    seconds = (
        {
            "asked": len(asked),
            "mean": statistics.fmean(asked),
            "median": statistics.median(asked),
            "max": max(asked),
            "total": sum(asked),
        }
        if asked
        else {"asked": 0}
    )
    return {
        "scored": len(scored),
        "ambiguous": len(rows) - len(scored),
        "right": count("yes", "yes") + count("no", "no"),
        "yes_yes": count("yes", "yes"),
        "no_no": count("no", "no"),
        "wrong_no": count("yes", "no"),
        "wrong_yes": count("no", "yes"),
        "unsure_on_match": count("yes", "unsure"),
        "unsure_on_non_match": count("no", "unsure"),
        "seconds_per_track": seconds,
    }


def leaks(results: Sequence[CaseResult]) -> list[dict]:
    """Every track the judge got wrong the dangerous way, so none hides in a total."""
    out = []
    for r in results:
        for t in r.tracks:
            if t.truth == "yes" and t.verdict == "no":
                kind = "wrong_no"
            elif t.truth == "no" and t.verdict == "yes":
                kind = "wrong_yes"
            else:
                continue
            out.append(
                {"kind": kind, "clip": r.clip_id, "description": r.description, "track": t.track_id}
            )
    return out


# --- the report ---------------------------------------------------------------------------


def report_data(results: Sequence[CaseResult], judge: Judge, meta: dict) -> dict:
    def track_row(t: TrackResult) -> dict:
        return {"track": t.track_id, "truth": t.truth, "verdict": t.verdict, "seconds": t.seconds}

    return {
        "judge": {"name": judge.name, "version": judge.version, "settings": judge.settings},
        **meta,
        "totals": tally(results),
        "leaks": leaks(results),
        "cases": [
            {
                "clip": r.clip_id,
                "description": r.description,
                "totals": tally([r]),
                "tracks": [track_row(t) for t in r.tracks],
            }
            for r in results
        ],
    }


def format_report(data: dict) -> str:
    t = data["totals"]
    lines = [
        "F-13 description judge: measurement",
        f"  judge      {data['judge']['name']} v{data['judge']['version']} "
        f"{json.dumps(data['judge']['settings'], sort_keys=True)}",
        f"  backend    {data['backend']}, {data['samples']} crops per track",
        f"  hardware   {data['hardware']}   (runs on: {data['runs_on']})",
        f"  tracks     {data['tracks_from']}",
        f"  date       {data['date']}",
        "",
        f"  {'case':<58}{'right':>6}{'wrong':>7}{'wrong':>7}{'unsure':>7}",
        f"  {'':<58}{'':>6}{'no':>7}{'yes':>7}{'':>7}",
    ]
    for c in data["cases"]:
        ct = c["totals"]
        name = f"{c['clip']} | {c['description']}"
        lines.append(
            f"  {name[:57]:<58}{ct['right']:>6}{ct['wrong_no']:>7}{ct['wrong_yes']:>7}"
            f"{ct['unsure_on_match'] + ct['unsure_on_non_match']:>7}"
        )
    unsure = t["unsure_on_match"] + t["unsure_on_non_match"]
    lines += [
        "",
        f"TOTAL: {t['scored']} tracks scored ({t['ambiguous']} ambiguous, run but not scored)",
        f"  right ....... {t['right']:>4}   (match -> yes: {t['yes_yes']}, "
        f"non-match -> no: {t['no_no']})",
        f"  WRONG NO .... {t['wrong_no']:>4}   a track that matches was ruled out: left "
        'unmasked by "blur the ..."',
        f"  wrong yes ... {t['wrong_yes']:>4}   a track that doesn't match was accepted: left "
        'unmasked by "everyone except ..."',
        f"  unsure ...... {unsure:>4}   (on matches {t['unsure_on_match']}, on non-matches "
        f"{t['unsure_on_non_match']}): masked, safe but blurs more",
    ]
    s = t["seconds_per_track"]
    if s["asked"]:
        lines.append(
            f"  seconds per asked track: mean {s['mean']:.2f}, median {s['median']:.2f}, "
            f"max {s['max']:.2f}, over {s['asked']} tracks ({s['total']:.0f} s of judge calls)"
        )
    else:
        lines.append("  seconds per asked track: none were asked")
    if data["leaks"]:
        lines += ["", "Tracks the judge got wrong the dangerous way:"]
        lines += [
            f"  {x['kind'].replace('_', ' ')}: {x['clip']} | {x['description']} | {x['track']}"
            for x in data["leaks"]
        ]
    lines += [
        "",
        "Counts, not rates: a handful of tracks can't support a percentage. These are the "
        "hand-labelled boxes, so a detector's tracks may do worse.",
    ]
    return "\n".join(lines)


def measure(
    resolved: Sequence[Resolved],
    judge: Judge,
    *,
    samples: int = DEFAULT_SAMPLES,
    meta: dict | None = None,
    progress: Callable[[str], None] = lambda _: None,
) -> dict:
    """Run every case and return the report as data (``format_report`` makes it readable).
    A judge that can't answer raises ``DescribeError``: nothing is reported, since an error is
    never a verdict."""
    results = []
    for i, r in enumerate(resolved, 1):
        progress(f"[{i}/{len(resolved)}] {r.case.clip_id} | {r.case.description}")
        results.append(run_case(r, judge, samples))
    base = {
        "backend": "?",
        "hardware": "?",
        "runs_on": "?",
        "samples": samples,
        "tracks_from": "hand-labelled boxes (benchmark labels), not detector tracks",
        "date": datetime.now(UTC).strftime("%Y-%m-%dT%H:%MZ"),
    }
    return report_data(results, judge, {**base, **(meta or {})})


# --- command line -------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m vibe.modules.vision.measure",
        description="Measure a description judge on labelled clips (F-13, D-020).",
    )
    p.add_argument("truth", type=Path, help="the truth file (see this module's docstring)")
    p.add_argument(
        "--hardware",
        required=True,
        help='what the model runs on, truthfully, e.g. "NVIDIA L4 23 GB (HiPerGator)"',
    )
    p.add_argument(
        "--runs-on",
        required=True,
        choices=RUNS_ON,
        help="where the crops go: this machine, a team machine, a university cluster the team "
        "approved (HiPerGator), or a third party (needs consent_cloud on every clip)",
    )
    p.add_argument("--backend", choices=("stub", "local", "remote"), default=None)
    p.add_argument("--samples", type=int, default=DEFAULT_SAMPLES, help="crops per track")
    p.add_argument("--json", dest="json_out", type=Path, default=None, help="also write the data")
    return p


def main(
    argv: Sequence[str] | None = None,
    *,
    storage: Storage | None = None,
    manifest: ClipManifest | None = None,
    labels_dir: Path = LABELS_DIR,
    judge: Judge | None = None,
) -> int:
    args = _parser().parse_args(argv)
    try:
        backend = args.backend or model_backend()
        manifest = manifest or load_manifest()
        resolved = resolve(load_truth(args.truth), manifest, storage or Storage(), labels_dir)
        judge = judge or make_judge(backend)
        check_where(resolved, judge, args.runs_on)
        data = measure(
            resolved,
            judge,
            samples=args.samples,
            meta={"backend": backend, "hardware": args.hardware, "runs_on": args.runs_on},
            progress=lambda line: print(line, file=sys.stderr, flush=True),
        )
    except (BenchmarkError, ModuleError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(format_report(data))
    if args.json_out:
        args.json_out.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
