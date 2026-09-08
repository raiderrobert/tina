"""The client half: a `Source` whose implementation is a subprocess.

`ConnectorClient` satisfies the same `Source` protocol the in-process adapters
do, so nothing above it — dispatch, run, status, doctor — knows the tracker is
on the other side of a pipe. It spawns the configured command on first use,
performs the `initialize` handshake with the track's options, sends one
request at a time, and maps error responses onto `SourceError`.

Lifecycle is Tina's to own here: a per-request timeout, `shutdown` then a
grace period then `kill`, and a connector that dies mid-conversation reported
with its last stderr lines rather than as a broken pipe.
"""

from __future__ import annotations

import atexit
import collections
import os
import shlex
import subprocess
import sys
import threading
from typing import Any

from tina.connector import protocol
from tina.connector.protocol import Capabilities, Code, InitializeResult, Lifecycle
from tina.log import get_logger
from tina.models import WorkItem
from tina.sources.base import ClaimPrognosis, SourceError

log = get_logger(__name__)

DEFAULT_TIMEOUT = 120.0
SHUTDOWN_GRACE = 5.0
#: How many of the connector's stderr lines are kept to attach to a failure.
STDERR_TAIL = 20


class ConnectorClient:
    """A tracker, reached through a connector process (docs/connector-protocol.md)."""

    def __init__(
        self,
        command: list[str],
        *,
        name: str,
        track: str,
        options: dict[str, Any],
        lifecycle: Lifecycle,
        timeout: float = DEFAULT_TIMEOUT,
        env: dict[str, str] | None = None,
    ) -> None:
        self.command = command
        self.name = name
        self.track = track
        self.options = options
        self.lifecycle = lifecycle
        self.timeout = timeout
        self.env = env
        self._process: subprocess.Popen[str] | None = None
        self._next_id = 0
        self._stderr_tail: collections.deque[str] = collections.deque(maxlen=STDERR_TAIL)
        self._capabilities = Capabilities()
        self._connector: dict[str, str] = {}

    # -- lifecycle ---------------------------------------------------------------

    @property
    def capabilities(self) -> Capabilities:
        self._ensure_started()
        return self._capabilities

    @property
    def connector(self) -> dict[str, str]:
        """Who answered `initialize`: the connector's own name and version."""
        self._ensure_started()
        return self._connector

    def _ensure_started(self) -> None:
        if self._process is not None:
            return
        try:
            self._process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=self.env,
            )
        except OSError as exc:
            raise SourceError(
                f"{self.name}: could not start connector {shlex.join(self.command)}: {exc}",
                fix=f"Install the connector into the image; check [sources.{self.name}] command.",
            ) from exc
        atexit.register(self.close)
        threading.Thread(target=self._relay_stderr, daemon=True).start()
        log.info("connector started", extra={"source": self.name, "command": self.command})

        answer = self._call(
            "initialize",
            {
                "protocol_version": protocol.PROTOCOL_VERSION,
                "client": {"name": "tina", "version": _tina_version()},
                "track": self.track,
                "options": self.options,
                "lifecycle": self.lifecycle.model_dump(),
            },
        )
        initialized = InitializeResult.model_validate(answer)
        self._capabilities = initialized.capabilities
        self._connector = initialized.connector

    def close(self) -> None:
        """`shutdown`, close stdin, wait a grace period, then kill. Idempotent."""
        process, self._process = self._process, None
        if process is None or process.poll() is not None:
            return
        try:
            if process.stdin is not None:
                process.stdin.write(protocol.encode(protocol.notification(protocol.SHUTDOWN)))
                process.stdin.close()
            process.wait(timeout=SHUTDOWN_GRACE)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            process.kill()
            process.wait()

    def __enter__(self) -> ConnectorClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _relay_stderr(self) -> None:
        """The connector's log, prefixed, onto Tina's stderr — and remembered."""
        process = self._process
        if process is None or process.stderr is None:
            return
        for line in process.stderr:
            self._stderr_tail.append(line.rstrip("\n"))
            sys.stderr.write(f"[{self.name}] {line}")
            sys.stderr.flush()

    # -- the request/response core --------------------------------------------------

    def _call(self, method: str, params: dict[str, Any]) -> Any:
        if method != "initialize":
            self._ensure_started()
        process = self._process
        assert process is not None and process.stdin is not None and process.stdout is not None
        self._next_id += 1
        request_id = self._next_id
        try:
            process.stdin.write(protocol.encode(protocol.request(request_id, method, params)))
            process.stdin.flush()
            line = _readline(process, self.timeout)
        except (OSError, ValueError):
            line = ""
        if not line:
            raise self._died(method)

        try:
            message = protocol.decode(line)
        except protocol.ProtocolError as exc:
            raise SourceError(
                f"{self.name}: connector broke the protocol on {method}: {exc}"
            ) from exc
        if message.get("id") != request_id:
            raise SourceError(
                f"{self.name}: connector answered request {message.get('id')!r} to {method} "
                f"(expected {request_id})"
            )
        if "error" in message:
            raise self._error(method, message["error"])
        return message.get("result")

    def _error(self, method: str, error: dict[str, Any]) -> SourceError:
        code = error.get("code")
        message = error.get("message", "")
        data = error.get("data") or {}
        fix = str(data.get("fix", ""))
        if code == Code.UNSUPPORTED_VERSION:
            return SourceError(
                f"{self.name}: connector does not speak protocol version "
                f"{protocol.PROTOCOL_VERSION} (supports {data.get('supported')})",
                fix="Upgrade the connector or tina so the two share a protocol version.",
            )
        if code == Code.INVALID_OPTIONS:
            return OptionsRejected(f"{self.name}: [{self.track}.options]: {message}", fix=fix)
        return SourceError(f"{self.name}: {method}: {message}", fix=fix)

    def _died(self, method: str) -> SourceError:
        process = self._process
        code = process.poll() if process is not None else None
        tail = "\n".join(self._stderr_tail)
        detail = (
            f"exited {code}" if code is not None else f"gave no answer within {self.timeout:g}s"
        )
        if process is not None and code is None:
            process.kill()
        self._process = None
        return SourceError(
            f"{self.name}: connector {detail} during {method}" + (f":\n{tail}" if tail else ""),
            fix="Run the connector by hand, or `tina connector-check`, to see why.",
        )

    # -- the Source contract ----------------------------------------------------------

    def login(self) -> str:
        return str(self._call("login", {})["identity"])

    def query(self, q: str) -> list[WorkItem]:
        return _items(self._call("query", {"query": q}))

    def get(self, item_id: str) -> WorkItem:
        return WorkItem.model_validate(self._call("get", {"id": item_id})["item"])

    def matches(self, item_id: str, q: str) -> bool:
        return bool(self._call("matches", {"id": item_id, "query": q})["matches"])

    def claim(self, item: WorkItem) -> bool:
        return bool(self._call("claim", {"item": item.model_dump(mode="json")})["claimed"])

    def claim_prognosis(self, item: WorkItem) -> ClaimPrognosis:
        answer = self._call("claim_prognosis", {"item": item.model_dump(mode="json")})
        return ClaimPrognosis.model_validate(answer)

    def claimed(self, q: str) -> list[WorkItem]:
        return _items(self._call("claimed", {"query": q}))

    def annotate(self, item: WorkItem, comment: str) -> None:
        self._call("annotate", {"item": item.model_dump(mode="json"), "comment": comment})

    def block(self, item: WorkItem) -> None:
        self._call("block", {"item": item.model_dump(mode="json")})

    # -- optional capabilities -----------------------------------------------------------

    def build_query(self) -> str | None:
        """The connector's query from the track's options, or None if it offers none."""
        if not self.capabilities.build_query:
            return None
        answer = self._call(
            "build_query", {"options": self.options, "lifecycle": self.lifecycle.model_dump()}
        )
        return str(answer["query"])

    def verify_artifact(self, url: str) -> bool | None:
        """Whether the artifact behind `url` exists, per the connector — or None
        when the connector does not offer the check or the URL is not its."""
        if not self.capabilities.verify_artifact:
            return None
        answer = self._call("verify_artifact", {"url": url})
        return None if answer is None else bool(answer["exists"])


class OptionsRejected(SourceError):
    """The connector refused `[<track>.options]` — a config error, surfaced at load."""


def _items(answer: dict[str, Any]) -> list[WorkItem]:
    return [WorkItem.model_validate(item) for item in answer["items"]]


def _readline(process: subprocess.Popen[str], timeout: float) -> str:
    """One line from the connector's stdout, or "" when it does not arrive in time.

    A thread does the blocking read so the timeout is Tina's, not the pipe's.
    """
    stdout = process.stdout
    assert stdout is not None
    box: list[str] = []
    reader = threading.Thread(target=lambda: box.append(stdout.readline()), daemon=True)
    reader.start()
    reader.join(timeout)
    return box[0] if box else ""


def _tina_version() -> str:
    from tina import __version__

    return __version__


def default_env() -> dict[str, str]:
    return dict(os.environ)
