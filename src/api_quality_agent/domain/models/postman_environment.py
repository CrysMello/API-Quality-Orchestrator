from dataclasses import dataclass

from api_quality_agent.domain.models.environment_variable import EnvironmentVariable


@dataclass(frozen=True)
class PostmanEnvironment:
    name: str | None
    variables: tuple[EnvironmentVariable, ...]

    def get(self, key: str) -> EnvironmentVariable | None:
        # Variáveis desabilitadas nunca resolvem — mesmo comportamento do
        # Postman/Newman em runtime.
        return next(
            (variable for variable in self.variables if variable.key == key and variable.enabled),
            None,
        )
