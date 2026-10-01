from infra import config

MODES = ("share", "store-restore")


def image_distribution(app_config: config.BaseConfig) -> dict:
    """The env config "image-distribution" with defaults: mode "share", store with the image service role."""
    settings = {"mode": "share", "storeWithFunctionRole": False}
    settings.update(getattr(app_config, "environment_config", {}).get("image-distribution") or {})
    if settings["mode"] not in MODES:
        raise ValueError(f"image-distribution mode must be one of {MODES}, not {settings['mode']!r}")
    return settings


def is_store_restore(app_config: config.BaseConfig) -> bool:
    return image_distribution(app_config)["mode"] == "store-restore"
