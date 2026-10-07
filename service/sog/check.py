"""Preflight: is everything the pipeline needs in place?

    uv run python -m sog.check
"""

import shutil
import sys
import urllib.request

import anthropic
from sqlalchemy import text

from sog.config import settings
from sog.db import engine

EXPECTED_MIGRATION = "0004"


def main() -> int:
    ok = True

    def report(passed: bool, label: str, detail: str = "") -> None:
        nonlocal ok
        ok &= passed
        print(f"  {'OK  ' if passed else 'FAIL'}  {label}{f': {detail}' if detail else ''}")

    print("Database")
    try:
        with engine.connect() as conn:
            version = conn.execute(text("SELECT version_num FROM public.alembic_version")).scalar()
            report(version == EXPECTED_MIGRATION, "migrations", f"at {version}, expected {EXPECTED_MIGRATION}"
                   + ("" if version == EXPECTED_MIGRATION else " (run: make migrate)"))
            n = conn.execute(text("SELECT count(*) FROM archive.proposal")).scalar()
            g = conn.execute(text("SELECT count(*) FROM eval.gold_case")).scalar()
            report(n > 0 and g > 0, "seed data", f"{n} proposals, {g} gold packs" + ("" if n and g else " (run: make seed)"))
    except Exception as e:  # noqa: BLE001 - preflight reports every failure
        report(False, "connection", f"{str(e).splitlines()[0]} (is Docker running? run: make db)")

    print("Model")
    if not settings.anthropic_api_key:
        report(False, "ANTHROPIC_API_KEY", "empty in .env (paste the key and save the file)")
    else:
        try:
            client = anthropic.Anthropic(api_key=settings.anthropic_api_key, base_url=settings.sog_anthropic_base_url)
            model = client.models.retrieve(settings.sog_model_extract)
            report(True, "API key", f"accepted, {model.id} available")
        except anthropic.AuthenticationError:
            report(False, "API key", "rejected by Anthropic (check it was copied completely)")
        except anthropic.APIError as e:
            report(False, "API key", f"{type(e).__name__}: {str(e)[:120]}")

    print("Tools")
    report(shutil.which(settings.sog_libreoffice) is not None or __import__("os").path.exists(settings.sog_libreoffice),
           "LibreOffice", settings.sog_libreoffice)

    print("Services (only needed while `make dev` is running)")
    for name, url in [("API", "http://localhost:8000/api/health"), ("Review UI", "http://localhost:5173"),
                      ("Prisma Studio", "http://localhost:5555")]:
        try:
            urllib.request.urlopen(url, timeout=2)
            print(f"  up    {name}: {url}")
        except OSError:
            print(f"  down  {name}: {url}")

    print("\nReady." if ok else "\nFix the FAIL lines above, then run make check again.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
