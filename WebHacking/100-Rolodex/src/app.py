#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# W100 challenge for Pixels Camp CTF 2016
#
# Copyright (c) 2016, Bright Pixel
#


from __future__ import print_function
from __future__ import division
from __future__ import unicode_literals
from __future__ import absolute_import

import sys
import os
import logging
import hashlib
import json
import csv
import time

from getopt import getopt, GetoptError
from functools import wraps
from random import shuffle
from copy import deepcopy

from bottle import route, request, response, \
                   run, parse_auth, HTTPError


log = logging.getLogger("ctf-challenge")


# Participant UID range...
MAX_PARTICIPANT_UID = 500

ACCESS_TOKEN_LIFETIME = 900  # ...seconds.


# File name where participant data will be saved,
# to keep state in case any of the teams manages
# to crash the service while trying to solve it...
save_filename = None

# People loaded from ".csv" files...
employee_data = {}     # ...indexed by employee UID.
participant_data = {}  # ...indexed by username.

# Tokens generated by valid "/token" requests...
access_tokens = {}


def load_employees(filename):
    employees = {}

    with open(filename, "rb") as f:
        for row in csv.reader(f, delimiter=b",", quotechar=b"\""):
            uid = int(row[0])

            if uid <= MAX_PARTICIPANT_UID:
                raise ValueError("employee ID <= %d", MAX_PARTICIPANT_UID)

            employee = {
                "uid": uid,
                "name": row[1],
                "username": row[2],
                "phone": row[3],
                "location": row[4],
                "department": row[5],
                "position": row[6],
                "notes": row[7]
            }

            employees[uid] = employee

    return employees


def load_participants(filename):
    participants = {}
    uid = MAX_PARTICIPANT_UID

    with open(filename, "rb") as f:
        for row in csv.reader(f, delimiter=b",", quotechar=b"\""):
            participant = {
                "uid": uid,
                "name": "John J. Random",
                "phone": "55590210",
                "location": "Building 3, Floor 10, Office 1A",
                "department": "Mergers and Acquisitions",
                "position": "Intern",
                "notes": "Still hasn't signed the corporate security policy.",
                "username": row[0].strip().lower(),
                "password": row[1],  # ...password is hashed.
                "token": None  # ...temporary, (re)generated on demand.
            }

            participants[participant["username"]] = participant
            uid += 1

    return participants


def save_participants():
    if not save_filename:
        return

    with open(save_filename, "w") as f:
        data = json.dumps({"participants": participant_data, "tokens": access_tokens})
        f.write(data)
        log.debug("Saved participant data.")


def restore_participants(filename):
    with open(filename, "r") as f:
        data = json.loads(f.read())
        return data["participants"], data["tokens"]


def has_admin_privileges(user):
    """Return whether the specified user has admin privileges."""

    # Finding this is the point of the challenge...
    return user["position"].lower() == "admin"


