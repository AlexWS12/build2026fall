"""``uv run python -m vibe.modules <command>``: compute setup (F-08).

doctor [--json]                 what this machine can run: GPU, driver, PyTorch, tier
models                          the weights `download` knows, with license and tier
download NAME... [--pin]        fetch weights into VIBE_MODELS_DIR, checking sha256
cache SHA256 [KIND]             what's cached for a video (kinds, keys)
serve [--host H] [--port P]     run the model server for the `remote` backend
"""

import argparse
import json
import sys

from vibe.modules.cache import ModuleError


def _doctor(args: argparse.Namespace) -> int:
    from vibe.modules import doctor

    hw = doctor.probe()
    print(json.dumps(hw.as_dict(), indent=2) if args.json else doctor.report(hw))
    return 0


def _models(_: argparse.Namespace) -> int:
    from vibe.modules.download import manifest

    for e in manifest().values():
        pinned = "" if e.source != "url" else (" (sha256 pinned)" if e.sha256 else " (unpinned)")
        print(f"{e.name:<14} tier {e.min_tier}+  {e.source:<8} {e.license}{pinned}")
        print(f"{'':<14} {e.purpose}")
    return 0


def _download(args: argparse.Namespace) -> int:
    from vibe.modules.download import download, manifest

    entries = manifest()
    failed = 0
    for name in args.names:
        entry = entries.get(name)
        if entry is None:
            print(f"error: unknown model {name!r}; see `models`", file=sys.stderr)
            failed += 1
            continue
        try:
            path, digest = download(entry, pin=args.pin)
        except ModuleError as e:
            print(f"error: {e}", file=sys.stderr)
            failed += 1
            continue
        print(f"{name}: {path}")
        if entry.sha256 is None:
            print(f"  sha256 {digest}  <- check it, then pin it in models.json")
    return 1 if failed else 0


def _cache(args: argparse.Namespace) -> int:
    from vibe.modules.cache import cached_keys
    from vibe.storage import Storage, StorageError

    storage = Storage()
    try:
        folder = storage.cache_path(args.sha256, "x", "x").parent.parent
    except (StorageError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if not folder.is_dir():
        print(f"nothing cached for {args.sha256[:12]} in {storage.cache_dir}")
        return 0
    kinds = [args.kind] if args.kind else sorted(p.name for p in folder.iterdir() if p.is_dir())
    if not kinds:
        print(f"nothing cached for {args.sha256[:12]} in {storage.cache_dir}")
        return 0
    for kind in kinds:
        for key in cached_keys(storage, args.sha256, kind):
            print(f"{kind}/{key}")
    return 0


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    from vibe import config
    from vibe.modules import doctor
    from vibe.modules.remote import create_app

    token = config.remote_token()
    if args.host not in {"127.0.0.1", "localhost", "::1"} and token is None:
        print(
            "warning: listening beyond this machine with no VIBE_REMOTE_TOKEN; only do this on "
            "a private network (e.g. Tailscale) and set a token in .env",
            file=sys.stderr,
        )
    app = create_app(token=token, hardware=lambda: doctor.probe().as_dict())
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m vibe.modules", description="Compute setup: backends, models, cache (F-08)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("doctor", help="what this machine can run: GPU, driver, PyTorch, tier")
    p.add_argument("--json", action="store_true", help="print JSON")
    p.set_defaults(run=_doctor)

    p = sub.add_parser("models", help="weights `download` knows, with license and tier")
    p.set_defaults(run=_models)

    p = sub.add_parser("download", help="fetch weights into VIBE_MODELS_DIR, checking sha256")
    p.add_argument("names", nargs="+", metavar="NAME")
    p.add_argument("--pin", action="store_true", help="allow a model with no pinned sha256")
    p.set_defaults(run=_download)

    p = sub.add_parser("cache", help="what's cached for a video")
    p.add_argument("sha256")
    p.add_argument("kind", nargs="?")
    p.set_defaults(run=_cache)

    p = sub.add_parser("serve", help="run the model server for the remote backend")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.set_defaults(run=_serve)

    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
