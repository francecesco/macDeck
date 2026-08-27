"""Esecuzione di processi esterni.

Unico modulo dell'agent che tocca subprocess. Non solleva mai: ogni
fallimento diventa un Result con ok=False, perche' un'azione che va male non
deve poter abbattere il server.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass
class Result:
    ok: bool
    out: str = ""
    error: str | None = None


class Executor:
    def run(self, argv: list[str], timeout: float = 5.0) -> Result:
        try:
            p = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return Result(False, error=f"timeout dopo {timeout}s: {argv[0]}")
        except OSError as e:
            return Result(False, error=f"{type(e).__name__}: {e}")
        if p.returncode != 0:
            msg = (p.stderr or p.stdout or "").strip()
            return Result(False, out=p.stdout, error=msg or f"exit {p.returncode}")
        return Result(True, out=p.stdout)

    def osascript(self, script: str, timeout: float = 5.0) -> Result:
        return self.run(["/usr/bin/osascript", "-e", script], timeout=timeout)

    def shell(self, cmd: str, cwd: str | None = None, timeout: float = 5.0) -> Result:
        argv = ["/bin/sh", "-c", cmd]
        if cwd:
            argv = ["/bin/sh", "-c", f"cd {cwd!r} && ({cmd})"]
        return self.run(argv, timeout=timeout)
