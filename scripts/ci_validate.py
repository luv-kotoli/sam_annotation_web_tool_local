"""Portable CI entry point; the hosted job requires an actual macOS ARM64 VM."""
import argparse
from datetime import datetime, timezone
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
import unittest

PROJECT = Path(__file__).resolve().parents[1]
REPORTS = PROJECT / "test-artifacts" / "ci"


class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, text):
        for stream in self.streams:
            stream.write(text)

    def flush(self):
        for stream in self.streams:
            stream.flush()


def capture(command):
    return subprocess.run(command, cwd=PROJECT, text=True, capture_output=True, check=True, timeout=30).stdout.strip()


def environment(require_macos_arm64):
    system, architecture = platform.system(), platform.machine()
    value = {
        "schema": "local-annotation.ci-environment.v1",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "system": system,
        "architecture": architecture,
        "python": platform.python_version(),
        "commit": capture(["git", "rev-parse", "HEAD"]),
        "require_macos_arm64": require_macos_arm64,
        "torch_installed": importlib.util.find_spec("torch") is not None,
        "packages": {},
        "tools": {},
    }
    (REPORTS / "environment.json").write_text(json.dumps(value, indent=2) + "\n")
    if require_macos_arm64 and (system != "Darwin" or architecture != "arm64"):
        raise RuntimeError(f"Expected macOS ARM64, received {system} {architecture}")
    if system == "Darwin":
        value["macos"] = capture(["sw_vers"])
    for package in ("fastapi", "uvicorn", "httpx", "playwright"):
        try:
            value["packages"][package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            value["packages"][package] = None
    for name, argument in (("ffmpeg", "-version"), ("ffprobe", "-version"), ("node", "--version")):
        executable = shutil.which(name)
        if not executable:
            raise RuntimeError(f"Required CI tool missing: {name}")
        value["tools"][name] = capture([executable, argument]).splitlines()[0]
    encoders = capture(["ffmpeg", "-hide_banner", "-encoders"])
    if "libx264" not in encoders or "mjpeg" not in encoders:
        raise RuntimeError("FFmpeg must support libx264 and mjpeg for the real video tests")
    (REPORTS / "environment.json").write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps(value, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-macos-arm64", action="store_true")
    args = parser.parse_args()
    REPORTS.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    report = {"schema": "local-annotation.ci-tests.v1", "passed": False, "tests_run": 0}
    try:
        environment(args.require_macos_arm64)
        sources = [PROJECT / "app.py", PROJECT / "run.py", PROJECT / "case_bundle.py", PROJECT / "local_annotation"]
        subprocess.run([sys.executable, "-m", "compileall", "-q", *map(str, sources)], cwd=PROJECT, check=True)
        for script in sorted((PROJECT / "local_annotation" / "assets").glob("*.js")):
            subprocess.run(["node", "--check", str(script)], cwd=PROJECT, check=True)
        sys.path.insert(0, str(PROJECT))
        suite = unittest.defaultTestLoader.discover(str(PROJECT / "tests"), pattern="test_*.py")
        with (REPORTS / "unit-tests.log").open("w") as logfile:
            result = unittest.TextTestRunner(stream=Tee(sys.stderr, logfile), verbosity=2).run(suite)
        report.update(tests_run=result.testsRun, failures=len(result.failures), errors=len(result.errors), skipped=len(result.skipped))
        report["passed"] = result.testsRun > 0 and result.wasSuccessful() and not result.skipped
        if result.skipped:
            report["reason"] = "Skipped video checks do not establish macOS compatibility"
    except Exception as error:
        report["reason"] = str(error)
        print(f"CI validation failed: {error}", file=sys.stderr)
    finally:
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        (REPORTS / "unit-report.json").write_text(json.dumps(report, indent=2) + "\n")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
