import sys

from arc2dia2zen.cli import main

print("arc2dia is now arc2dia2zen; this alias will go away. Use: python3 -m arc2dia2zen", file=sys.stderr)
sys.exit(main())
