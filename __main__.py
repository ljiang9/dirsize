"""Enables `python -m dirsize`."""
if __package__:
    from .dirsize import main
else:
    from dirsize import main

if __name__ == "__main__":
    raise SystemExit(main())
