"""Create users and set passwords from the server's command line.

    python manage_users.py create wamique "Wamique" owner
    python manage_users.py create clerk1 "Shop Clerk" clerk
    python manage_users.py set-password wamique

Roles: owner (everything), manager (close bills, advances), clerk (daily entry), viewer (read only).
Passwords are asked for interactively and never stored in the repo.
"""

import argparse
import getpass

import db
from security import hash_password


def ask_password() -> str:
    while True:
        p1 = getpass.getpass("New password: ")
        if len(p1) < 8:
            print("At least 8 characters.")
            continue
        if p1 == getpass.getpass("Again: "):
            return p1
        print("They do not match.")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("username")
    c.add_argument("display_name")
    c.add_argument("role", choices=["owner", "manager", "clerk", "viewer"])
    s = sub.add_parser("set-password")
    s.add_argument("username")
    args = ap.parse_args()

    db.migrate()
    with db.transaction() as conn:
        if args.cmd == "create":
            conn.execute(
                "INSERT INTO users (username, display_name, role, password_hash) VALUES (%s, %s, %s, %s)",
                (args.username, args.display_name, args.role, hash_password(ask_password())),
            )
            print("Created", args.username)
        else:
            n = conn.execute("UPDATE users SET password_hash = %s WHERE username = %s",
                             (hash_password(ask_password()), args.username)).rowcount
            conn.execute("DELETE FROM sessions WHERE user_id = (SELECT id FROM users WHERE username = %s)",
                         (args.username,))
            print("Password set" if n else "No such user")


if __name__ == "__main__":
    main()
