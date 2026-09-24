"""Adapter: beaaa-202/fin_transcripts p14/p31 records → generic messages JSONL.

Each row has `prompt` (user) and `response` (assistant). We emit one
single-turn conversation per row. Rows missing either field are skipped
with a logged reason; an empty output raises ShapeError.
"""
import argparse, json, sys
from pathlib import Path


class ShapeError(ValueError):
    pass


def convert(path: Path) -> list[dict]:
    convs = []
    skipped = []
    for n, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as e:
            raise ShapeError(f"{path}:{n}: not JSON") from e
        if not isinstance(row, dict):
            raise ShapeError(f"{path}:{n}: expected object")
        prompt = row.get("prompt") or row.get("query") or row.get("user")
        response = row.get("response") or row.get("answer") or row.get("assistant")
        if not prompt or not response:
            skipped.append(f"{path}:{n}: missing prompt or response (keys={list(row)[:6]})")
            continue
        convs.append({"messages": [
            {"role": "user", "content": str(prompt)},
            {"role": "assistant", "content": str(response)},
        ]})
    for s in skipped:
        print(f"SKIP {s}", file=sys.stderr)
    if not convs:
        raise ShapeError(f"{path}: no usable conversations after skipping {len(skipped)} rows")
    return convs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    all_convs = []
    for p in args.inputs:
        all_convs.extend(convert(p))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w") as f:
        for c in all_convs:
            f.write(json.dumps(c) + "\n")
    print(f"wrote {len(all_convs)} conversations → {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
