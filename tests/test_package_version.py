from pathlib import Path
import tomllib

import picklang
import durable_weave


def test_package_versions_match_release_metadata():
    version = tomllib.loads(Path('pyproject.toml').read_text())['project']['version']
    assert picklang.__version__ == durable_weave.__version__ == version
