import pytest

from macdeck.executor import Executor, Result


class FakeExecutor(Executor):
    """Registra le chiamate invece di eseguirle.

    `replies` mappa una sottostringa del comando al Result da restituire;
    la prima chiave contenuta nel comando vince. Senza corrispondenze
    risponde ok.
    """

    def __init__(self, replies: dict[str, Result] | None = None):
        self.calls: list[tuple[str, ...]] = []
        self.replies = replies or {}

    def run(self, argv: list[str], timeout: float = 5.0) -> Result:
        self.calls.append(tuple(argv))
        joined = " ".join(argv)
        for needle, reply in self.replies.items():
            if needle in joined:
                return reply
        return Result(True)

    @property
    def scripts(self) -> list[str]:
        """Gli AppleScript passati a osascript, in ordine."""
        return [c[2] for c in self.calls if c[0].endswith("osascript")]


@pytest.fixture
def fake_ex() -> FakeExecutor:
    return FakeExecutor()
