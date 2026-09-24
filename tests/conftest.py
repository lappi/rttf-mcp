import gzip
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    plain = FIXTURES / name
    if plain.exists():
        return plain.read_text(encoding="utf-8")
    packed = FIXTURES / f"{name}.gz"
    if packed.exists():
        return gzip.decompress(packed.read_bytes()).decode("utf-8")
    raise FileNotFoundError(
        f"нет фикстуры {name}; запустите uv run python scripts/fetch_fixtures.py"
    )


@pytest.fixture
def load_fixture():
    return _read
