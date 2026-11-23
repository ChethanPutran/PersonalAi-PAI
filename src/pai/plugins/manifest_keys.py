"""Single source of truth for every manifest key.

Never write a bare string literal like "capabilities" in a parser,
validator, or test — always refer to these.
"""
from __future__ import annotations


class K:
    # Top-level
    SCHEMA_VERSION = "schemaVersion"
    ID = "id"
    NAME = "name"
    TYPE = "type"
    VERSION = "version"
    DESCRIPTION = "description"
    AUTHOR = "author"
    LICENSE = "license"
    RUNTIME = "runtime"
    PLATFORMS = "platforms"
    ARCHITECTURES = "architectures"
    DEVICE_TYPES = "deviceTypes"
    PERMISSIONS = "permissions"
    DEPENDENCIES = "dependencies"
    POST_INSTALL = "postInstall"
    METADATA = "metadata"
    CONFIG_SCHEMA = "configSchema"          # opt-in, rarely used
    CAPABILITIES = "capabilities"

    class Runtime:
        KIND = "kind"
        LANGUAGE = "language"                # new, recommended
        NATIVE_MODULE = "nativeModule"
        MIN_PAI_VERSION = "minPaiVersion"
        ARTIFACT = "artifact"
        ENTRYPOINT = "entrypoint"

    class Platform:
        ARTIFACT = "artifact"
        ENTRYPOINT = "entrypoint"

    class Cap:
        ID = "id"
        DESCRIPTION = "description"
        RISK = "risk"
        REQUIRE_CONFIRMATION = "require_Confirmation"
        CONFIRMATION_REASON = "confirmationReason"
        CONFIRMATION_PREVIEW = "confirmationPreview"
        PRECONDITION = "precondition"
        PRECONDITIONS = "preconditions"
        PERMISSIONS = "permissions"
        PARAMETERS = "parameters"

    class Param:
        TYPE = "type"
        REQUIRED = "required"
        DESCRIPTION = "description"
        PROPERTIES = "properties"
        DEFAULT = "default"
        ENUM = "enum"
        ITEMS = "items"
        PATTERN = "pattern"
        MINIMUM = "minimum"
        MAXIMUM = "maximum"

    class Precondition:
        TYPE = "type"
        FIELD = "field"
        ALLOWED_ROOTS_REF = "allowedRootsRef"
        ALLOWED_PREFIXES_REF = "allowedPrefixesRef"
