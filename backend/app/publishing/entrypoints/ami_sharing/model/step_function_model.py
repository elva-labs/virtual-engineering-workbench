from typing import Optional

from pydantic import BaseModel, Field


class DecideActionRequest(BaseModel):
    event_type: str = Field("DecideActionRequest", alias="eventType")
    product_id: str = Field(..., alias="productId")
    version_id: str = Field(..., alias="versionId")
    aws_account_id: str = Field(..., alias="awsAccountId")
    stage: str = Field(..., alias="stage")
    product_type: str = Field(..., alias="productType")


class DecideActionResponse(BaseModel):
    event_type: str = Field("DecideActionResponse", alias="eventType")
    decision: str = Field(..., alias="decision", description="COPY/SHARE/DONE/LICENSE_GRANT/VERIFY_EXTERNAL_GRANT")
    original_ami_id: Optional[str] = Field(None, alias="originalAmiId")
    copied_ami_id: Optional[str] = Field(None, alias="copiedAmiId")
    region: str = Field(..., alias="region")
    image_id: Optional[str] = Field(None, alias="imageId")
    project_id: Optional[str] = Field(None, alias="projectId")
    license_arn: Optional[str] = Field(None, alias="licenseArn")
    home_region: Optional[str] = Field(None, alias="homeRegion")
    grant_name: Optional[str] = Field(None, alias="grantName")


class CopyAmiRequest(BaseModel):
    event_type: str = Field("CopyAmiRequest", alias="eventType")
    original_ami_id: str = Field(..., alias="originalAmiId")
    region: str = Field(..., alias="region")


class CopyAmiResponse(BaseModel):
    event_type: str = Field("CopyAmiResponse", alias="eventType")
    copied_ami_id: str = Field(..., alias="copiedAmiId")


class ShareAmiRequest(BaseModel):
    event_type: str = Field("ShareAmiRequest", alias="eventType")
    original_ami_id: str = Field(..., alias="originalAmiId")
    copied_ami_id: str = Field(..., alias="copiedAmiId")
    region: str = Field(..., alias="region")
    aws_account_id: str = Field(..., alias="awsAccountId")


class ShareAmiResponse(BaseModel):
    event_type: str = Field("ShareAmiResponse", alias="eventType")


class StoreAmiRequest(BaseModel):
    event_type: str = Field("StoreAmiRequest", alias="eventType")
    source_ami_id: str = Field(..., alias="sourceAmiId")
    region: str = Field(..., alias="region")
    aws_account_id: str = Field(..., alias="awsAccountId")


class StoreAmiResponse(BaseModel):
    event_type: str = Field("StoreAmiResponse", alias="eventType")
    object_key: str = Field(..., alias="objectKey")


class VerifyStoreRequest(BaseModel):
    event_type: str = Field("VerifyStoreRequest", alias="eventType")
    source_ami_id: str = Field(..., alias="sourceAmiId")
    region: str = Field(..., alias="region")


class VerifyStoreResponse(BaseModel):
    event_type: str = Field("VerifyStoreResponse", alias="eventType")
    is_store_verified: bool = Field(..., alias="isStoreVerified")


class RestoreAmiRequest(BaseModel):
    event_type: str = Field("RestoreAmiRequest", alias="eventType")
    original_ami_id: str = Field(..., alias="originalAmiId")
    object_key: str = Field(..., alias="objectKey")
    region: str = Field(..., alias="region")
    aws_account_id: str = Field(..., alias="awsAccountId")


class RestoreAmiResponse(BaseModel):
    event_type: str = Field("RestoreAmiResponse", alias="eventType")
    distributed_ami_id: str = Field(..., alias="distributedAmiId")


class VerifyRestoreRequest(BaseModel):
    event_type: str = Field("VerifyRestoreRequest", alias="eventType")
    distributed_ami_id: str = Field(..., alias="distributedAmiId")
    region: str = Field(..., alias="region")
    aws_account_id: str = Field(..., alias="awsAccountId")


class VerifyRestoreResponse(BaseModel):
    event_type: str = Field("VerifyRestoreResponse", alias="eventType")
    is_restore_verified: bool = Field(..., alias="isRestoreVerified")


class VerifyCopyRequest(BaseModel):
    event_type: str = Field("VerifyCopyRequest", alias="eventType")
    region: str = Field(..., alias="region")
    copied_ami_id: str = Field(..., alias="copiedAmiId")


