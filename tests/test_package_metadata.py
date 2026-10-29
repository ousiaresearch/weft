import tomllib
from pathlib import Path


def test_local_build_metadata_declares_alpha_and_entrypoint():
    repo = Path(__file__).resolve().parents[1]
    metadata = tomllib.loads((repo / 'pyproject.toml').read_text())
    project = metadata['project']
    assert project['name'] == 'weft-memory'
    assert 'a' in project['version']
    import weft
    assert weft.__version__ == project['version']
    assert project['requires-python'] == '>=3.11'
    assert project['scripts']['weft'] == 'weft.cli:entrypoint'
    assert any(dep.startswith('sqlite-vec') for dep in project['dependencies'])
    assert any(dep.startswith('pypdf') for dep in project['dependencies'])
    assert any(dep.startswith('python-docx') for dep in project['dependencies'])
    assert any(dep.startswith('mlx-embeddings') for dep in project['optional-dependencies']['local-embedding'])
