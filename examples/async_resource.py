"""
ManagedResource example with native asynchronous cleanup.
"""

from __future__ import annotations

import asyncio

from boti.core import ManagedResource


class AsyncResource(ManagedResource):
    """Custom resource that implements the async cleanup hook."""

    def __init__(self, label: str) -> None:
        super().__init__()
        self.label = label
        self.cleaned_up_async = False

    async def ping(self) -> str:
        await asyncio.sleep(0)
        return f"AsyncResource(label={self.label!r}, closed={self.closed})"

    async def _acleanup(self) -> None:
        await asyncio.sleep(0)
        self.cleaned_up_async = True


async def main() -> None:
    async with AsyncResource(label="example-async") as resource:
        print(await resource.ping())

    print(f"closed={resource.closed}, cleaned_up_async={resource.cleaned_up_async}")


if __name__ == "__main__":
    asyncio.run(main())
