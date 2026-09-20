"""Send an isolated Close TEST using the production renderer."""

from scripts.test_email import main

if __name__ == "__main__":
    raise SystemExit(main("Close"))
