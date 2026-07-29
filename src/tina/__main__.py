"""`python -m tina` — how the local executor spawns workers.

`main` delegates to the typer app, which raises SystemExit itself.
"""

from __future__ import annotations

from tina.cli import main

if __name__ == "__main__":
    main()
