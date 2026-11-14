#!/usr/bin/env python3
"""End-to-end smoke test of the docker compose stack.

Exercises the API *through the nginx proxy* on port 8080, so the same-origin
path the browser uses is the path under test. Verifies the multi-user workflow:
identity resolution, cross-user isolation, READ/WRITE sharing, revocation,
optimistic-concurrency conflicts and input validation.

Run against a running stack:  python3 docker/smoke-test.py
Override the base URL with NOTES_BASE_URL (default http://localhost:8080/api/v1).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("NOTES_BASE_URL", "http://localhost:8080/api/v1")

ALICE = "11111111-1111-1111-1111-111111111111"
BOB = "22222222-2222-2222-2222-222222222222"
CAROL = "33333333-3333-3333-3333-333333333333"

_passed = 0
_failed = 0


def request(method: str, path: str, user: str | None = None, body: dict | None = None):
    """Return (status, parsed_json_or_None)."""
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if user is not None:
        req.add_header("X-Demo-User-ID", user)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            parsed = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            parsed = None
        return exc.code, parsed


def check(desc: str, expected, actual) -> None:
    global _passed, _failed
    if expected == actual:
        print(f"  ok   {desc} ({actual})")
        _passed += 1
    else:
        print(f"  FAIL {desc} (expected {expected}, got {actual})")
        _failed += 1


def status(method: str, path: str, user: str | None = None, body: dict | None = None) -> int:
    return request(method, path, user, body)[0]


def main() -> int:
    print("== identity ==")
    check("GET /me as Alice", 200, status("GET", "/me", ALICE))
    check("GET /me without identity", 401, status("GET", "/me"))
    check(
        "GET /me with unknown identity",
        401,
        status("GET", "/me", "99999999-9999-9999-9999-999999999999"),
    )

    print("== create + isolation ==")
    created_status, created = request(
        "POST", "/notes", ALICE, {"title": "Docker smoke test", "content": "<p>hello</p>"}
    )
    check("Create note returns 201", 201, created_status)
    assert created is not None
    nid = created["id"]
    print(f"  note id: {nid}")
    check("Alice reads her note", 200, status("GET", f"/notes/{nid}", ALICE))
    check("Bob cannot read Alice's private note", 404, status("GET", f"/notes/{nid}", BOB))
    check(
        "Bob cannot update Alice's note",
        404,
        status("PUT", f"/notes/{nid}", BOB, {"title": "x", "content": "y", "version": 1}),
    )
    check("Bob cannot delete Alice's note", 404, status("DELETE", f"/notes/{nid}", BOB))
    check("Bob cannot list permissions", 404, status("GET", f"/notes/{nid}/permissions", BOB))

    print("== share READ ==")
    check(
        "Alice grants READ to Bob",
        201,
        status("POST", f"/notes/{nid}/permissions", ALICE, {"user_id": BOB, "permission": "read"}),
    )
    check("Bob can now read", 200, status("GET", f"/notes/{nid}", BOB))
    check(
        "Bob still cannot update (READ)",
        403,
        status("PUT", f"/notes/{nid}", BOB, {"title": "x", "content": "y", "version": 1}),
    )
    check("Bob still cannot delete (READ)", 403, status("DELETE", f"/notes/{nid}", BOB))
    check(
        "Bob cannot manage permissions",
        403,
        status("POST", f"/notes/{nid}/permissions", BOB, {"user_id": CAROL, "permission": "read"}),
    )

    print("== upgrade to WRITE ==")
    check(
        "Alice upgrades Bob to WRITE",
        201,
        status("POST", f"/notes/{nid}/permissions", ALICE, {"user_id": BOB, "permission": "write"}),
    )
    check(
        "Bob can now update",
        200,
        status(
            "PUT",
            f"/notes/{nid}",
            BOB,
            {"title": "updated by bob", "content": "<p>hi</p>", "version": 1},
        ),
    )
    check("Bob still cannot delete (WRITE)", 403, status("DELETE", f"/notes/{nid}", BOB))
    check(
        "Bob still cannot manage permissions (WRITE)",
        403,
        status("POST", f"/notes/{nid}/permissions", BOB, {"user_id": CAROL, "permission": "read"}),
    )

    print("== concurrency ==")
    check(
        "Stale update is rejected with 409",
        409,
        status("PUT", f"/notes/{nid}", ALICE, {"title": "stale", "content": "z", "version": 1}),
    )

    print("== revocation ==")
    check("Alice revokes Bob", 204, status("DELETE", f"/notes/{nid}/permissions/{BOB}", ALICE))
    check("Bob loses read access", 404, status("GET", f"/notes/{nid}", BOB))
    check(
        "Bob loses write access",
        404,
        status("PUT", f"/notes/{nid}", BOB, {"title": "x", "content": "y", "version": 2}),
    )

    print("== validation ==")
    check("Missing title is rejected", 422, status("POST", "/notes", ALICE, {"content": "x"}))
    check("Malformed UUID is rejected", 422, status("GET", "/notes/not-a-uuid", ALICE))
    check(
        "Unknown note id is 404",
        404,
        status("GET", "/notes/00000000-0000-0000-0000-000000000000", ALICE),
    )

    print("== users listing ==")
    check("Alice lists users for sharing", 200, status("GET", "/users", ALICE))

    print()
    print(f"RESULT: {_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
