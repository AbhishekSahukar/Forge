"""
Single entrypoint to start every backend process Forge needs:
  - Knowledge Agent A2A server   (:9001, internal only)
  - Engineering Agent A2A server (:9002, internal only)
  - Review Agent A2A server      (:9003, internal only)
  - Main FastAPI app + auth      (:$PORT, the only externally exposed one)

Run: python run_all.py
Stop: Ctrl+C — terminates all four child processes cleanly.

Deployment note: main-api now binds to the PORT environment variable if
set (Render, and most PaaS platforms, inject this and expect the app to
listen on it), falling back to 8000 for local development where nothing
sets PORT. The three agent servers keep their fixed ports regardless —
they're never exposed outside this container/machine, so there's no
reason for them to move, and every URL constant that points at them
(KNOWLEDGE_AGENT_URL, ENGINEERING_AGENT_URL, REVIEW_AGENT_URL) stays
"http://localhost:900X/" unchanged. That only works because all four
processes run inside the same network namespace — on Render, that means
the same container; splitting these into separate Render services would
require changing those URL constants to real inter-service hostnames.
"""

import os
import subprocess
import sys
import threading
import time

MAIN_API_PORT = os.environ.get("PORT", "8000")

PROCESSES = [
    ("knowledge-agent", [sys.executable, "-m", "app.a2a.knowledge_agent_server"]),
    ("engineering-agent", [sys.executable, "-m", "app.a2a.engineering_agent_server"]),
    ("review-agent", [sys.executable, "-m", "app.a2a.review_agent_server"]),
    ("main-api", [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", MAIN_API_PORT]),
]

# Colors make it possible to tell processes apart in one merged terminal.
_COLORS = ["\033[36m", "\033[35m", "\033[33m", "\033[32m"]
_RESET = "\033[0m"


def _stream_output(name: str, proc: subprocess.Popen, color: str):
    for line in iter(proc.stdout.readline, b""):
        if not line:
            break
        text = line.decode(errors="replace").rstrip()
        print(f"{color}[{name}]{_RESET} {text}")


def main():
    children: list[tuple[str, subprocess.Popen]] = []
    threads: list[threading.Thread] = []

    print(f"Starting Forge backend (4 processes, main-api on port {MAIN_API_PORT})...\n")

    for i, (name, cmd) in enumerate(PROCESSES):
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        children.append((name, proc))
        t = threading.Thread(
            target=_stream_output, args=(name, proc, _COLORS[i % len(_COLORS)]), daemon=True
        )
        t.start()
        threads.append(t)
        time.sleep(1.5)

    print(f"\nAll processes started. Main API on port {MAIN_API_PORT}.")
    print("Press Ctrl+C to stop everything.\n")

    try:
        while True:
            time.sleep(1)
            for name, proc in children:
                if proc.poll() is not None:
                    print(f"\n!! {name} exited unexpectedly (code {proc.returncode}). "
                          f"Check the log lines above for the error.")
    except KeyboardInterrupt:
        print("\nStopping all processes...")
        for name, proc in children:
            proc.terminate()
        for name, proc in children:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        print("Stopped.")


if __name__ == "__main__":
    main()