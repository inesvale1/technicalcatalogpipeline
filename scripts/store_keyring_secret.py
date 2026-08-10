from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

if __package__ in {None, ""}:
    package_parent = Path(__file__).resolve().parents[2]
    if str(package_parent) not in sys.path:
        sys.path.insert(0, str(package_parent))


def main() -> None:
    parser = argparse.ArgumentParser(description="Store a database password securely in the OS keyring.")
    parser.add_argument("--service", required=True, help="Keyring service name, for example: technicalcatalog-oracle")
    parser.add_argument("--username", required=True, help="Database username used as the keyring username")
    parser.add_argument("--show-check", action="store_true", help="Confirm that the password can be read back")
    args = parser.parse_args()

    try:
        import keyring
    except ImportError as exc:
        raise RuntimeError("This script requires keyring. Install it with: pip install keyring") from exc

    password = getpass.getpass("Database password: ")
    confirmation = getpass.getpass("Repeat database password: ")
    if password != confirmation:
        raise ValueError("Passwords do not match. Nothing was stored.")

    keyring.set_password(args.service, args.username, password)
    print(f"Password stored in keyring for service '{args.service}' and username '{args.username}'.")

    if args.show_check:
        stored = keyring.get_password(args.service, args.username)
        print("Read-back check:", "OK" if stored else "NOT FOUND")


if __name__ == "__main__":
    main()
