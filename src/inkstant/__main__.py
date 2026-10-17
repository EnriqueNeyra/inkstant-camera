"""python3 -m inkstant {camera|gallery|ctl}."""

import sys


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv.pop(0) if argv else ""
    if cmd == "camera":
        from inkstant.camera_app.main import main as camera_main

        return camera_main(argv)
    if cmd == "gallery":
        from inkstant.gallery.app import serve

        listen = argv[0] if argv else "0.0.0.0:80"
        serve(listen=listen)
        return 0
    if cmd == "ctl":
        from inkstant.ctl import main as ctl_main

        return ctl_main(argv)
    print("usage: python3 -m inkstant {camera|gallery [host:port]|ctl ...}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
