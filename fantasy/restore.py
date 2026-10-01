"""Restore sealed public artifacts without downloading, fitting or evaluating plays."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory

from fantasy.config import ROOT
from fantasy.server import opportunity_method

BUNDLE = ROOT / "artifacts/fantasy/expected-points/v1"
ARTIFACTS = ("model.json", "freeze.json", "validation.json", "holdout.json")


def verified_files(bundle):
    """Read and check every packaged byte before touching a destination."""
    bundle = Path(bundle)
    files = {}
    for line in (bundle / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split("  ", 1)
        path = PurePosixPath(name)
        if (path.is_absolute() or ".." in path.parts or name in files
                or str(path) != name or len(digest) != 64):
            raise ValueError("Invalid checksum entry")
        source = bundle / name
        if source.is_symlink() or not source.resolve().is_relative_to(bundle.resolve()):
            raise ValueError("Bundle paths must stay inside the bundle")
        data = source.read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError(f"Checksum mismatch: {name}")
        files[name] = data
    actual = {str(p.relative_to(bundle)) for p in bundle.rglob("*") if p.is_file()}
    if actual != set(files) | {"SHA256SUMS"}:
        raise ValueError("Bundle inventory differs from checksums")
    manifest = json.loads(files["manifest.json"])
    if manifest["bundle_version"] != 1:
        raise ValueError("Unsupported bundle version")
    if set(manifest["files"]) != set(files) - {"manifest.json"}:
        raise ValueError("Manifest inventory mismatch")
    for name, receipt in manifest["files"].items():
        if (hashlib.sha256(files[name]).hexdigest() != receipt["sha256"]
                or len(files[name]) != receipt["bytes"]):
            raise ValueError(f"Manifest mismatch: {name}")
    for name, digest in manifest["repository_files"].items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"Incompatible repository file: {name}")
    return files


def restore(cache, bundle=BUNDLE):
    """Restore only four JSON artifacts; refuse to replace different local evidence."""
    files = verified_files(bundle)
    cache = Path(cache)
    with TemporaryDirectory(prefix="fantasy-artifact-check-") as temporary:
        root = Path(temporary) / "expected-points"
        root.mkdir()
        for name in ARTIFACTS:
            (root / name).write_bytes(files[name])
        status, model = opportunity_method({"cache": Path(temporary)})
        if model is None or not status["display_supported"]:
            raise ValueError(f"Existing API integrity check rejected bundle: {status}")
    destination = cache / "expected-points"
    if destination.exists():
        if any(not (destination / n).is_file() or (destination / n).is_symlink()
               or (destination / n).read_bytes() != files[n] for n in ARTIFACTS):
            raise ValueError("Existing artifacts differ; nothing replaced")
        return status
    cache.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".restore-", dir=cache) as temporary:
        staged = Path(temporary) / "expected-points"
        staged.mkdir()
        for name in ARTIFACTS:
            (staged / name).write_bytes(files[name])
        staged.rename(destination)
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=ROOT / ".fantasy-cache")
    args = parser.parse_args()
    status = restore(args.cache_dir)
    print(json.dumps({k: status[k] for k in ("status", "model_sha256", "frozen_at")}))


if __name__ == "__main__":
    main()
