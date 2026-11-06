import pytest

from boti import Logger, ManagedResource, ProjectService, SecureResource, is_secure_path
from boti.core import (
    Logger as CoreLogger,
)
from boti.core import (
    ManagedResource as CoreManagedResource,
)
from boti.core import (
    ProjectService as CoreProjectService,
)
from boti.core import (
    SecureResource as CoreSecureResource,
)
from boti.core import (
    is_secure_path as core_is_secure_path,
)


def test_top_level_boti_reexports_curated_core_api():
    assert Logger is CoreLogger
    assert ManagedResource is CoreManagedResource
    assert ProjectService is CoreProjectService
    assert SecureResource is CoreSecureResource
    assert is_secure_path is core_is_secure_path


def test_import_boti_does_not_load_core():
    """``import boti`` must stay cheap: boti.core (and fsspec/pyarrow behind it)
    load only on first attribute access. Checked in a fresh interpreter because
    this test process has already imported boti.core."""
    import json
    import subprocess
    import sys

    probe = (
        "import sys, boti\n"
        "before = sorted(m for m in sys.modules if m.startswith('boti'))\n"
        "boti.Logger\n"
        "after = 'boti.core' in sys.modules\n"
        "print(__import__('json').dumps([before, after]))\n"
    )
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
    before, after = json.loads(out.stdout)
    assert before == ["boti"]
    assert after is True


def test_boti_core_attribute_resolves_without_prior_import():
    import subprocess
    import sys

    probe = "import boti; print(boti.core.__name__)"
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "boti.core"


def test_top_level_unknown_attribute_raises():
    import boti

    with pytest.raises(AttributeError, match="no attribute 'nope'"):
        _ = boti.nope
