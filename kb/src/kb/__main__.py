"""`python -m kb` -- the operator CLI. See adapters/inbound/cli.py."""

import sys

from kb.adapters.inbound.cli import main

if __name__ == "__main__":
    sys.exit(main())
