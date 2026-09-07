"""Unified platform adapter interface."""

from __future__ import annotations

from abc import ABC, abstractmethod


class PlatformAdapter(ABC):
    @abstractmethod
    async def run(self) -> None:
        """Run the adapter until it is stopped."""

