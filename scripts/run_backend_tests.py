#!/usr/bin/env python3
"""Run backend checks in a disposable source snapshot with owned cleanup.

Requires only host Python 3.8+, Git, Docker and already-built backend/Mongo images.
Never mounts production source, credentials, sockets, logs or database volumes.
"""
import argparse
import json
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
import uuid


LABEL = "sereinfish.test.run"


def command(args, **kwargs):
    return subprocess.run(args, check=True, text=True, capture_output=True, **kwargs).stdout.strip()


def snapshot_source(repo, destination):
    files = command(["git", "-C", str(repo), "ls-files", "-z", "--cached", "--others",
                     "--exclude-standard", "--", "server"]).split("\0")
    copied = 0
    for relative in dict.fromkeys(files):
        if not relative:
            continue
        path = Path(relative)
        if path.name == ".env" or (path.name.startswith(".env.") and not path.name.endswith(".example")):
            continue
        source = repo / path
        if source.is_symlink():
            raise ValueError("Test snapshots do not follow source symlinks: " + relative)
        if not source.is_file():
            continue
        target = destination / path.relative_to("server")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied += 1
    return copied


class TestResources:
    def __init__(self, run_id):
        self.run_id = run_id
        self.containers = []
        self.network = ""

    def create_container(self, name, args):
        command(["docker", "create", "--name", name, "--label", LABEL + "=" + self.run_id, *args])
        self.containers.append(name)

    def create_mongo(self):
        name = "sereinfish-test-mongo-" + self.run_id
        self.network = "sereinfish-tests-" + self.run_id
        command(["docker", "network", "create", "--internal", "--label", LABEL + "=" + self.run_id, self.network])
        self.create_container(name, ["--network", self.network, "--tmpfs", "/data/db:size=1g",
                                     "--tmpfs", "/data/configdb:size=32m", "mongo:7"])
        command(["docker", "start", name])
        return "mongodb://" + name + ":27017"

    def cleanup(self):
        errors = []
        for name in reversed(self.containers):
            result = subprocess.run(["docker", "inspect", "--format", '{{index .Config.Labels "' + LABEL + '"}}', name],
                                    text=True, capture_output=True)
            if result.returncode == 0 and result.stdout.strip() == self.run_id:
                removed = subprocess.run(["docker", "rm", "-f", name], capture_output=True)
                if removed.returncode:
                    errors.append(name)
        if self.network:
            result = subprocess.run(["docker", "network", "rm", self.network], capture_output=True)
            if result.returncode:
                errors.append(self.network)
        return errors


def execute(args, source, resources, log_path):
    network, mongo_uri = "none", ""
    if args.mongo:
        mongo_uri = resources.create_mongo()
        network = resources.network
    name = "sereinfish-test-backend-" + resources.run_id
    options = ["--init", "--network", network, "--read-only", "--tmpfs", "/tmp",
               "--mount", "type=bind,src=" + str(source) + ",dst=/app,readonly", "--workdir", "/app"]
    env = {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": "/app", "SEREINFISH_RUN_KIND": "test",
           "LOG_DIR": "/tmp/test-logs", "OBS_LOG_DIR": "/tmp/test-observability",
           "MONGODB_URI": mongo_uri or "mongodb://127.0.0.1:1", "MONGODB_DATABASE": "isolated_tests"}
    if mongo_uri:
        env["SEREINFISH_TEST_MONGODB_URI"] = mongo_uri
    for key, value in env.items():
        options.extend(["-e", key + "=" + value])
    selection = args.tests or ["test_server/tests"]
    resources.create_container(name, [*options, args.image, "python", "-m", "pytest", "-q",
                                      "-p", "no:cacheprovider", *selection])
    with log_path.open("w") as output:
        process = subprocess.Popen(["docker", "start", "--attach", name], stdout=output, stderr=subprocess.STDOUT)
        try:
            return process.wait(timeout=args.timeout)
        except subprocess.TimeoutExpired:
            output.write("\nIsolated test deadline exceeded. Owned containers will be removed.\n")
            return 124
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def main():
    def interrupted(_signum, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGHUP, interrupted)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--image", default="sere1nfish_backend")
    parser.add_argument("--mongo", action="store_true", help="Use a private disposable MongoDB for opt-in contracts")
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--output", type=Path, required=True, help="Directory outside the deployed source for logs and manifest")
    parser.add_argument("tests", nargs="*", help="pytest paths/selectors relative to server/")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    repo = Path(command(["git", "-C", str(args.repo), "rev-parse", "--show-toplevel"]))
    output = args.output.resolve()
    if output == repo or repo in output.parents:
        parser.error("--output must be outside the source repository")
    output.mkdir(parents=True, exist_ok=True)
    image_id = command(["docker", "image", "inspect", "--format", "{{.Id}}", args.image])
    if args.mongo:
        command(["docker", "image", "inspect", "--format", "{{.Id}}", "mongo:7"])
    run_id = uuid.uuid4().hex[:12]
    resources = TestResources(run_id)
    log_path = output / (run_id + ".log")
    started, code, count = time.monotonic(), 1, 0
    errors = []
    try:
        with tempfile.TemporaryDirectory(prefix="sereinfish-tests-") as temporary:
            source = Path(temporary)
            count = snapshot_source(repo, source)
            try:
                code = execute(args, source, resources, log_path)
            finally:
                errors = resources.cleanup()
    except KeyboardInterrupt:
        code = 130
    finally:
        report = {"run_id": run_id, "run_kind": "test", "image_id": image_id,
                  "source_files": count, "elapsed_seconds": round(time.monotonic() - started, 3),
                  "exit_code": code, "cleanup_errors": errors, "log": str(log_path)}
        (output / (run_id + ".json")).write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report))
    return code or (1 if errors else 0)


if __name__ == "__main__":
    raise SystemExit(main())
