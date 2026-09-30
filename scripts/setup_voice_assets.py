#!/usr/bin/env python3
"""Install pinned browser voice assets without ever handling microphone audio.

Run after ``npm ci --ignore-scripts``. ``--check`` only verifies local files:
it performs no downloads, copies, or status-file writes. Python stdlib only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import tempfile
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "config" / "voice-assets.json"
OUTPUT = ROOT / "static" / "voice"
CHUNK_SIZE = 1024 * 1024
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
REVISION = re.compile(r"[0-9a-f]{40}\Z")
REPO_ID = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
SOURCE_HOSTS = {"huggingface.co", "raw.githubusercontent.com", "creativecommons.org"}


class AssetError(RuntimeError):
    """An asset could not be safely installed or verified."""


def safe_path(base: Path, relative: str) -> Path:
    """Reject traversal and paths escaping through an existing symlink/junction."""
    if not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative:
        raise AssetError("Invalid relative asset path")
    raw_parts = relative.split("/")
    if any(part in ("", ".", "..") for part in raw_parts):
        raise AssetError(f"Invalid asset path: {relative}")
    path = PurePosixPath(relative)
    if path.is_absolute():
        raise AssetError(f"Absolute asset path is not allowed: {relative}")
    base = base.resolve()
    target = base.joinpath(*path.parts)
    if not target.resolve().is_relative_to(base):
        raise AssetError(f"Asset path leaves its directory: {relative}")
    return target


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(CHUNK_SIZE), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_file(path: Path, item: dict) -> str | None:
    try:
        if not path.is_file():
            return "missing"
        if path.stat().st_size != item["size_bytes"]:
            return "size_mismatch"
        if digest_file(path) != item["sha256"]:
            return "sha256_mismatch"
    except OSError:
        return "unreadable"
    return None


def validate_file(item: dict) -> None:
    if type(item.get("size_bytes")) is not int or item["size_bytes"] <= 0:
        raise AssetError("Every asset must have a positive, pinned size_bytes")
    if not isinstance(item.get("sha256"), str) or not SHA256.fullmatch(item["sha256"]):
        raise AssetError("Every asset must have a pinned SHA256")


def validate_url(url: str) -> None:
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in SOURCE_HOSTS
            or parsed.username or parsed.password or parsed.fragment):
        raise AssetError("Asset downloads must use an approved public HTTPS source")


def load_manifest(path: Path = MANIFEST) -> tuple[dict, list[dict], str]:
    if not OUTPUT.resolve().is_relative_to(ROOT.resolve()):
        raise AssetError("The voice output directory must remain inside the project")
    raw = path.read_bytes()
    manifest = json.loads(raw)
    if manifest.get("schema_version") != 1:
        raise AssetError("Unsupported voice asset manifest version")
    runtime = manifest["runtime"]
    if runtime["package"] != "@huggingface/transformers" or runtime["version"] != "3.8.1":
        raise AssetError("Unexpected Transformers.js package/version")
    if not runtime["files"] or not manifest["models"]:
        raise AssetError("The manifest must include runtime and model assets")

    records = []
    for item in runtime["files"]:
        if not item["source"].startswith("node_modules/"):
            raise AssetError("Runtime copies must come from node_modules")
        safe_path(ROOT, item["source"])
        records.append({**item, "group": "runtime"})
    model_ids = set()
    for model in manifest["models"]:
        if model["id"] in model_ids or not model["files"]:
            raise AssetError("Duplicate model ID or empty model file list")
        model_ids.add(model["id"])
        if not REPO_ID.fullmatch(model["repo"]) or not REVISION.fullmatch(model["revision"]):
            raise AssetError("Models must use a repository ID and an immutable commit revision")
        for item in model["files"]:
            safe_path(OUTPUT, item["path"])
            url = (f"https://huggingface.co/{model['repo']}/resolve/"
                   f"{model['revision']}/{quote(item['path'], safe='/')}")
            records.append({**item, "path": f"models/{model['repo']}/{item['path']}",
                            "url": url, "group": model["id"]})
    for item in manifest.get("attribution_files", []):
        group = "runtime"
        for model in manifest["models"]:
            if item["path"].startswith(f"models/{model['repo']}/"):
                group = model["id"]
                break
        records.append({**item, "group": group})

    seen = set()
    for item in records:
        validate_file(item)
        safe_path(OUTPUT, item["path"])
        # Windows also treats differing case as the same destination.
        key = item["path"].casefold()
        if key in seen or key == "assets-status.json":
            raise AssetError(f"Duplicate or reserved asset destination: {item['path']}")
        seen.add(key)
        if "url" in item:
            validate_url(item["url"])
    return manifest, records, hashlib.sha256(raw).hexdigest()


class HTTPSRedirectsOnly(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Hugging Face weights legitimately redirect to signed CDN URLs.
        # Do not print those URLs, send credentials, or permit HTTPS downgrade.
        parsed = urlsplit(newurl)
        if parsed.scheme != "https" or parsed.username or parsed.password:
            raise AssetError("Refused non-HTTPS asset redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def atomic_install(stream, item: dict, output: Path = OUTPUT) -> None:
    target = safe_path(output, item["path"])
    target.parent.mkdir(parents=True, exist_ok=True)
    safe_path(output, item["path"])
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=target.parent,
                                         prefix=f".{target.name}.", suffix=".part",
                                         delete=False) as temp:
            temp_path = Path(temp.name)
            size = 0
            digest = hashlib.sha256()
            while True:
                block = stream.read(min(CHUNK_SIZE, item["size_bytes"] - size + 1))
                if not block:
                    break
                size += len(block)
                if size > item["size_bytes"]:
                    raise AssetError(f"Downloaded file is too large: {item['path']}")
                digest.update(block)
                temp.write(block)
            if size != item["size_bytes"] or digest.hexdigest() != item["sha256"]:
                raise AssetError(f"Size/SHA256 verification failed: {item['path']}")
            temp.flush()
            os.fsync(temp.fileno())
        # An old valid file survives interrupted downloads and failed hashes.
        safe_path(output, item["path"])
        os.replace(temp_path, target)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def install_file(item: dict, opener, timeout: float, retries: int) -> None:
    target = safe_path(OUTPUT, item["path"])
    if verify_file(target, item) is None:
        print(f"[verified] {item['path']}", flush=True)
        return
    if "source" in item:
        source = safe_path(ROOT, item["source"])
        if verify_file(source, item) is not None:
            raise AssetError(f"Installed package asset differs or is missing: {item['source']}; "
                             "run npm ci --ignore-scripts using the committed lockfile")
        print(f"[copy] {item['path']}", flush=True)
        with source.open("rb") as stream:
            atomic_install(stream, item)
        return

    print(f"[download] {item['path']} ({item['size_bytes']:,} bytes)", flush=True)
    for attempt in range(1, retries + 1):
        try:
            request = Request(item["url"], headers={
                "User-Agent": "DASLab-Local-Voice-Assets/1.0",
                "Accept-Encoding": "identity",
            })
            with opener.open(request, timeout=timeout) as response:
                if response.status != 200:
                    raise AssetError(f"Unexpected HTTP status {response.status}")
                content_length = response.headers.get("Content-Length")
                if content_length is not None and int(content_length) != item["size_bytes"]:
                    raise AssetError(f"Unexpected Content-Length for {item['path']}")
                atomic_install(response, item)
            return
        except (OSError, URLError, HTTPError, AssetError, ValueError) as exc:
            # Avoid logging a signed CDN URL or a proxy's credential-bearing error.
            reason = f"HTTP {exc.code}" if isinstance(exc, HTTPError) else type(exc).__name__
            if attempt == retries:
                raise AssetError(f"Could not install {item['path']} ({reason}); "
                                 "existing files were preserved") from None
            print(f"[retry {attempt}/{retries}] {item['path']} ({reason})", flush=True)


def asset_status(manifest: dict, records: list[dict], manifest_hash: str,
                 output: Path = OUTPUT) -> dict:
    files = []
    for item in records:
        error = verify_file(safe_path(output, item["path"]), item)
        files.append({"path": item["path"], "sizeBytes": item["size_bytes"],
                      "sha256": item["sha256"], "ready": error is None,
                      "error": error, "group": item["group"]})
    groups = []
    for model in manifest["models"]:
        owned = [item for item in files if item["group"] == model["id"]]
        groups.append({"id": model["id"], "repo": model["repo"],
                       "revision": model["revision"], "dtype": model["dtype"],
                       "license": model["license"], "ready": all(x["ready"] for x in owned),
                       "totalBytes": sum(x["sizeBytes"] for x in owned if x["ready"]),
                       "files": owned})
    runtime_files = [item for item in files if item["group"] == "runtime"]
    ready = all(item["ready"] for item in files)
    return {"schemaVersion": 1, "ready": ready,
            "state": "ready" if ready else "missing_or_invalid",
            "checkedAt": datetime.now(timezone.utc).isoformat(),
            "manifestSha256": manifest_hash,
            "totalBytes": sum(item["sizeBytes"] for item in files if item["ready"]),
            "expectedTotalBytes": sum(item["sizeBytes"] for item in files),
            "models": groups,
            "runtime": {"package": manifest["runtime"]["package"],
                        "version": manifest["runtime"]["version"],
                        "ready": all(item["ready"] for item in runtime_files),
                        "files": runtime_files},
            "missingOrInvalid": [item["path"] for item in files if not item["ready"]]}


def write_status(status: dict, output: Path = OUTPUT) -> None:
    data = (json.dumps(status, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    import io
    atomic_install(io.BytesIO(data), {"path": "assets-status.json", "size_bytes": len(data),
                                     "sha256": hashlib.sha256(data).hexdigest()}, output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify only; no network or writes")
    parser.add_argument("--timeout", type=float, default=60, help="HTTP read timeout in seconds")
    parser.add_argument("--retries", type=int, default=3, help="Download attempts per file (1-5)")
    args = parser.parse_args()
    if not 1 <= args.retries <= 5 or not 1 <= args.timeout <= 600:
        parser.error("retries must be 1-5 and timeout must be 1-600 seconds")
    try:
        manifest, records, manifest_hash = load_manifest()
        if args.check:
            status = asset_status(manifest, records, manifest_hash)
            print(json.dumps(status, ensure_ascii=False, indent=2))
            return 0 if status["ready"] else 1

        status = asset_status(manifest, records, manifest_hash)
        status.update({"ready": False, "state": "installing"})
        write_status(status)
        try:
            opener = build_opener(HTTPSRedirectsOnly())
            for item in records:
                install_file(item, opener, args.timeout, args.retries)
            status = asset_status(manifest, records, manifest_hash)
            write_status(status)
            if not status["ready"]:
                raise AssetError("Final local asset verification failed")
        except (Exception, KeyboardInterrupt) as exc:
            status = asset_status(manifest, records, manifest_hash)
            status.update({"ready": False, "state": "failed", "error": type(exc).__name__})
            write_status(status)
            raise
        print(f"Voice assets ready: {len(records)} files, {status['totalBytes']:,} bytes. "
              "This verifies files, not recognition accuracy.")
        return 0
    except KeyboardInterrupt:
        print("Voice asset setup interrupted; rerun to resume verified files.", file=sys.stderr)
        return 130
    except (AssetError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Voice asset setup failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
