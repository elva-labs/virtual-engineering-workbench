from pydantic import BaseModel, ConfigDict

from app.publishing.domain.value_objects import aws_account_id_value_object, stage_value_object, tech_id_value_object


class DistributePlatformVersionsCommand(BaseModel):
    """Give a newly onboarded program account the platform versions already at its stage (docs/platform-products.md)."""

    technologyId: tech_id_value_object.TechIdValueObject
    awsAccountId: aws_account_id_value_object.AWSAccountIDValueObject
    stage: stage_value_object.StageValueObject
    model_config = ConfigDict(arbitrary_types_allowed=True)
