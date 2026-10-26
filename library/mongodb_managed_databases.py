#!/usr/bin/python
# SPDX-FileCopyrightText: 2026 Slavi Pantaleev
#
# SPDX-License-Identifier: AGPL-3.0-or-later

DOCUMENTATION = r"""
---
module: mongodb_managed_databases
short_description: Prepares managed MongoDB databases (users, passwords, roles)
description:
  - Inspects the server first and only runs the commands needed to bring the user of each managed database
    to the desired state, so that it can report whether anything changed.
  - Talks to MongoDB by running C(mongosh) in its container (via C(docker exec)) and feeding it a script on stdin,
    so that passwords never end up in files or on command lines.
    The script authenticates as the superuser via the container's own C(MONGO_INITDB_ROOT_USERNAME) and C(MONGO_INITDB_ROOT_PASSWORD) environment variables.
  - Each user lives in its managed database (which MongoDB creates on first use), and gets the given roles on it.
    Roles are only ever granted, never revoked.
  - Whether a user's password is the desired one is checked by recomputing its stored SCRAM credentials.
    MongoDB redacts passwords from its log, so new ones are sent as they are and hashed by the server.
options:
  docker_command:
    description: The command (as a list of arguments) which invokes the Docker CLI.
    type: list
    elements: str
    required: true
  container:
    description: The name of the (running) MongoDB container.
    type: str
    required: true
  databases:
    description: The managed databases.
    type: list
    elements: dict
    required: true
    options:
      name:
        type: str
        required: true
      username:
        type: str
        required: true
      password:
        type: str
        required: true
        no_log: true
      roles:
        description: The roles (on the managed database) to grant to the user.
        type: list
        elements: str
        required: true
  connection_retries:
    description: How many times to retry when mongosh cannot connect to MongoDB.
    type: int
    default: 0
  connection_retry_delay_seconds:
    description: How long to wait between connection retries.
    type: int
    default: 3
"""

RETURN = r"""
changes:
  description: Human-readable descriptions of the changes made (or which would be made, in check mode).
  type: list
  elements: str
  returned: always
"""

import base64
import hashlib
import hmac
import json
import re
import stringprep
import time
import unicodedata

from ansible.module_utils.basic import AnsibleModule

# mongosh keeps its logs and history in the home directory, which is the data directory for the container's user.
# /tmp is a tmpfs in the container, so nothing is left behind.
MONGOSH = ["--env", "HOME=/tmp", "%(container)s", "mongosh", "--quiet", "--file", "/dev/stdin"]

# Errors which mean that the server could not be reached (yet), as opposed to it rejecting something.
CONNECTION_ERROR_RE = re.compile(r"MongoNetworkError|MongoServerSelectionError|ECONNREFUSED|is not running")

AUTHENTICATE = (
    "db.getSiblingDB('admin').auth(process.env.MONGO_INITDB_ROOT_USERNAME, process.env.MONGO_INITDB_ROOT_PASSWORD);\n"
)


def js(value):
    """Returns a JavaScript literal for the value."""
    return json.dumps(value)


def saslprep(password):
    """Prepares a password the way MongoDB does (RFC 4013) before hashing it for SCRAM-SHA-256.

    Returns `None` for input which MongoDB would reject.
    """
    if all(ord(c) < 128 for c in password):
        return password.encode("utf-8")

    mapped = "".join(
        " " if stringprep.in_table_c12(c) else c
        for c in password
        if not stringprep.in_table_b1(c)
    )
    normalized = unicodedata.ucd_3_2_0.normalize("NFKC", mapped)

    prohibited = (
        stringprep.in_table_c12,
        stringprep.in_table_c21_c22,
        stringprep.in_table_c3,
        stringprep.in_table_c4,
        stringprep.in_table_c5,
        stringprep.in_table_c6,
        stringprep.in_table_c7,
        stringprep.in_table_c8,
        stringprep.in_table_c9,
        stringprep.in_table_a1,
    )
    if any(check(c) for c in normalized for check in prohibited):
        return None

    if any(stringprep.in_table_d1(c) for c in normalized):
        if any(stringprep.in_table_d2(c) for c in normalized):
            return None
        if not (stringprep.in_table_d1(normalized[0]) and stringprep.in_table_d1(normalized[-1])):
            return None

    return normalized.encode("utf-8")


def scram_matches(digest, prepared_password, credential):
    salted = hashlib.pbkdf2_hmac(digest, prepared_password, base64.b64decode(credential["salt"]), credential["iterationCount"])
    client_key = hmac.new(salted, b"Client Key", digest).digest()
    stored_key = hashlib.new(digest, client_key).digest()
    server_key = hmac.new(salted, b"Server Key", digest).digest()
    return (
        hmac.compare_digest(stored_key, base64.b64decode(credential["storedKey"]))
        and hmac.compare_digest(server_key, base64.b64decode(credential["serverKey"]))
    )


