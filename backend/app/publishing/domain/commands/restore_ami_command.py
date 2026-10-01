from pydantic import BaseModel, ConfigDict

from app.publishing.domain.value_objects import ami_id_value_object, aws_account_id_value_object, region_value_object


class RestoreAmiCommand(BaseModel):
    """Restores a stored image in the target account it was stored for."""

    originalAmiId: ami_id_value_object.AmiIdValueObject
    objectKey: str
    region: region_value_object.RegionValueObject
    awsAccountId: aws_account_id_value_object.AWSAccountIDValueObject
    model_config = ConfigDict(arbitrary_types_allowed=True)
