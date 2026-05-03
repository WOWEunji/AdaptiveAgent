"""TeeStream: 화면 + 파일에 동시 출력."""

from __future__ import annotations

import io
from typing import TextIO


class TeeStream(io.TextIOBase):
    """Write to multiple text streams simultaneously."""

    def __init__(self, *streams: TextIO) -> None:
        self._streams = streams

    def write(self, s: str) -> int:
        for st in self._streams:
            try:
                st.write(s)
            except (ValueError, OSError):
                pass
        return len(s)

    def flush(self) -> None:
        for st in self._streams:
            try:
                st.flush()
            except (ValueError, OSError):
                pass

    def isatty(self) -> bool:
        # 첫 번째 스트림(터미널)의 isatty 위임 → ANSI 색상 유지
        return hasattr(self._streams[0], "isatty") and self._streams[0].isatty()