def password_matches(username, password, credentials):
    """Tells whether the user's stored credentials (for every mechanism it has) are for this password."""
    if not credentials:
        return False
    for mechanism, credential in credentials.items():
        if mechanism == "SCRAM-SHA-1":
            prepared = hashlib.md5(("%s:mongo:%s" % (username, password)).encode("utf-8")).hexdigest().encode("ascii")
            if not scram_matches("sha1", prepared, credential):
                return False
        elif mechanism == "SCRAM-SHA-256":
            prepared = saslprep(password)
            if prepared is None or not scram_matches("sha256", prepared, credential):
                return False
        else:
            return False
    return True


class Mongosh(object):
    def __init__(self, module):
        self.module = module
        self.args = module.params["docker_command"] + ["exec", "-i"] + [
            arg % dict(container=module.params["container"]) for arg in MONGOSH
        ]

    def run(self, script):
        """Runs the script (as the superuser) and returns its output, retrying while MongoDB cannot be connected to."""
        attempts = 1 + max(self.module.params["connection_retries"], 0)
        for attempt in range(1, attempts + 1):
            rc, stdout, stderr = self.module.run_command(self.args, data=AUTHENTICATE + script, binary_data=True)
            if rc == 0 or not CONNECTION_ERROR_RE.search(stdout + stderr) or attempt == attempts:
                break
            time.sleep(self.module.params["connection_retry_delay_seconds"])
        if rc != 0:
            self.module.fail_json(
                msg="Preparing the managed databases failed: mongosh exited with code %d.\nOutput:\n%s%s" % (rc, stdout, stderr),
                rc=rc,
                stdout=stdout,
                stderr=stderr,
            )
        return stdout

    def query(self, script):
        """Runs a script which prints a single JSON document at the end, and returns that document."""
        lines = [line for line in self.run(script).splitlines() if line.strip()]
        return json.loads(lines[-1])


def inspect_users(mongosh, databases):
    """Returns the existing managed users (with their credentials and roles), keyed by (database, username)."""
    users = mongosh.query(
        "print(JSON.stringify(db.getSiblingDB('admin').runCommand({usersInfo: %s, showCredentials: true}).users));\n"
        % js([dict(db=db["name"], user=db["username"]) for db in databases])
    )
    return dict(((user["db"], user["user"]), user) for user in users)


def main():
    module = AnsibleModule(
        argument_spec=dict(
            docker_command=dict(type="list", elements="str", required=True),
            container=dict(type="str", required=True),
            databases=dict(
                type="list",
                elements="dict",
                required=True,
                options=dict(
                    name=dict(type="str", required=True),
                    username=dict(type="str", required=True),
                    password=dict(type="str", required=True, no_log=True),
                    roles=dict(type="list", elements="str", required=True),
                ),
            ),
            connection_retries=dict(type="int", default=0),
            connection_retry_delay_seconds=dict(type="int", default=3),
        ),
        supports_check_mode=True,
    )

    databases = module.params["databases"]
    if not databases:
        module.exit_json(changed=False, changes=[])

    mongosh = Mongosh(module)
    users = inspect_users(mongosh, databases)

    changes = []
    commands = []
    for db in databases:
        name, username, password = db["name"], db["username"], db["password"]
        user = users.get((name, username))
        database = "db.getSiblingDB(%s)" % js(name)

        if user is None:
            roles = [dict(role=role, db=name) for role in db["roles"]]
            changes.append("create user `%s` in database `%s`" % (username, name))
            commands.append("%s.createUser(%s);" % (database, js(dict(user=username, pwd=password, roles=roles))))
            users[(name, username)] = dict(credentials=None, roles=roles, password=password)
            continue

        if user.get("password", None) != password and not password_matches(username, password, user["credentials"]):
            changes.append("set the password of user `%s` in database `%s`" % (username, name))
            commands.append("%s.updateUser(%s, %s);" % (database, js(username), js(dict(pwd=password))))
        user["password"] = password

        missing = [role for role in db["roles"] if dict(role=role, db=name) not in user["roles"]]
        if missing:
            roles = [dict(role=role, db=name) for role in missing]
            changes.append("grant roles %s on database `%s` to user `%s`" % (", ".join(missing), name, username))
            commands.append("%s.grantRolesToUser(%s, %s);" % (database, js(username), js(roles)))
            user["roles"] = user["roles"] + roles

    changed = len(changes) > 0
    result = dict(changed=changed, changes=changes)
    if module._diff:
        result["diff"] = dict(before="", after="".join("%s\n" % c for c in changes))

    if module.check_mode or not commands:
        module.exit_json(**result)

    mongosh.run("\n".join(commands) + "\n")
    module.exit_json(**result)


if __name__ == "__main__":
    main()
