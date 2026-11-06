import hashlib
import json

from app.shared.domain.ports.idempotency_service import IdempotencyScope


def derive_image_client_token(scope: IdempotencyScope, image_id: str) -> str:
    """Derive the same Image Builder token for every attempt at a reserved image."""
    identity = ["vew-image-builder-client-token-v1", scope.client_id, scope.project_id, scope.operation, image_id]
    encoded = json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
