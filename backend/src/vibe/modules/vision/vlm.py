"""The VLM description judge (F-13, D-020): ask a vision-language model about a track's crops.

    crops of one track ──► one chat request with N images ──► "yes" / "no" / "unsure"

A :class:`~vibe.modules.vision.describe.Judge` for the ``local`` and ``remote`` backends. It talks
to any server that speaks the OpenAI chat API with images (Ollama, llama.cpp, vLLM) using only
the standard library, so it adds no dependency. Which model to use is D-020, still Proposed: the
model is ``VIBE_VLM_MODEL`` and there is no default here.

Fail closed, like the rest of F-13:

- The model is asked to say ``unsure`` whenever it can't tell, and any reply that isn't exactly
  one of the three words counts as ``unsure``: a model that rambles is not saying "no".
- Fewer than ``MIN_CROPS`` crops is ``unsure`` without asking.
- A server that can't answer (down, HTTP error, timeout, nonsense) raises ``DescribeError``,
  never ``no``.

Where the crops go (D-017): crops are media. ``local`` only talks to this machine; ``remote``
only to a host listed in ``VIBE_REMOTE_HOSTS``. A tunnel to a machine you don't control looks
like ``localhost``, so for one use ``VIBE_BACKEND=remote`` and list ``localhost``: that is the
explicit opt-in, and the person who sets the tunnel up owns that decision.

Not tested here: any real model. Whether a model says a confident wrong ``no`` is what the
benchmark has to measure (D-020).
"""

import base64
import http.client
import json
import math
import struct
import urllib.error
import urllib.request
import zlib
from collections.abc import Sequence
from urllib.parse import urlsplit

import numpy as np

from vibe import config
from vibe.config import Backend
from vibe.modules.vision.describe import MIN_CROPS, DescribeError, Judge, Verdict

PROMPT_VERSION = 1
"""In the judge's settings, so in every cache key. Bump it when the prompt changes."""

MAX_SIDE = 512
"""Crops with a longer side are shrunk to about this before sending: a VLM resizes them anyway,
and a person crop from a 4K frame is megabytes."""

DEFAULT_TIMEOUT = 180.0
"""Seconds. The first request to a server may include loading the model onto the GPU."""

LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_ANSWERS: tuple[Verdict, ...] = ("yes", "no", "unsure")
_QUOTES = " \t\r\n*_`\"'.,:;!"

_PROMPT = (
    "You are checking one tracked {label} in a video. The {count} images are crops of that same "
    "track at different moments.\n"
    'Question: does it match this description: "{description}"?\n'
    "Reply with exactly one word:\n"
    "yes - only if it clearly matches in every image;\n"
    "no - only if it clearly does not match;\n"
    "unsure - if you cannot tell for any reason (small, blurry, hidden, cut off, only partly "
    "visible, the images disagree) or if the description does not apply to what you see.\n"
    "When in doubt, answer unsure. Do not try to say who anyone is. One word only."
)


def prompt(label: str, description: str, count: int) -> str:
    return _PROMPT.format(label=label, description=description, count=count)


def parse_verdict(text: str) -> Verdict:
    """The model's reply as a verdict: only exactly ``yes``, ``no`` or ``unsure`` (any case,
    with stray punctuation or markdown) counts. Anything else, "no, but ..." or "probably", is
    ``unsure``: that is what masks."""
    word = text.strip(_QUOTES).lower()
    for answer in _ANSWERS:
        if word == answer:
            return answer
    return "unsure"


def shrink(crop: np.ndarray, max_side: int = MAX_SIDE) -> np.ndarray:
    """``crop`` with its longer side at most about ``max_side`` (averaging blocks of pixels)."""
    height, width = crop.shape[:2]
    k = math.ceil(max(height, width) / max_side)
    if k <= 1:
        return crop
    if min(height, width) < k:  # a sliver: blocks would be empty
        return crop[::k, ::k]
    h, w = height // k * k, width // k * k
    blocks = crop[:h, :w].reshape(h // k, k, w // k, k, crop.shape[2])
    return blocks.mean(axis=(1, 3)).round().astype(np.uint8)


def encode_png(rgb: np.ndarray) -> bytes:
    """A PNG of an (h, w, 3) uint8 image, from the standard library alone."""
    if rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3 or 0 in rgb.shape:
        raise ValueError(f"need a non-empty (h, w, 3) uint8 image, got {rgb.dtype} {rgb.shape}")
    height, width = rgb.shape[:2]
    rows = np.zeros((height, 1 + width * 3), dtype=np.uint8)  # each row starts with filter 0
    rows[:, 1:] = rgb.reshape(height, width * 3)

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit RGB
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows.tobytes(), 6))
        + chunk(b"IEND", b"")
    )


