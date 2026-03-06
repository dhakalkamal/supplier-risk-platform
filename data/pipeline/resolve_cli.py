"""CLI tool for manual entity resolution testing and review.

Usage:
    # Resolve a single name
    python -m data.pipeline.resolve_cli resolve "TSMC" --country TW

    # Batch resolve from CSV (columns: name, country_hint)
    python -m data.pipeline.resolve_cli batch --input suppliers.csv --output results.csv

    # Show unresolved queue
    python -m data.pipeline.resolve_cli unresolved --limit 20
"""

import asyncio
import csv
import json
from pathlib import Path
from typing import Annotated

import typer

from data.pipeline.entity_resolution import EntityResolver, InMemorySupplierRegistry
from data.pipeline.models import ResolutionResult, SupplierRegistryEntry

app = typer.Typer(
    name="resolve",
    help="Supplier entity resolution CLI.",
    no_args_is_help=True,
)


# ── Registry bootstrap ────────────────────────────────────────────────────────

def _load_registry_from_env() -> InMemorySupplierRegistry:
    """Load supplier registry.

    In production this would connect to Postgres. For CLI use, loads from
    SUPPLIER_REGISTRY_JSON env var (path to a JSON file) if set, else
    returns an empty registry and warns the user.
    """
    import os

    registry_path = os.getenv("SUPPLIER_REGISTRY_JSON")
    if not registry_path:
        typer.echo(
            "Warning: SUPPLIER_REGISTRY_JSON not set. "
            "Running with empty registry — all names will be unresolved.",
            err=True,
        )
        return InMemorySupplierRegistry([])

    path = Path(registry_path)
    if not path.exists():
        typer.echo(f"Error: registry file not found: {path}", err=True)
        raise typer.Exit(code=1)

    raw = json.loads(path.read_text())
    entries = [SupplierRegistryEntry(**entry) for entry in raw]
    typer.echo(f"Loaded {len(entries)} registry entries from {path}", err=True)
    return InMemorySupplierRegistry(entries)


# ── resolve command ───────────────────────────────────────────────────────────

@app.command()
def resolve(
    name: Annotated[str, typer.Argument(help="Raw company name to resolve.")],
    country: Annotated[
        str | None,
        typer.Option("--country", "-c", help="ISO 3166-1 alpha-2 country hint (e.g. TW, US)."),
    ] = None,
    context: Annotated[
        str | None,
        typer.Option("--context", help="Optional sentence/snippet where the name appeared."),
    ] = None,
) -> None:
    """Resolve a single company name to a canonical supplier ID."""
    registry = _load_registry_from_env()
    resolver = EntityResolver(registry=registry)
    result = asyncio.run(resolver.resolve(name, country_hint=country, context=context))

    if result.resolved:
        typer.echo(
            f"Resolved:   {result.canonical_name} ({result.supplier_id})\n"
            f"Method:     {result.method} | "
            f"Confidence: {result.confidence:.2f} | "
            f"Matched:    \"{result.matched_string}\""
        )
    else:
        typer.echo(
            f"Unresolved: \"{name}\" could not be matched to any supplier.\n"
            f"Method:     {result.method}"
        )
        raise typer.Exit(code=1)


# ── batch command ─────────────────────────────────────────────────────────────

def _read_batch_input(input_path: Path) -> list[tuple[str, str | None]]:
    """Read (name, country_hint) pairs from a CSV file.

    Expected columns: name (required), country_hint (optional).
    """
    with input_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None or "name" not in reader.fieldnames:
            typer.echo("Error: CSV must have a 'name' column.", err=True)
            raise typer.Exit(code=1)
        has_country = "country_hint" in (reader.fieldnames or [])
        return [
            (
                row["name"].strip(),
                row.get("country_hint", "").strip() or None if has_country else None,
            )
            for row in reader
            if row["name"].strip()
        ]


def _write_batch_output(
    output_path: Path,
    names: list[tuple[str, str | None]],
    results: list[ResolutionResult],
) -> None:
    """Write resolution results to a CSV file."""
    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "raw_name", "country_hint", "resolved", "supplier_id",
            "canonical_name", "confidence", "method", "matched_string",
        ])
        for result in results:
            writer.writerow([
                result.raw_name,
                result.country_hint or "",
                result.resolved,
                result.supplier_id or "",
                result.canonical_name or "",
                f"{result.confidence:.4f}",
                result.method,
                result.matched_string or "",
            ])


@app.command()
def batch(
    input: Annotated[
        Path,
        typer.Option(
            "--input", "-i",
            help="Input CSV path. Required columns: name, country_hint (optional).",
        ),
    ],
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Output CSV path for results."),
    ],
    max_concurrent: Annotated[
        int,
        typer.Option("--max-concurrent", help="Max concurrent resolution tasks."),
    ] = 10,
) -> None:
    """Batch resolve company names from a CSV file."""
    if not input.exists():
        typer.echo(f"Error: input file not found: {input}", err=True)
        raise typer.Exit(code=1)

    names = _read_batch_input(input)
    typer.echo(f"Resolving {len(names)} names...", err=True)

    registry = _load_registry_from_env()
    resolver = EntityResolver(registry=registry)
    results = asyncio.run(resolver.resolve_batch(names, max_concurrent=max_concurrent))

    _write_batch_output(output, names, results)

    resolved_count = sum(1 for r in results if r.resolved)
    typer.echo(
        f"Done. {resolved_count}/{len(results)} resolved. "
        f"Results written to {output}"
    )


# ── unresolved command ────────────────────────────────────────────────────────

@app.command()
def unresolved(
    limit: Annotated[
        int,
        typer.Option("--limit", "-n", help="Maximum number of unresolved entries to show."),
    ] = 20,
    output_json: Annotated[
        bool,
        typer.Option("--json", help="Output as JSON instead of table."),
    ] = False,
) -> None:
    """Show unresolved entities from the review queue.

    Reads from the in-memory registry populated during this session.
    In production, query pipeline.unresolved_entities directly.
    """
    registry = _load_registry_from_env()

    # Run a no-op resolve to trigger any deferred loading, then read queue
    queue = registry.unresolved[:limit]

    if not queue:
        typer.echo("No unresolved entities in queue.")
        return

    if output_json:
        typer.echo(json.dumps([e.model_dump(mode="json") for e in queue], indent=2, default=str))
        return

    typer.echo(f"\n{'RAW NAME':<40} {'COUNTRY':>7}  {'SOURCE':<8}  ATTEMPTED AT")
    typer.echo("-" * 80)
    for entity in queue:
        attempted = entity.attempted_at.strftime("%Y-%m-%d %H:%M UTC")
        typer.echo(
            f"{entity.raw_name[:39]:<40} "
            f"{(entity.country_hint or '–'):>7}  "
            f"{entity.source:<8}  "
            f"{attempted}"
        )
    typer.echo(f"\nShowing {len(queue)} entr{'y' if len(queue) == 1 else 'ies'}.")


# ── entrypoint ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app()
