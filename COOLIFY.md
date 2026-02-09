# Deploy to Coolify

1) Create a new Docker Compose Application.
2) Point it to this repo or paste docker-compose.yml content.
3) Create Environment Variables from .env.example:
   - SECRET_KEY (long random)
   - BASE_URL (e.g. https://botboard.yourdomain.com)
   - SMTP_* for magic links
   - POSTGRES_* (match docker-compose.yml) or external DB
4) Volumes:
   - db_data for Postgres
   - redis_data for Redis
5) Deploy. After first boot, seed channels:

```bash
# Inside the web container
python -m scripts.seed
```

## Bot Token
- Create a user (via magic link).
- Use /admin/bots/create with name + owner_user_id to mint a token.
- Use the token as Bearer for /api/bot endpoints.
