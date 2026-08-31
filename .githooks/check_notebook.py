"""Refuse a notebook whose code or outputs name a date or a weekday.

Only `source` and output text are examined. Scanning the raw file would also
scan the base64 image payloads, and that alphabet can spell a weekday by
chance; a hook that blocks an innocent notebook teaches its author to pass
--no-verify, which is worse than having no hook.
"""
import json
import re
import sys

DATE = re.compile(
    r"20\d{2}-\d{2}-\d{2}"
    r"|\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b"
    r"|\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*\d{1,2}\b"
    r"|\b\d{1,2}\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b"
    r"|\b(mon|tues|wednes|thurs|fri|satur|sun)day\b"
    r"|\b20\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\b",
    re.I)

# Three-letter weekdays (Sat, Mon) are deliberately absent: they occur in
# ordinary English, and a hook that blocks an innocent notebook teaches its
# author to bypass it. They are rejected at draw time instead, by
# src/figures.py, where labels are short and deliberate.


def texts(nb):
    for cell in nb.get("cells", []):
        yield "".join(cell.get("source", []))
        for out in cell.get("outputs", []):
            yield "".join(out.get("text", []))
            # A traceback is where a date lands by accident rather than by
            # intent: a pandas KeyError on a Timestamp is exactly the sort of
            # output that gets committed by mistake.
            yield "".join(out.get("traceback", []))
            data = out.get("data", {})
            # image/svg+xml matters as much as the text types: it is a real
            # matplotlib backend and it renders text visibly on the figure.
            for key in ("text/plain", "text/html", "image/svg+xml",
                        "text/markdown", "text/latex"):
                yield "".join(data.get(key, []))


def main(path: str) -> int:
    nb = json.loads(open(path).read())
    hits = {m.group() for chunk in texts(nb) for m in DATE.finditer(chunk)}
    # "weekdays" is a JSON key in the validation spec, not a rendered label.
    hits = {h for h in hits if h.lower() not in {"weekdays"}}
    if hits:
        print(f"BLOCKED  {path}  -- names a date or weekday: {sorted(hits)[:4]}")
        print("         published figures in this repository carry neither; see the README")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
