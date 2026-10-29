"""Publish an honest job summary, including missing or incomplete checks."""
import json
import os
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]


def read(relative):
    try:
        return json.loads((PROJECT / relative).read_text())
    except (OSError, ValueError):
        return {}


def main():
    environment = read("test-artifacts/ci/environment.json")
    units = read("test-artifacts/ci/unit-report.json")
    browser = read("test-artifacts/browser/report.json")
    devices = read("test-artifacts/ci/compute-devices.json")
    macos_arm64 = environment.get("system") == "Darwin" and environment.get("architecture") == "arm64"
    passed = macos_arm64 and units.get("passed") is True and browser.get("passed") is True
    lines = [
        "## macOS ARM64 compatibility validation",
        "",
        "Result: **" + ("passed" if passed else "failed or incomplete") + "**",
        "",
        f"- Commit: `{environment.get('commit', 'not captured')}`",
        f"- Environment: {environment.get('system', 'unknown')} / {environment.get('architecture', 'unknown')} / Python {environment.get('python', 'unknown')}",
        f"- API and extraction tests: {units.get('tests_run', 0)} run; {units.get('failures', 0)} failures; {units.get('errors', 0)} errors; {units.get('skipped', 0)} skipped",
        "- Browser workflow: " + ("passed" if browser.get("passed") is True else "failed or no result"),
        "- Core ML devices visible: " + ", ".join(devices.get("coreml_devices", [])),
        "- Real model / MPS / Neural Engine inference: **not tested**",
        "",
        "Only generated, nonclinical videos and isolated databases are used. The original gateway is not contacted.",
        "Download `macos-arm64-validation` for logs, reports and screenshots. Artifact retention: 7 days.",
    ]
    if units.get("reason"):
        lines.extend(["", "Validation note: " + units["reason"]])
    if browser.get("failure"):
        lines.extend(["", "Browser failure: " + browser["failure"]])
    output = "\n".join(lines) + "\n"
    print(output)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        notices = {
            "Execution environment": {key: environment.get(key) for key in ("system", "architecture", "python", "commit")},
            "API and extraction test result": units,
            "Apple device visibility (not inference)": devices,
        }
        for title, value in notices.items():
            detail = json.dumps(value, ensure_ascii=False).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
            print(f"::notice title={title}::{detail}")
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a") as handle:
            handle.write(output)


if __name__ == "__main__":
    main()
