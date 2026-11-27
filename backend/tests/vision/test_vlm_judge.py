"""F-13 / D-020: the VLM description judge. No GPU and no real model: a small HTTP server on
127.0.0.1 plays the model, so the request, the parsing and the failure paths are all checked."""

import json
import socket
import struct
import threading
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from vision_helpers import FPS, HEIGHT, WIDTH

from vibe.contract import Keyframe, Track
from vibe.modules.vision.describe import (
    MIN_CROPS,
    DescribeError,
    VideoDescriber,
    make_judge,
)
from vibe.modules.vision.vlm import (
    MAX_SIDE,
    VlmJudge,
    encode_png,
    parse_verdict,
    prompt,
    shrink,
    vlm_judge,
)
from vibe.storage import Storage

# --- a fake model server ------------------------------------------------------------------


@pytest.fixture
def server():
    """A server that answers every chat request with ``state.reply`` and records the requests."""
    state = SimpleNamespace(requests=[], reply="yes", status=200, raw=None, url="")

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers["Content-Length"])
            state.requests.append({"path": self.path, "body": json.loads(self.rfile.read(length))})
            if state.raw is not None:
                body = state.raw
            else:
                body = json.dumps({"choices": [{"message": {"content": state.reply}}]}).encode()
            self.send_response(state.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:  # keep the test output clean
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    state.url = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield state
    httpd.shutdown()
    httpd.server_close()


def crops(n: int = 3, size: int = 20) -> list[np.ndarray]:
    return [np.full((size, size, 3), 100 + i, dtype=np.uint8) for i in range(n)]


def judge_for(server, **kwargs) -> VlmJudge:
    return VlmJudge("test-model", server.url, **kwargs)


# --- sending pixels -----------------------------------------------------------------------


def decode_png(data: bytes) -> np.ndarray:
    """Reads back what ``encode_png`` writes (8-bit RGB, filter 0 on every row)."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, size = 8, b"", None
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        tag, body = data[pos + 4 : pos + 8], data[pos + 8 : pos + 8 + length]
        (crc,) = struct.unpack(">I", data[pos + 8 + length : pos + 12 + length])
        assert crc == zlib.crc32(tag + body)
        if tag == b"IHDR":
            width, height, depth, color, *_ = struct.unpack(">IIBBBBB", body)
            assert (depth, color) == (8, 2)
            size = (height, width)
        elif tag == b"IDAT":
            idat += body
        pos += 12 + length
    height, width = size
    rows = np.frombuffer(zlib.decompress(idat), dtype=np.uint8).reshape(height, 1 + width * 3)
    assert (rows[:, 0] == 0).all()
    return rows[:, 1:].reshape(height, width, 3)


def test_a_png_reads_back_as_the_same_pixels() -> None:
    image = np.random.default_rng(0).integers(0, 256, size=(7, 13, 3), dtype=np.uint8)

    assert np.array_equal(decode_png(encode_png(image)), image)


@pytest.mark.parametrize(
    "image",
    [
        np.zeros((4, 4), dtype=np.uint8),
        np.zeros((4, 4, 3), dtype=np.float32),
        np.zeros((0, 4, 3), dtype=np.uint8),
    ],
)
def test_a_png_is_only_made_of_an_rgb_image(image: np.ndarray) -> None:
    with pytest.raises(ValueError):
        encode_png(image)


def test_big_crops_are_shrunk_and_small_ones_left_alone() -> None:
    small = np.zeros((100, 60, 3), dtype=np.uint8)
    assert shrink(small) is small

    big = np.full((1500, 700, 3), 200, dtype=np.uint8)
    smaller = shrink(big)
    assert max(smaller.shape[:2]) <= MAX_SIDE and smaller.shape[2] == 3
    assert (smaller == 200).all()  # averaging keeps a flat colour flat

    sliver = np.zeros((2, 3000, 3), dtype=np.uint8)
    assert max(shrink(sliver).shape[:2]) <= MAX_SIDE


# --- reading the answer -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reply", "verdict"),
    [
        ("yes", "yes"),
        ("Yes.", "yes"),
        ("  NO\n", "no"),
        ("**no**", "no"),
        ("unsure", "unsure"),
        ("Unsure!", "unsure"),
        # Anything that isn't exactly one of the three words is doubt, which is masked.
        ("", "unsure"),
        ("maybe", "unsure"),
        ("no, but the second image is blurry", "unsure"),
        ("yes or no", "unsure"),
        ("not sure", "unsure"),
        ("I think yes", "unsure"),
    ],
)
def test_only_an_exact_answer_counts(reply: str, verdict: str) -> None:
    assert parse_verdict(reply) == verdict


def test_the_prompt_asks_about_the_track_and_never_who_it_is() -> None:
    text = prompt("person", "in a grey t-shirt with a bag", 3)

    assert "person" in text and "3 images" in text and '"in a grey t-shirt with a bag"' in text
    assert "unsure" in text and "When in doubt" in text
    assert "who anyone is" in text  # told not to identify (D-017)


# --- asking ---------------------------------------------------------------------------------


def test_one_request_carries_the_question_and_every_crop(server) -> None:
    verdict = judge_for(server).judge(crops(3), "person", "in red")

    assert verdict == "yes"
    assert len(server.requests) == 1
    request = server.requests[0]
    assert request["path"] == "/v1/chat/completions"
    body = request["body"]
    assert body["model"] == "test-model" and body["temperature"] == 0
    parts = body["messages"][0]["content"]
    assert parts[0]["type"] == "text" and '"in red"' in parts[0]["text"]
    images = [p for p in parts if p["type"] == "image_url"]
    assert len(images) == 3
    assert all(p["image_url"]["url"].startswith("data:image/png;base64,") for p in images)


@pytest.mark.parametrize(
    ("reply", "verdict"), [("yes", "yes"), ("no", "no"), ("unsure", "unsure"), ("hmm", "unsure")]
)
def test_the_reply_becomes_a_verdict(server, reply: str, verdict: str) -> None:
    server.reply = reply

    assert judge_for(server).judge(crops(), "person", "in red") == verdict


def test_too_few_crops_is_unsure_without_asking(server) -> None:
    judge = judge_for(server)

    assert judge.judge([], "person", "in red") == "unsure"
    assert judge.judge(crops(MIN_CROPS - 1), "person", "in red") == "unsure"
    assert server.requests == []


def test_a_url_that_already_ends_in_v1_still_works(server) -> None:
    VlmJudge("m", server.url + "/v1/").judge(crops(), "person", "in red")

    assert server.requests[0]["path"] == "/v1/chat/completions"


# --- a server that can't answer is an error, never a no ---------------------------------


def test_an_http_error_is_an_error_not_a_verdict(server) -> None:
    server.status, server.raw = 500, b"model 'test-model' not found"

    with pytest.raises(DescribeError, match=r"HTTP 500.*not found"):
        judge_for(server).judge(crops(), "person", "in red")


@pytest.mark.parametrize(
    "raw",
    [b"not json at all", b"{}", b'{"choices": []}', b'{"choices": [{"message": {}}]}'],
)
def test_a_reply_that_isnt_a_chat_answer_is_an_error(server, raw: bytes) -> None:
    server.raw = raw

    with pytest.raises(DescribeError):
        judge_for(server).judge(crops(), "person", "in red")


def test_a_server_that_is_down_is_an_error() -> None:
    with socket.socket() as s:  # a port nobody listens on
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    with pytest.raises(DescribeError, match="can't get an answer"):
        VlmJudge("m", f"http://127.0.0.1:{port}", timeout=5).judge(crops(), "person", "in red")


def test_a_reply_with_no_text_is_unsure(server) -> None:
    server.raw = json.dumps({"choices": [{"message": {"content": None}}]}).encode()

    assert judge_for(server).judge(crops(), "person", "in red") == "unsure"


# --- the cache key --------------------------------------------------------------------------


def test_the_model_and_the_prompt_are_in_the_settings_but_the_address_is_not() -> None:
    a = VlmJudge("model-a", "http://localhost:11434")
    assert a.settings != VlmJudge("model-b", "http://localhost:11434").settings
    assert a.settings == VlmJudge("model-a", "http://other-host:9999").settings
    assert "prompt" in a.settings


# --- which backend may send crops where ----------------------------------------------------


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in ("VIBE_VLM_MODEL", "VIBE_VLM_URL", "VIBE_REMOTE_HOSTS"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_no_model_named_is_refused_and_says_which_variable(clean_env) -> None:
    with pytest.raises(DescribeError, match="VIBE_VLM_MODEL"):
        vlm_judge("local")


def test_local_talks_to_this_machine_by_default(clean_env) -> None:
    clean_env.setenv("VIBE_VLM_MODEL", "gemma3:12b")

    judge = vlm_judge("local")

    assert isinstance(judge, VlmJudge)
    assert judge.model == "gemma3:12b" and judge.url == "http://localhost:11434"


def test_local_refuses_another_machine(clean_env) -> None:
    clean_env.setenv("VIBE_VLM_MODEL", "m")
    clean_env.setenv("VIBE_VLM_URL", "http://alex-5070:11434")

    with pytest.raises(DescribeError, match="only talks to this machine"):
        vlm_judge("local")


def test_remote_only_sends_crops_to_a_listed_host(clean_env) -> None:
    clean_env.setenv("VIBE_VLM_MODEL", "m")
    clean_env.setenv("VIBE_VLM_URL", "http://alex-5070:11434")

    with pytest.raises(DescribeError, match="VIBE_REMOTE_HOSTS"):
        vlm_judge("remote")

    clean_env.setenv("VIBE_REMOTE_HOSTS", "alex-5070")
    assert isinstance(vlm_judge("remote"), VlmJudge)


def test_a_tunnel_is_an_explicit_opt_in_on_the_remote_backend(clean_env) -> None:
    """A tunnel to a cluster looks like localhost. Under ``remote`` it has to be listed."""
    clean_env.setenv("VIBE_VLM_MODEL", "m")  # default URL: http://localhost:11434
    with pytest.raises(DescribeError, match="VIBE_REMOTE_HOSTS"):
        vlm_judge("remote")

    clean_env.setenv("VIBE_REMOTE_HOSTS", "localhost")
    assert isinstance(vlm_judge("remote"), VlmJudge)


@pytest.mark.parametrize("url", ["localhost:11434", "ftp://localhost", "http://"])
def test_a_url_that_isnt_http_is_refused(clean_env, url: str) -> None:
    clean_env.setenv("VIBE_VLM_MODEL", "m")
    clean_env.setenv("VIBE_VLM_URL", url)

    with pytest.raises(DescribeError, match="VIBE_VLM_URL"):
        vlm_judge("local")


def test_make_judge_builds_the_vlm_judge_on_local_and_remote(clean_env) -> None:
    clean_env.setenv("VIBE_VLM_MODEL", "m")
    clean_env.setenv("VIBE_REMOTE_HOSTS", "localhost")

    assert isinstance(make_judge("local"), VlmJudge)
    assert isinstance(make_judge("remote"), VlmJudge)


# --- through the describer ---------------------------------------------------------------


def red_person() -> Track:
    """Follows the red square in the ``square_clip`` fixture, as a person."""
    keys = [
        Keyframe(
            t=i / FPS,
            box=((4 + 4 * i) / WIDTH, 10 / HEIGHT, (24 + 4 * i) / WIDTH, 30 / HEIGHT),
            conf=0.9,
            state="detected",
        )
        for i in range(10)
    ]
    return Track(track_id="trk_red", label="person", source="detector", keyframes=keys)


def test_the_describer_sends_the_whole_sentence_once_and_caches_the_answer(
    server, tmp_path: Path, square_clip: Path
) -> None:
    store = Storage(tmp_path / "data")
    ref = store.add_video(square_clip)
    describer = VideoDescriber(store, ref.sha256, square_clip, judge_for(server))

    first = describer.ask([red_person()], "in a grey t-shirt with a bag")
    again = describer.ask([red_person()], "In a grey t-shirt with a bag.")

    assert first == again == {"trk_red": "yes"}
    assert len(server.requests) == 1  # one question (not split), and the second was cached
    content = server.requests[0]["body"]["messages"][0]["content"]
    assert '"in a grey t-shirt with a bag"' in content[0]["text"]
    assert len([p for p in content if p["type"] == "image_url"]) == 3


def test_a_down_server_through_the_describer_is_an_error_not_a_no(
    tmp_path: Path, square_clip: Path
) -> None:
    store = Storage(tmp_path / "data")
    ref = store.add_video(square_clip)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    judge = VlmJudge("m", f"http://127.0.0.1:{port}", timeout=5)
    describer = VideoDescriber(store, ref.sha256, square_clip, judge)

    with pytest.raises(DescribeError):
        describer.ask([red_person()], "in red")
