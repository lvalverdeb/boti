from boti import Logger, ManagedResource, ProjectService, SecureResource, is_secure_path
from boti.core import (
    Logger as CoreLogger,
    ManagedResource as CoreManagedResource,
    ProjectService as CoreProjectService,
    SecureResource as CoreSecureResource,
    is_secure_path as core_is_secure_path,
)


def test_top_level_boti_reexports_curated_core_api():
    assert Logger is CoreLogger
    assert ManagedResource is CoreManagedResource
    assert ProjectService is CoreProjectService
    assert SecureResource is CoreSecureResource
    assert is_secure_path is core_is_secure_path
