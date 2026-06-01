from flask import Blueprint, current_app, jsonify

health_bp = Blueprint("health", __name__)


@health_bp.get("/health")
def health():
    poller = current_app.extensions.get("hotel_poller")
    poller_alive = poller.is_alive() if poller else False
    return jsonify({"status": "ok", "poller": {"alive": poller_alive}}), 200


@health_bp.post("/internal/cache/invalidate")
def invalidate_cache():
    from flask import request

    repo = current_app.extensions.get("hotel_repo")
    if repo is None:
        return jsonify({"error": "Repository not available"}), 503

    body = request.get_json(silent=True) or {}
    iata_code = body.get("iata_code")

    if iata_code:
        key = str(iata_code).upper()
        repo._cache.pop(key, None)
    else:
        repo._cache.clear()

    return jsonify({"invalidated": iata_code.upper() if iata_code else "__all__"}), 200