def ensure_valid_token(func):
    """
    Decorator for routes that require a valid access token.

    The token is passed as the first parameter to the decorated
    function along with an additional "privileged" flag passed as
    a keyword argument, indicating the level of access required.
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        # Token in headers overrides token in query string for safety...
        token = request.headers.get("X-API-Token") or request.GET.get("token")

        if token not in access_tokens or access_tokens[token]["expires"] < time.time():
            response.status = 403
            return {"status": 403, "error": "bad access token"}

        user = participant_data[access_tokens[token]["username"]]
        privileged = has_admin_privileges(user)
        kwargs["privileged"] = privileged

        if privileged:
            log.info("Privileged call for user \"%s\".", user["username"])

        return func(token, *args, **kwargs)

    return wrapper


@route("/token", method="GET")
def get_token():
    credentials = request.headers.get("Authorization", "")
    username, password = parse_auth(credentials) or ("", "")

    username = username.strip().lower()
    password = hashlib.sha1(password).hexdigest()

    participant = participant_data.get(username)

    if not participant or participant["password"] != password:
        response.status = 401
        return {"status": 401, "error": "bad user credentials"}

    token = hashlib.sha256(os.urandom(1024)).hexdigest()

    if participant["token"]:  # ...invalidate previous token.
        del access_tokens[participant["token"]]

    participant["token"] = token

    access_tokens[token] = {
        "username": username,
        "uid": participant["uid"],
        "expires": int(time.time() + ACCESS_TOKEN_LIFETIME)
    }

    save_participants()

    response.status = 200
    return {
        "status": 200,
        "token": token,
        "uid": participant["uid"],
        "expires": access_tokens[token]["expires"]
    }


@route("/users", method="GET")
@ensure_valid_token
def get_users(token, privileged=False):
    results = []
    attributes = ["uid", "username", "name", "location", "department", "position"]

    if privileged:
        attributes.append("notes")

    for employee in employee_data.itervalues():
        entry = {k: employee[k] for k in attributes}
        results.append(entry)

    # Add our participant to the list...
    participant = participant_data[access_tokens[token]["username"]]
    results.append({k: participant[k] for k in attributes + ["notes"]})

    shuffle(results)

    response.status = 200
    return {"status": 200, "users": results}


@route("/users/<uid:int>", method="GET")
@ensure_valid_token
def get_user(token, uid, privileged=False):
    participant = participant_data[access_tokens[token]["username"]]
    entry = {}

    attributes = ["uid", "username", "name", "location", "department", "position"]

    if participant["uid"] == uid:  # ...don't return other participants.
        entry = {k: participant[k] for k in attributes + ["notes"]}
    elif uid in employee_data:
        entry = {k: employee_data[uid][k] for k in attributes}
        if privileged:
            entry["notes"] = employee_data[uid]["notes"]

    if not entry:
        response.status = 404
        return {"status": 404, "error": "user not found"}

    response.status = 200
    return {"status": 200, "user": entry}


@route("/users/<uid:int>", method="PUT")
@ensure_valid_token
def set_user(token, uid, privileged=False):
    participant = participant_data[access_tokens[token]["username"]]

    #
    # TODO: Allow modifying other users when privileged. Right now this
    #       isn't needed for this challenge, but may be useful if this
    #       service is reused for other challenges in the future.
    #
    if uid != participant["uid"]:
        response.status = 403
        return {"status": 403, "error": "not implemented" if privileged else "forbidden"}

    try:
        changes = request.json
    except (ValueError, TypeError):
        # Recent versions of bottle do this internally...
        raise HTTPError(400, "Invalid JSON")

    if changes is None:  # ...bad content-type or something.
        raise HTTPError(400, "Invalid JSON")

    # The attributes that can be edited...
    rw_attributes = set(["name", "location", "department", "position", "notes"])

    if set(changes.keys()).difference(rw_attributes):
        response.status = 400
        return {"status": 400, "error": "bad attribute(s)"}

    entry = deepcopy(participant)
    for attribute, value in changes.iteritems():
        if attribute is None and attribute in entry:
            del entry[attribute]  # ...remove "null" attributes.
            continue

        entry[attribute] = value

    if entry == participant:
        response.status = 304
        return {"status": 304, "error": "changes already match existing entry"}

    participant_data[access_tokens[token]["username"]] = entry
    save_participants()

    response.status = 200
    return {"status": 200}


def print_usage():
    """Output the proper usage syntax for this program."""

    print("USAGE: %s --employees <file.csv> --participants <file.csv> "
          "[--listen <ip:port>] [--debug]" % os.path.basename(sys.argv[0]))


def parse_args():
    """Parse and enforce command-line arguments."""

    try:
        options, args = getopt(sys.argv[1:], "l:e:p:s:dh", ["listen=", "employees=",
                                                            "participants=", "save=",
                                                            "debug", "help"])
    except GetoptError as e:
        print("error: %s." % e, file=sys.stderr)
        print_usage()
        sys.exit(1)

    listen = {"host": "127.0.0.1", "port": "8080"}
    employees = None
    participants = None
    savefile = None
    debug = False

    for option, value in options:
        if option in ("-h", "--help"):
            print_usage()
            sys.exit(0)
        elif option in ("-e", "--employees"):
            employees = value
        elif option in ("-p", "--participants"):
            participants = value
        elif option in ("-s", "--save"):
            savefile = value
        elif option in ("-l", "--listen"):
            fields = value.split(":")
            listen = {"host": fields[0].strip(),
                      "port": int(fields[1]) if len(fields) > 1 else "8080"}
        elif option in ("-d", "--debug"):
            debug = True

    if not employees or not participants:
        print("error: parameter(s) missing.", file=sys.stderr)
        print_usage()
        sys.exit(1)

    return (listen, employees, participants, savefile, debug)


if __name__ == "__main__":
    listen, employees, participants, savefile, debug = parse_args()

    format = logging.Formatter("%(asctime)s: %(levelname)s [%(process)s]: %(message)s")
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(format)
    logging.getLogger().addHandler(handler)

    log.setLevel(logging.DEBUG if debug else logging.INFO)

    if savefile and os.path.isfile(savefile):
        log.info("Restoring participant data from saved state...")
        participant_data, access_tokens = restore_participants(savefile)
    else:
        log.info("Loading participant data from pristine state...")
        participant_data = load_participants(participants)

    save_filename = savefile if savefile else None  # ...save on data modification.
    employee_data = load_employees(employees)

    run(host=listen["host"], port=listen["port"], debug=debug)


# vim: set expandtab ts=4 sw=4: