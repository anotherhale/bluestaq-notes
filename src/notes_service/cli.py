"""Command-line entry point.

Subcommands:

* ``serve``     — run the API with uvicorn (default).
* ``init-db``   — create the schema (dev convenience) and optionally seed demo
                  users. Production should use Alembic migrations instead.
* ``seed``      — seed demo users into an existing schema.
"""

from __future__ import annotations

import argparse
import sys

from notes_service.bootstrap.config import load_settings
from notes_service.infrastructure.db.base import Base
from notes_service.infrastructure.db.engine import create_db_engine, create_session_factory
from notes_service.infrastructure.db.models import UserRow  # noqa: F401  (register tables)
from notes_service.infrastructure.db.seed import seed_demo_users


def _cmd_init_db(args: argparse.Namespace) -> int:
    settings = load_settings()
    engine = create_db_engine(settings)
    Base.metadata.create_all(engine)
    print(f"schema created for {settings.database_provider.value}")
    if args.seed:
        factory = create_session_factory(engine)
        with factory() as session:
            users = seed_demo_users(session)
            session.commit()
        print(f"seeded {len(users)} demo users")
    return 0


def _cmd_seed(args: argparse.Namespace) -> int:
    settings = load_settings()
    engine = create_db_engine(settings)
    factory = create_session_factory(engine)
    with factory() as session:
        users = seed_demo_users(session)
        session.commit()
    print(f"seeded {len(users)} demo users")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run(
        "notes_service.bootstrap.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_config=None,  # our logging config is applied in create_app
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="notes-service", description="Bluestaq notes service")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="run the API server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")
    serve.set_defaults(func=_cmd_serve)

    init_db = sub.add_parser("init-db", help="create the schema (dev) and optionally seed")
    init_db.add_argument("--seed", action="store_true", help="also seed demo users")
    init_db.set_defaults(func=_cmd_init_db)

    seed = sub.add_parser("seed", help="seed demo users into an existing schema")
    seed.set_defaults(func=_cmd_seed)

    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        # Default to serving when invoked with no subcommand.
        args = parser.parse_args(["serve"])
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
