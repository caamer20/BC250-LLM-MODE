"""Session 4 native test adapter.

Replaces the deleted compatibility facade in tests: composes a real
``Application`` over temporary paths and exposes only the narrow seams
tests need — the query read model, settings revision, repository appends,
and model seeding via typed repositories. A whole-state save is
intentionally impossible here, mirroring the production contract.
"""

from __future__ import annotations

from bc250_llm_mode.app import Application
from bc250_llm_mode.paths import AppPaths
from bc250_llm_mode.unit_of_work import UnitOfWorkFactory


class NativeApp:
    """Composition-native stand-in for the old ``CompatStateStore`` fixtures."""

    def __init__(self, tmp_path, name: str = "root") -> None:
        self.paths = AppPaths.temporary(tmp_path / name)
        self.application = Application.compose(self.paths)
        self.units = UnitOfWorkFactory(self.paths.database_path)

    def load(self):
        """Assembled frontend read model (disposable draft)."""
        return self.application.read_model()

    def reload(self):
        """A fresh composition's view of the same database."""
        return Application.compose(self.paths).read_model()

    def revision(self) -> int:
        from bc250_llm_mode.repositories import SettingsRepository

        with self.units.read() as conn:
            return SettingsRepository(conn).revision()

    def set_settings(self, values: dict) -> None:
        from bc250_llm_mode.repositories import SettingsRepository

        with self.units.begin() as conn:
            settings = SettingsRepository(conn)
            settings.set_many(values)
            settings.set_revision(settings.revision() + 1)

    def seed_models(self, models: list, *, current: str | None = None) -> None:
        from bc250_llm_mode.repositories import (
            ModelInstallationsRepository,
            RuntimeConfigRepository,
            SettingsRepository,
        )

        with self.units.begin() as conn:
            ModelInstallationsRepository(conn).replace_all(models)
            if current is not None:
                RuntimeConfigRepository(conn).update(
                    model_alias=current, context=8192, slots=1
                )
                settings = SettingsRepository(conn)
                settings.set_many({"current_model": current})
                settings.set_revision(settings.revision() + 1)

    def append_bench(self, entry: dict) -> None:
        from bc250_llm_mode.repositories import BenchHistoryRepository

        with self.units.begin() as conn:
            BenchHistoryRepository(conn).append(entry, commit=False)

    def record_benchmark(self, entry: dict) -> None:
        """U1.1 §8.6: frontends reach durable writes only via the app."""
        self.application.record_benchmark(entry)

    def append_autotune(self, entry: dict) -> None:
        from bc250_llm_mode.repositories import AutotuneHistoryRepository

        with self.units.begin() as conn:
            AutotuneHistoryRepository(conn).append(entry, commit=False)