class VlmJudge(Judge):
    """Asks a VLM whether a track's crops match a description. One request per track, all the
    crops in it. Reads whole sentences (``compound``), so "in grey with a bag" is one question."""

    name = "vlm"
    version = 1
    compound = True

    def __init__(
        self,
        model: str,
        url: str = "http://localhost:11434",
        timeout: float = DEFAULT_TIMEOUT,
        max_side: int = MAX_SIDE,
        max_tokens: int = 16,
    ) -> None:
        if not model:
            raise ValueError("a VLM judge needs a model name")
        # The server's address is left out of the settings: the same model answers the same.
        super().__init__(
            model=model, prompt=PROMPT_VERSION, max_side=max_side, max_tokens=max_tokens
        )
        self.model, self.url, self.timeout = model, url, timeout
        self.max_side, self.max_tokens = max_side, max_tokens

    @property
    def endpoint(self) -> str:
        base = self.url.rstrip("/").removesuffix("/v1")
        return f"{base}/v1/chat/completions"

    def judge(self, crops: Sequence[np.ndarray], label: str, description: str) -> Verdict:
        if len(crops) < MIN_CROPS:
            return "unsure"  # one look is never enough for a sure answer
        return parse_verdict(self._ask(self.request(crops, label, description)))

    def request(self, crops: Sequence[np.ndarray], label: str, description: str) -> dict:
        """The chat request body: the question, then one image per crop."""
        images = [
            {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/png;base64,"
                    + base64.b64encode(encode_png(shrink(crop, self.max_side))).decode()
                },
            }
            for crop in crops
        ]
        text = {"type": "text", "text": prompt(label, description, len(crops))}
        return {
            "model": self.model,
            "temperature": 0,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "user", "content": [text, *images]}],
        }

    def _ask(self, body: dict) -> str:
        """The model's reply text. A server that can't answer is an error, never a verdict."""
        req = urllib.request.Request(
            self.endpoint,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                payload = json.loads(response.read())
        except urllib.error.HTTPError as e:
            raise DescribeError(
                f"the VLM server at {self.url} answered HTTP {e.code} for model "
                f"{self.model!r}: {_detail(e)}"
            ) from e
        except (OSError, ValueError, http.client.HTTPException) as e:
            raise DescribeError(
                f"can't get an answer from the VLM server at {self.url}: {e}. Is it running, "
                "and is the tunnel (if any) open?"
            ) from e
        try:
            text = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise DescribeError(f"unexpected reply from the VLM server at {self.url}") from e
        return text if isinstance(text, str) else ""


def _detail(error: urllib.error.HTTPError) -> str:
    try:
        return error.read(300).decode(errors="replace").strip() or str(error.reason)
    except OSError:
        return str(error.reason)


def vlm_judge(backend: Backend) -> VlmJudge:
    """The judge for the ``local`` or ``remote`` backend, from ``VIBE_VLM_MODEL`` and
    ``VIBE_VLM_URL``. Raises :class:`DescribeError` if it isn't set up or the URL is somewhere
    the crops may not go (see the module docstring)."""
    model = config.vlm_model()
    if model is None:
        raise DescribeError(
            f"the {backend!r} description judge needs a model: set VIBE_VLM_MODEL (and "
            "VIBE_VLM_URL if the server isn't http://localhost:11434). Which VLM is D-020, still "
            "Proposed. Set VIBE_BACKEND=stub for clothing colours only."
        )
    url = config.vlm_url()
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise DescribeError(f"VIBE_VLM_URL must be http(s)://host[:port], got {url!r}")
    host = parts.hostname.lower()
    if backend == "local":
        if host not in LOOPBACK_HOSTS:
            raise DescribeError(
                f"the 'local' judge only talks to this machine, not {host!r}. For another "
                "machine use VIBE_BACKEND=remote and list its host in VIBE_REMOTE_HOSTS."
            )
    else:
        from vibe.modules.remote import RemoteNotAllowed, check_allowed

        try:
            check_allowed(url, config.remote_hosts())
        except RemoteNotAllowed as e:
            raise DescribeError(str(e)) from e
    return VlmJudge(model, url)
