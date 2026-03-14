"""PhotoGal CLI."""

import typer
import uvicorn

app = typer.Typer(name="photogal", help="PhotoGal photo library organizer")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Host to bind"),
    port: int = typer.Option(8765, help="Port to listen on"),
    db: str = typer.Option(None, help="Database path (default: ~/Library/Application Support/com.photogal.desktop/photogal.db)"),
    reload: bool = typer.Option(False, help="Auto-reload on code changes"),
):
    """Start the PhotoGal API server."""
    from photogal.server import create_app
    application = create_app(db_path=db)
    uvicorn.run(application, host=host, port=port, reload=reload)


@app.command()
def scan(
    path: str = typer.Argument(..., help="Folder to scan"),
    db: str = typer.Option(None, help="Database path"),
):
    """Scan a folder and import photos (Level 0)."""
    from pathlib import Path
    from photogal.config import get_db_path, load_config
    from photogal.db import Database
    from photogal.pipeline.scanner import Scanner

    db_path = db or get_db_path()
    config = load_config()

    with Database(db_path) as database:
        source_id = database.add_source(path)
        scanner = Scanner(config)
        result = scanner.run(database, source_id, Path(path))
        typer.echo(f"Scanned: {result['scanned']}, New: {result['new']}, Skipped: {result['skipped']}")


@app.command()
def analyze(db: str = typer.Option(None, help="Database path")):
    """Run Level 1: quality analysis + geocoding."""
    from photogal.config import get_db_path, load_config
    from photogal.db import Database
    from photogal.pipeline.analyzer import Analyzer

    db_path = db or get_db_path()
    config = load_config()

    with Database(db_path) as database:
        analyzer = Analyzer(config)
        result = analyzer.run(database)
        typer.echo(f"Processed: {result['processed']}, Errors: {result['errors']}")


if __name__ == "__main__":
    app()
