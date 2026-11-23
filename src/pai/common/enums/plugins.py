from enum import Enum
class ArtifactStoreType(str, Enum):
    LOCAL = "local"
    S3 = "s3"
