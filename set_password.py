"""Set or change the dashboard login password.

Reads your password without showing it, stores only a salted hash in .env
(never the plaintext), and generates a session secret key if one isn't there.

Run:  python set_password.py
"""
import getpass
import os
import secrets

from werkzeug.security import generate_password_hash

ENV = ".env"


def read_env():
    data = {}
    if os.path.exists(ENV):
        for line in open(ENV):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                data[k.strip()] = v.strip().strip("'").strip('"')  # drop surrounding quotes
    return data


def write_env(data):
    with open(ENV, "w") as f:
        for k, v in data.items():
            f.write(f"{k}='{v}'\n")  # single-quote so dotenv reads values literally
    os.chmod(ENV, 0o600)  # owner-only read/write


def main():
    pw = getpass.getpass("New dashboard password: ")
    pw2 = getpass.getpass("Confirm password: ")
    if not pw:
        print("Empty password — nothing changed.")
        return
    if pw != pw2:
        print("Passwords didn't match — nothing changed.")
        return

    env = read_env()
    env["DASHBOARD_PASSWORD_HASH"] = generate_password_hash(pw)
    if "FLASK_SECRET_KEY" not in env:
        env["FLASK_SECRET_KEY"] = secrets.token_hex(32)
    write_env(env)
    print("Password set, stored as a hash in .env (permissions locked to you).")
    print("Restart the dashboard for it to take effect.")


if __name__ == "__main__":
    main()
