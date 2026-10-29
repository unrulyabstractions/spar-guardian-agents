"""`python -m tools`: calls cli.main."""
import sys

from tools.tool_dispatch import main

sys.exit(main())
