"""Execute the notebooks and strip everything date-shaped from the result.

Notebooks are committed with their outputs -- a notebook without them is just
noisier source code -- but the executor records a wall-clock timestamp against
every cell, and cell ids and kernel details vary run to run. The timestamps are
a log of when the author was working, so they are removed here rather than left
for the pre-commit hook to reject.

    python3 notebooks/build.py
"""
import sys
from pathlib import Path

import nbformat as nbf
from nbclient import NotebookClient

HERE = Path(__file__).resolve().parent
NOTEBOOKS = ["01_signal", "02_ventilation", "03_occupancy"]


def strip(nb) -> None:
    nb.metadata.pop("widgets", None)
    for cell in nb.cells:
        # Written by the executor: {'execution': {'iopub.execute_input': '...Z'}}
        cell.metadata.pop("execution", None)
        for output in cell.get("outputs", []):
            output.get("metadata", {}).pop("execution", None)


def main() -> int:
    for name in NOTEBOOKS:
        path = HERE / f"{name}.ipynb"
        nb = nbf.read(path, as_version=4)
        NotebookClient(nb, timeout=300, kernel_name="python3",
                       resources={"metadata": {"path": str(HERE)}}).execute()
        strip(nb)
        nbf.write(nb, path)
        figures = sum("image/png" in o.get("data", {})
                      for c in nb.cells for o in c.get("outputs", []))
        print(f"  {name}: executed, {figures} figures")
    return 0


if __name__ == "__main__":
    sys.exit(main())
