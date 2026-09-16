"""
Single entrypoint to start every backend process Forge needs:
  - Knowledge Agent A2A server   (:9001)
  - Engineering Agent A2A server (:9002)
  - Review Agent A2A server      (:9003)
  - Main FastAPI app + auth      (:8000)

Run: python run_all.py
Stop: Ctrl+C — terminates all four child processes cleanly.

This replaces running four separate terminal commands. It's a plain
subprocess supervisor (not a process manager like supervisord/pm2) —
appropriate for local development; a real deployment would run each
service as its own container/process instead of using this script.
"""

import subprocess
import sys
import threading
import time

PROCESSES = [
    ("knowledge-agent", [sys.executable, "-m", "app.a2a.knowledge_agent_server"]),
    ("engineering-agent", [sys.executable, "-m", "app.a2a.engineering_agent_server"]),
    ("review-agent", [sys.executable, "-m", "app.a2a.review_agent_server"]),
    ("main-api", [sys.executable, "-m", "uvicorn", "app.main:app", "--port", "8000"]),
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

    print("Starting Forge backend (4 processes)...\n")

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
        # Small stagger so agent servers are up before anything tries
        # to hit them; the main API doesn't strictly need this, but it
        # keeps the interleaved log output readable on startup.
        time.sleep(1.5)

    print("\nAll processes started. Main API: http://localhost:8000/docs")
    print("Press Ctrl+C to stop everything.\n")

    try:
        while True:
            time.sleep(1)
            for name, proc in children:
                if proc.poll() is not None:
                    print(f"\n!! {name} exited unexpectedly (code {proc.returncode}). "
                          f"Check the log lines above for the error, then Ctrl+C and fix it.")
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