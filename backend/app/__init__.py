import logging

from flask import Flask, jsonify

from app.config.settings import Config
from app.repositories.api_repository import APIHotelRepository
from app.routes.health_routes import health_bp
from app.routes.recommendation_routes import recommendation_bp
from app.utils.background_poller import BackgroundPoller


def create_app(config=None):
    app = Flask(__name__)

    # ------------------------------------------------------------------ config
    cfg = config or Config
    if config is not None:
        app.config.from_object(config)

    # ----------------------------------------------------------------- logging
    logging.basicConfig(
        level=getattr(logging, cfg.LOG_LEVEL, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # ----------------------------------------------------------- repository
    repo = APIHotelRepository(config=cfg)
    app.extensions["hotel_repo"] = repo

    # --------------------------------------------------------- background poller
    poller = BackgroundPoller(
        repository=repo,
        poll_interval=getattr(cfg, "POLL_INTERVAL_SECONDS", 50),
    )
    poller.start()
    app.extensions["hotel_poller"] = poller

    # --------------------------------------------------------------- blueprints
    app.register_blueprint(health_bp)
    app.register_blueprint(recommendation_bp)

    # ----------------------------------------------------------- error handlers
    @app.errorhandler(404)
    def not_found(exc):
        return jsonify({"error": "Not found"}), 404

    @app.errorhandler(500)
    def internal_error(exc):
        return jsonify({"error": "Internal server error"}), 500

    return app
