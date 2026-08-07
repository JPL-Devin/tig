"""Console-script entry point.

Dispatches to the warm path first and only imports the click CLI when that
does not apply, so the common case never pays for click or the Docker SDK.
"""
import sys


def main() -> None:
    from .fast import run
    from .spec import TigError

    try:
        exit_code = run(sys.argv[1:])
    except KeyboardInterrupt:
        sys.exit(130)
    except (TigError, OSError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if exit_code is None:
        from .cli import main as cli_main

        cli_main()
        return
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
