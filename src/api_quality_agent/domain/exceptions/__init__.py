from api_quality_agent.domain.exceptions.errors import (
    AmbiguousResourceError,
    ApiQualityAgentError,
    AuthenticationError,
    BackupError,
    BackupIntegrityError,
    BaselineAlreadyExistsError,
    ConfigurationError,
    ConflictError,
    InputError,
    IntegrationError,
    ResourceNotFoundError,
    SelectionError,
    UpdateNotApprovedError,
)
from api_quality_agent.domain.exceptions.contract_errors import StrictContractMatchFailedError
from api_quality_agent.domain.exceptions.execution_result_errors import (
    InvalidExecutionResultError,
    ReportAlreadyExistsError,
    UnsupportedExecutionResultSchemaError,
)
from api_quality_agent.domain.exceptions.input_errors import (
    CorruptedInputFileError,
    EmptyInputError,
    InputEncodingError,
    InputFileNotFoundError,
    InputSizeLimitExceededError,
    InvalidJsonError,
    UnsupportedInputExtensionError,
)
from api_quality_agent.domain.exceptions.managed_block_errors import (
    CorruptedManagedBlockError,
    DuplicateManagedBlockError,
    UnclosedManagedBlockError,
)
from api_quality_agent.domain.exceptions.postman_collection_errors import (
    InvalidPostmanCollectionError,
)
from api_quality_agent.domain.exceptions.specification_errors import (
    InvalidApiSpecificationError,
    UnresolvedReferenceError,
    UnsupportedSpecificationVersionError,
)

__all__ = [
    "AmbiguousResourceError",
    "ApiQualityAgentError",
    "AuthenticationError",
    "BackupError",
    "BackupIntegrityError",
    "BaselineAlreadyExistsError",
    "ConfigurationError",
    "ConflictError",
    "CorruptedInputFileError",
    "CorruptedManagedBlockError",
    "DuplicateManagedBlockError",
    "EmptyInputError",
    "InputEncodingError",
    "InputError",
    "InputFileNotFoundError",
    "InputSizeLimitExceededError",
    "IntegrationError",
    "InvalidApiSpecificationError",
    "InvalidExecutionResultError",
    "InvalidJsonError",
    "InvalidPostmanCollectionError",
    "ReportAlreadyExistsError",
    "ResourceNotFoundError",
    "SelectionError",
    "StrictContractMatchFailedError",
    "UnclosedManagedBlockError",
    "UnresolvedReferenceError",
    "UnsupportedExecutionResultSchemaError",
    "UnsupportedInputExtensionError",
    "UnsupportedSpecificationVersionError",
    "UpdateNotApprovedError",
]
