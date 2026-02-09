# Import all models so SQLAlchemy registers them and resolves relationships
from app.models.user import User          # noqa: F401
from app.models.bot import Bot            # noqa: F401
from app.models.api_token import ApiToken  # noqa: F401
from app.models.channel import Channel    # noqa: F401
from app.models.post import Post          # noqa: F401
from app.models.comment import Comment    # noqa: F401
