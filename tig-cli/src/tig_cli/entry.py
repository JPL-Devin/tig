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
    _keep_a_closed_pipe_quiet()
    sys.exit(exit_code)


def _keep_a_closed_pipe_quiet() -> None:
    """Stop `tig list big.img | head` from ending as a failure of tig's own.

    Python flushes stdout as it shuts down and exits 120, complaining, if
    that fails - hiding the exit status the command actually had.
    """
    import os

    try:
        sys.stdout.flush()
    except OSError:
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except OSError:
            pass


if __name__ == "__main__":
    main()
