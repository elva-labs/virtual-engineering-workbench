from app.shared.middleware.authorization import VirtualWorkbenchRoles

ALL_STAGES = ["DEV", "QA", "PROD"]

# The stages a program role may consume. It used to shape only the product listing; launching and the
# version listing enforce it too, so a user who calls the API directly cannot reach a DEV or QA
# release (a stage is a product's release channel; plain users consume PROD).
_ALL_STAGE_ROLES = {
    VirtualWorkbenchRoles.Admin,
    VirtualWorkbenchRoles.ProgramOwner,
    VirtualWorkbenchRoles.PowerUser,
    VirtualWorkbenchRoles.ProductContributor,
}


def allowed_stages(roles: list[str]) -> list[str]:
    if any(role in _ALL_STAGE_ROLES for role in roles):
        return list(ALL_STAGES)
    if VirtualWorkbenchRoles.BetaUser in roles:
        return ["QA", "PROD"]
    return ["PROD"]


def is_allowed(roles: list[str], stage: str) -> bool:
    return str(stage).upper() in allowed_stages(roles)
