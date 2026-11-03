"""Entry point, so the project runs without installing it:

    python main.py                       # chat with Léo in the terminal
    python main.py web                   # chat in the browser (http://127.0.0.1:5000)
    python main.py --verbose             # terminal chat that also prints every tool call
    python main.py web --port 8000       # any option of the chosen mode goes after it

Run ``python main.py --help`` or ``python main.py web --help`` for the options.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))


def run(argv: list[str]) -> int:
    if argv[:1] == ["web"]:
        from emporio.web import main
        return main(argv[1:])
    from emporio.cli import main
    return main(argv)


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))
