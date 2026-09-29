"""Insert the Stage B include into the unique scanner TLS server block."""

from __future__ import annotations

import pathlib
import re
import sys


INCLUDE = "    include /etc/nginx/snippets/backbond-stage-b-inbound.conf;"


def server_blocks(text: str) -> list[tuple[int, int, str]]:
    blocks: list[tuple[int, int, str]] = []
    for match in re.finditer(r"(?m)^\s*server\s*\{", text):
        depth = 0
        for index in range(match.end() - 1, len(text)):
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
                if depth == 0:
                    blocks.append((match.start(), index, text[match.start() : index + 1]))
                    break
        else:
            raise ValueError("nginx_server_block_unclosed")
    return blocks


def patched(text: str) -> str:
    if INCLUDE in text:
        return text
    candidates = []
    for start, end, block in server_blocks(text):
        has_name = re.search(r"(?m)^\s*server_name\s+[^;]*\bscanner\.backbond\.ai\b[^;]*;", block)
        has_tls = re.search(r"(?m)^\s*listen\s+[^;]*\b443\b[^;]*\bssl\b[^;]*;", block)
        if has_name and has_tls:
            candidates.append((start, end))
    if len(candidates) != 1:
        raise ValueError(f"expected_one_scanner_tls_block_got_{len(candidates)}")
    _, end = candidates[0]
    return text[:end] + INCLUDE + "\n" + text[end:]


def main() -> None:
    path = pathlib.Path(sys.argv[1])
    text = path.read_text()
    path.write_text(patched(text))


if __name__ == "__main__":
    main()
