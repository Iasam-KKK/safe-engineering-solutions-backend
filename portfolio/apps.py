from django.apps import AppConfig


class PortfolioConfig(AppConfig):
    name = "portfolio"
    verbose_name = "Portfolio"

    def ready(self):
        from . import signals  # noqa: F401
