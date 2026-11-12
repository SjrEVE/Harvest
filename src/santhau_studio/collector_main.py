"""Standalone Linux entry point; imports no Windows service or desktop modules."""

from santhau_studio.linux.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
