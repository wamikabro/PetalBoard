"""Create users and set passwords from the server's command line.

    python manage_users.py create wamique "Wamique" owner
    python manage_users.py create clerk1 "Shop Clerk" clerk
    python manage_users.py set-password wamique
    python manage_users.py create wamique "Wamique" owner --generate   # makes a strong password and prints it once

Roles: owner (everything), manager (close bills, advances), clerk (daily entry), viewer (read only).
Passwords are asked for interactively and never stored in the repo.
"""

import argparse
import getpass
import secrets

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


WORDS = ["gulab", "motia", "genda", "kaliyan", "phool", "sehra", "gajra", "haar", "chameli", "raat",
         "rani", "lily", "rose", "tulip", "jasmine", "daisy", "lotus", "orchid", "petal", "bahar"]


def generate_password() -> str:
    """Simple to type: a flower word and four digits, e.g. motia4821. Logins are rate limited."""
    return secrets.choice(WORDS) + "".join(secrets.choice("0123456789") for _ in range(4))


def main():
    ap = argparse.ArgumentParser(description="People log in with their password alone, so every password must be different.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("username")
    c.add_argument("display_name")
    c.add_argument("role", choices=["owner", "manager", "clerk", "viewer"])
    c.add_argument("--generate", action="store_true", help="make a simple password (flower word + 4 digits) and print it once")
    s = sub.add_parser("set-password")
    s.add_argument("username")
    s.add_argument("--generate", action="store_true", help="make a simple password (flower word + 4 digits) and print it once")
    args = ap.parse_args()

    db.migrate()
    from routes_auth import find_user_by_password
    with db.transaction() as conn:
        for _ in range(20):
            password = generate_password() if args.generate else ask_password()
            other = find_user_by_password(conn, password)
            if not other or (args.cmd == "set-password" and other["username"] == args.username):
                break
            if not args.generate:
                print("Someone else already uses that password. Choose a different one.")
        else:
            raise SystemExit("Could not find a free password")
        if args.cmd == "create":
            conn.execute(
                "INSERT INTO users (username, display_name, role, password_hash) VALUES (%s, %s, %s, %s)",
                (args.username, args.display_name, args.role, hash_password(password)),
            )
            print("Created", args.username)
            if args.generate:
                print("Password (shown once, write it down):", password)
        else:
            n = conn.execute("UPDATE users SET password_hash = %s WHERE username = %s",
                             (hash_password(password), args.username)).rowcount
            conn.execute("DELETE FROM sessions WHERE user_id = (SELECT id FROM users WHERE username = %s)",
                         (args.username,))
            print("Password set" if n else "No such user")
            if n and args.generate:
                print("Password (shown once, write it down):", password)


if __name__ == "__main__":
    main()
