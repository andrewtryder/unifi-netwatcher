from fastapi.templating import Jinja2Templates

from app.web.formatting import format_relative_ago

templates = Jinja2Templates(directory="app/web/templates")
templates.env.filters["relative_ago"] = lambda dt, parens=False: format_relative_ago(dt, parens=parens)
templates.env.cache = None
