class ExtractorError(Exception):
    """Base error that the CLI can show without a traceback."""


class ConfigurationError(ExtractorError):
    pass


class ModelAPIError(ExtractorError):
    pass


class ModelResponseError(ExtractorError):
    pass
