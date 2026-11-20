import signal
import time

import typer
from pathlib import Path
from typing import List

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn

from analyzer import (
    extract_label_data,
    vision_config,
    extract_from_multiple,
    _is_extraction_unreliable,
)
from validator import validate_compliance
from models import LabelData

app = typer.Typer(add_completion=False)
console = Console()


def _signal_handler(sig, frame):
    console.print("\n[yellow]Interrupted by user[/yellow]")
    raise typer.Exit(0)


signal.signal(signal.SIGINT, _signal_handler)


def extract_with_retry(
    image_path: Path, max_retries: int = 3, backoff_factor: float = 2.0
) -> LabelData:
    """Extract with exponential backoff for transient API failures."""
    last_error = None
    for attempt in range(max_retries):
        try:
            return extract_label_data(image_path)
        except (FileNotFoundError, ValueError):
            raise
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                wait = backoff_factor ** attempt
                console.log(
                    f"[yellow]Attempt {attempt + 1} failed ({e}). "
                    f"Retrying in {wait:.1f}s...[/yellow]"
                )
                time.sleep(wait)
    raise RuntimeError(f"Failed after {max_retries} attempts: {last_error}")


def _render_result(
    data: LabelData,
    result: dict,
    source_map: dict = None,
    conflicts: list = None,
) -> None:
    source_map = source_map or {}
    conflicts = conflicts or []

    def fmt(field: str, value):
        if not value:
            return "NOT FOUND"
        src = source_map.get(field)
        return f"{value}  [dim](from: {src})[/dim]" if src else str(value)

    console.print(Panel.fit(
        f"[bold]Extracted Data:[/bold]\n"
        f"Manufacturer: {fmt('manufacturer_name', data.manufacturer_name)}\n"
        f"Address: {fmt('manufacturer_address', data.manufacturer_address)}\n"
        f"Net Quantity: {fmt('net_quantity', data.net_quantity)}\n"
        f"MRP: {fmt('mrp', data.mrp)}\n"
        f"Date: {fmt('date_of_pack', data.date_of_pack)}\n"
        f"Consumer Care: {fmt('consumer_care', data.consumer_care)}",
        title="Extracted Label Data",
    ))

    verdict = result.get("verdict", "")

    if verdict == "UNVERIFIABLE":
        console.print("[bold yellow]UNVERIFIABLE[/bold yellow]")
    elif result["is_compliant"]:
        console.print("[bold green]COMPLIANT[/bold green]")
    else:
        console.print("[bold red]NON-COMPLIANT[/bold red]")
        for v in result["violations"]:
            console.print(f"  [red]• {v}[/red]")

    if conflicts:
        critical = [c for c in conflicts if c.get("severity") == "CRITICAL"]
        non_critical = [c for c in conflicts if c.get("severity") != "CRITICAL"]

        if critical:
            console.print(
                "\n[bold red]CRITICAL conflicts — manual review required:[/bold red]"
            )
            for c in critical:
                console.print(
                    f"  [red]• {c['field']}: "
                    f"'{c['first_value']}' (from {c['first_source']}) "
                    f"vs '{c['second_value']}' (from {c['second_source']})[/red]"
                )
            console.print(
                "  [red]Values discarded — cannot auto-confirm compliance.[/red]"
            )

        if non_critical:
            console.print(
                "\n[bold yellow]Minor conflicts (please verify):[/bold yellow]"
            )
            for c in non_critical:
                console.print(
                    f"  [yellow]• {c['field']}: "
                    f"'{c['first_value']}' vs '{c['second_value']}'[/yellow]"
                )

    if result["warnings"]:
        console.print("\n[bold yellow]Warnings:[/bold yellow]")
        for w in result["warnings"]:
            console.print(f"  [yellow]• {w}[/yellow]")

    # Honest limitation note for consumer care
    if data.consumer_care:
        console.print(
            "\n[dim]Note: consumer care numbers are read from the image and "
            "may be inaccurate on degraded photos. Verify against the physical "
            "label before acting.[/dim]"
        )


def _require_vision_config() -> None:
    try:
        vision_config()
    except ValueError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from None


@app.command()
def scan(
    image_paths: List[str] = typer.Argument(
        ..., help="One or more product label images (front, back, side, etc.)"
    ),
    product_name: str = typer.Option(
        None, "--product", "-p",
        help="Product name hint for standard pack size check",
    ),
):
    """Scan one or more product label images for Legal Metrology compliance."""
    paths = [Path(p) for p in image_paths]

    for p in paths:
        if not p.exists():
            console.print(f"[red]File not found: {p}[/red]")
            raise typer.Exit(1)

    _require_vision_config()

    console.print(f"[bold]Analyzing {len(paths)} image(s):[/bold]")
    for p in paths:
        console.print(f"  • {p.name}")

    # --- Extraction with spinner ---
    if len(paths) == 1:
        with console.status("[bold yellow]Extracting label data...[/bold yellow]", spinner="dots"):
            try:
                data = extract_with_retry(paths[0])
                source_map, conflicts = {}, []
            except Exception as e:
                console.print(f"[red]Extraction failed: {e}[/red]")
                raise typer.Exit(1)
    else:
        with console.status(
            f"[bold yellow]Extracting and merging label data from {len(paths)} images...[/bold yellow]",
            spinner="dots",
        ):
            try:
                data, source_map, conflicts = extract_from_multiple(paths)
            except Exception as e:
                console.print(f"[red]Extraction failed: {e}[/red]")
                raise typer.Exit(1)

    # --- Check if extraction was reliable ---
    unreliable = _is_extraction_unreliable(data)

    # --- Validation with spinner ---
    with console.status("[bold yellow]Validating compliance...[/bold yellow]", spinner="dots"):
        result = validate_compliance(data, product_name, unreliable=unreliable)

    _render_result(data, result, source_map, conflicts)
    console.print()


@app.command()
def batch(
    folder: str = typer.Argument(..., help="Folder containing product images"),
    product_name: str = typer.Option(
        None, "--product", "-p", help="Product name hint for all images"
    ),
):
    """Batch scan single-image-per-product labels in a folder."""
    folder_path = Path(folder)
    if not folder_path.is_dir():
        console.print(f"[red]Not a directory: {folder}[/red]")
        raise typer.Exit(1)

    extensions = {".jpg", ".jpeg", ".png", ".webp"}
    images = [f for f in folder_path.iterdir() if f.suffix.lower() in extensions]

    if not images:
        console.print("[yellow]No images found[/yellow]")
        raise typer.Exit(0)

    _require_vision_config()

    passed = failed = unverifiable = errored = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
    ) as progress:
        task = progress.add_task("[bold yellow]Scanning...[/bold yellow]", total=len(images))

        for img in images:
            progress.update(task, description=f"[bold yellow]{img.name}[/bold yellow]")
            try:
                data = extract_with_retry(img)
                unreliable = _is_extraction_unreliable(data)
                result = validate_compliance(data, product_name, unreliable=unreliable)
                verdict = result.get("verdict", "")
                if verdict == "UNVERIFIABLE":
                    unverifiable += 1
                elif result["is_compliant"]:
                    passed += 1
                else:
                    failed += 1
            except Exception as e:
                errored += 1
                console.print(f"[red]  {img.name}: {e}[/red]")
            finally:
                progress.update(task, advance=1)

    console.print(
        f"\n[bold]Summary:[/bold] "
        f"[green]{passed} passed[/green], "
        f"[red]{failed} failed[/red], "
        f"[yellow]{unverifiable} unverifiable[/yellow], "
        f"[yellow]{errored} errored[/yellow]"
    )


if __name__ == "__main__":
    app()