class VerifyCopyResponse(BaseModel):
    event_type: str = Field("VerifyCopyResponse", alias="eventType")
    is_copy_verified: bool = Field(..., alias="isCopyVerified")


class SucceedAmiSharingRequest(BaseModel):
    event_type: str = Field("SucceedAmiSharingRequest", alias="eventType")
    product_id: str = Field(..., alias="productId")
    version_id: str = Field(..., alias="versionId")
    aws_account_id: str = Field(..., alias="awsAccountId")
    stage: str = Field(..., alias="stage")
    copied_ami_id: Optional[str] = Field(
        ..., alias="copiedAmiId"
    )  # Required but nullable: callers must explicitly pass this field, even if the value is None
    previous_event_name: str = Field(..., alias="previousEventName")
    old_version_id: Optional[str] = Field(None, alias="oldVersionId")
    product_type: str = Field(..., alias="productType")


class SucceedAmiSharingResponse(BaseModel):
    event_type: str = Field("SucceedAmiSharingResponse", alias="eventType")


class FailAmiSharingRequest(BaseModel):
    event_type: str = Field("FailAmiSharingRequest", alias="eventType")
    product_id: str = Field(..., alias="productId")
    version_id: str = Field(..., alias="versionId")
    aws_account_id: str = Field(..., alias="awsAccountId")
    # The state machine sets $.error on every route into FailAmiSharingLambda,
    # either from a task catch or from the Pass on a failing Choice branch.
    # Optional so an older in-flight execution without it still parses.
    error: Optional[dict] = Field(None, alias="error")
    stage: str = Field(..., alias="stage")


class FailAmiSharingResponse(BaseModel):
    event_type: str = Field("FailAmiSharingResponse", alias="eventType")


class CreateLicenseGrantRequest(BaseModel):
    event_type: str = Field("CreateLicenseGrantRequest", alias="eventType")
    product_id: str = Field(..., alias="productId")
    version_id: str = Field(..., alias="versionId")
    aws_account_id: str = Field(..., alias="awsAccountId")
    image_id: str = Field(..., alias="imageId")
    grant_name: str = Field(..., alias="grantName")
    project_id: str = Field(..., alias="projectId")


class CreateLicenseGrantResponse(BaseModel):
    event_type: str = Field("CreateLicenseGrantResponse", alias="eventType")
    grant_id: str = Field(..., alias="grantId")
    grant_arn: str = Field(..., alias="grantArn")
    home_region: str = Field(..., alias="homeRegion")


class AcceptLicenseGrantRequest(BaseModel):
    event_type: str = Field("AcceptLicenseGrantRequest", alias="eventType")
    grant_id: str = Field(..., alias="grantId")
    grant_arn: str = Field(..., alias="grantArn")
    aws_account_id: str = Field(..., alias="awsAccountId")
    project_id: str = Field(..., alias="projectId")
    home_region: str = Field(..., alias="homeRegion")


class AcceptLicenseGrantResponse(BaseModel):
    event_type: str = Field("AcceptLicenseGrantResponse", alias="eventType")


class VerifyLicenseGrantRequest(BaseModel):
    event_type: str = Field("VerifyLicenseGrantRequest", alias="eventType")
    ami_id: str = Field(..., alias="amiId")
    region: str = Field(..., alias="region")
    aws_account_id: str = Field(..., alias="awsAccountId")


class VerifyLicenseGrantResponse(BaseModel):
    event_type: str = Field("VerifyLicenseGrantResponse", alias="eventType")
    is_accessible: bool = Field(..., alias="isAccessible")


class VerifyExternalGrantRequest(BaseModel):
    event_type: str = Field("VerifyExternalGrantRequest", alias="eventType")
    product_id: str = Field(..., alias="productId")
    version_id: str = Field(..., alias="versionId")
    aws_account_id: str = Field(..., alias="awsAccountId")
    license_arn: str = Field(..., alias="licenseArn")
    ami_id: str = Field(..., alias="amiId")
    region: str = Field(..., alias="region")
    home_region: str = Field(..., alias="homeRegion")


class VerifyExternalGrantResponse(BaseModel):
    event_type: str = Field("VerifyExternalGrantResponse", alias="eventType")
    grant_verified: bool = Field(..., alias="grantVerified")
