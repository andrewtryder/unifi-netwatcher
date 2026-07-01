from .pushover import PushoverProvider
from .webhook import WebhookProvider

PROVIDERS = {
    "pushover": PushoverProvider(),
    "webhook": WebhookProvider()
}
