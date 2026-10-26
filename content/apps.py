from django.apps import AppConfig


class ContentConfig(AppConfig):
    name = "content"
    verbose_name = "Content"

    def ready(self):
        from . import signals  # noqa: F401
