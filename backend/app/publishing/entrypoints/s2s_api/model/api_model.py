from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

ProductType = Literal["WORKBENCH", "VIRTUAL_TARGET", "CONTAINER"]
# PROGRAM: the program's own product. PLATFORM: released once by the releasing program, distributed to
# and listed in every program (docs/platform-products.md).
ProductScope = Literal["PROGRAM", "PLATFORM"]
ProductStatus = Literal["CREATING", "CREATED", "FAILED", "PAUSED", "ARCHIVING", "ARCHIVED"]
ProductStage = Literal["DEV", "QA", "PROD"]


class CreateProductRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    productName: str = Field(..., min_length=1, max_length=50)
    productType: ProductType
    productDescription: str = Field("", max_length=100)
    technologyId: str = Field(..., min_length=1)
    scope: ProductScope = "PROGRAM"


class UpdateProductRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    productName: str = Field(..., min_length=1, max_length=50)
    productDescription: str = Field("", max_length=100)


class CreateProductResponse(BaseModel):
    productId: str


class Product(BaseModel):
    projectId: str
    productId: str
    productName: str
    productType: ProductType
    productDescription: str = ""
    technologyId: str
    technologyName: str
    status: ProductStatus
    recommendedVersionId: Optional[str] = None
    availableStages: list[ProductStage] = Field(default_factory=list)
    scope: ProductScope = "PROGRAM"
    createDate: str
    lastUpdateDate: str


class ProductPage(BaseModel):
    products: list[Product]


class VersionStageState(BaseModel):
    stage: ProductStage
    status: str


class ProductVersion(BaseModel):
    versionId: str
    versionName: str
    versionType: str
    stages: list[VersionStageState]


class ProductVersionPage(BaseModel):
    versions: list[ProductVersion]


class VersionDistribution(BaseModel):
    awsAccountId: str
    region: str
    status: str


class VersionPromotion(BaseModel):
    projectId: str
    productId: str
    versionId: str
    versionName: str
    stage: ProductStage
    status: str
    distributions: list[VersionDistribution]
