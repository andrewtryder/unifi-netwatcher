from fastapi.templating import Jinja2Templates

from app.config import settings
from app.web.display import (
    bytes_r_to_mbps,
    connection_subtitle,
    device_display_name,
    device_fingerprint_subtitle,
    device_icon,
    satisfaction_color_class,
)
from app.web.formatting import format_relative_ago

templates = Jinja2Templates(directory="app/web/templates")
templates.env.filters["relative_ago"] = lambda dt, parens=False: format_relative_ago(
    dt, parens=parens
)
templates.env.globals.update(
    {
        "device_display_name": device_display_name,
        "device_fingerprint_subtitle": device_fingerprint_subtitle,
        "device_icon": device_icon,
        "connection_subtitle": connection_subtitle,
        "satisfaction_color_class": satisfaction_color_class,
        "bytes_r_to_mbps": bytes_r_to_mbps,
    }
)
# Disable Jinja bytecode cache only in development (template hot-reload).
if settings.APP_ENV == "development":
    templates.env.cache = None
