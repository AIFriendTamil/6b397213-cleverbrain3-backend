"""Script to generate OpenAPI specification file in contracts/openapi.json from FastAPI app."""

import json
from pathlib import Path
import sys

# Ensure backend directory is on sys.path
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from src.api.main import app  # noqa: E402


def generate_openapi_schema() -> dict:
    """Generate OpenAPI schema dictionary from FastAPI app instance."""
    return app.openapi()


def write_openapi_json(output_path: Path) -> None:
    """Generate and save OpenAPI JSON specification to output path."""
    schema = generate_openapi_schema()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(schema, f, indent=2)
        f.write("\n")


if __name__ == "__main__":
    project_root = backend_dir.parent
    contracts_path = project_root / "contracts" / "openapi.json"
    write_openapi_json(contracts_path)
    print(f"Successfully generated OpenAPI specification at {contracts_path}")
