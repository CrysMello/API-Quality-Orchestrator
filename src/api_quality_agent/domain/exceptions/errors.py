class ApiQualityAgentError(Exception):
    pass


class ConfigurationError(ApiQualityAgentError):
    pass


class InputError(ApiQualityAgentError):
    pass


class SelectionError(ApiQualityAgentError):
    pass


class IntegrationError(ApiQualityAgentError):
    pass


class AuthenticationError(IntegrationError):
    pass


class ConflictError(IntegrationError):
    pass


class ResourceNotFoundError(SelectionError):
    pass


class AmbiguousResourceError(SelectionError):
    pass


class UpdateNotApprovedError(ApiQualityAgentError):
    pass


class BackupError(ApiQualityAgentError):
    pass


class BackupIntegrityError(BackupError):
    pass


class BaselineAlreadyExistsError(ApiQualityAgentError):
    pass


class PlaywrightGenerationNotImplementedError(ApiQualityAgentError):
    # Stub controlado (plano de ação Playwright, Parte 04): `--target
    # playwright`/`--target all` já roteiam corretamente para o caminho
    # Playwright, mas nenhum EndpointTestGenerator/PlaywrightTestSuiteBuilder
    # concreto existe ainda (só os contratos, ver generators/playwright/).
    pass
