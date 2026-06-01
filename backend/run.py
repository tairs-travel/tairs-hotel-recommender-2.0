from app import create_app
from app.config.settings import Config

# Module-level app instance — used by gunicorn: `gunicorn run:app`
app = create_app(config=Config)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
