"""Recommendation and airlines API routes."""

from __future__ import annotations

import json
import logging
import queue
import re
import threading
import time

import requests as http_requests
from flask import Blueprint, Response, current_app, jsonify, request, stream_with_context
from werkzeug.exceptions import BadRequest

from app.config.settings import Config
from app.controllers.recommendation_controller import handle_recommendations
from app.repositories.airline_repository import APIAirlineRepository
from app.services import get_recommender
from app.utils.airline_groups import get_airline_group
logger = logging.getLogger(__name__)

recommendation_bp = Blueprint("recommendations", __name__)

_airline_repo = APIAirlineRepository()

_sse_clients: list[queue.SimpleQueue] = []
_sse_lock = threading.Lock()
_current_airline_data: str | None = None
_watcher_thread: threading.Thread | None = None
_watcher_lock = threading.Lock()


def _airline_payload(a) -> dict:
    return {
        "display_name": a.display_name,
        "name": a.name,
        "iata_code": a.iata_code,
        "country": a.country,
        "lat": a.lat,
        "lng": a.lng,
        "group": get_airline_group(a.coach_price),
    }


def _serialize_airlines(airlines) -> str:
    return json.dumps([_airline_payload(a) for a in airlines])


def _broadcast(data: str) -> None:
    with _sse_lock:
        for client_queue in list(_sse_clients):
            client_queue.put_nowait(data)


def _airline_watcher(poll_interval: int = 30) -> None:
    global _current_airline_data
    repo = APIAirlineRepository()
    while True:
        try:
            fresh = repo._fetch_and_map()
            data = _serialize_airlines(fresh)
            if data != _current_airline_data:
                _current_airline_data = data
                _broadcast(data)
        except Exception as exc:
            logger.error("Airline watcher error: %s", exc)
        time.sleep(poll_interval)


def _ensure_watcher_running() -> None:
    global _watcher_thread
    with _watcher_lock:
        if _watcher_thread is None or not _watcher_thread.is_alive():
            _watcher_thread = threading.Thread(
                target=_airline_watcher,
                daemon=True,
                name="airline-watcher",
            )
            _watcher_thread.start()


@recommendation_bp.route("/recommendations", methods=["POST"])
def recommendations():
    resp = handle_recommendations(request)
    if isinstance(resp, tuple):
        return resp
    data = resp.get_json()
    if data is None:
        return resp
    warnings = get_recommender().last_warnings
    if warnings:
        data["warnings"] = warnings
        return jsonify(data), resp.status_code
    return resp


@recommendation_bp.route("/airlines", methods=["GET"])
def airlines():
    return jsonify(
        [_airline_payload(a) for a in _airline_repo.get_all_airlines()]
    )


@recommendation_bp.route("/airlines/stream")
def airlines_stream():
    testing = current_app.config.get("TESTING", False)
    if not testing:
        _ensure_watcher_running()

    def event_stream():
        client_queue: queue.SimpleQueue = queue.SimpleQueue()
        with _sse_lock:
            _sse_clients.append(client_queue)
        try:
            try:
                initial = _current_airline_data or _serialize_airlines(
                    _airline_repo.get_all_airlines()
                )
            except Exception:
                initial = "[]"
            yield f"data: {initial}\n\n"

            if testing:
                return

            while True:
                try:
                    data = client_queue.get(timeout=25)
                    yield f"data: {data}\n\n"
                except queue.Empty:
                    yield ": heartbeat\n\n"
        finally:
            with _sse_lock:
                try:
                    _sse_clients.remove(client_queue)
                except ValueError:
                    pass

    return Response(
        stream_with_context(event_stream()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# Allowed coordinate pattern: digits, minus, dots, commas and semicolons only
_COORDS_RE = re.compile(r'^[-\d.,;]+$')


@recommendation_bp.route("/osrm/route/<path:coords>", methods=["GET"])
def osrm_proxy(coords):
    """Proxy OSRM route requests to avoid CORS issues from the browser."""
    if not _COORDS_RE.match(coords):
        return jsonify({"error": "Invalid coordinates"}), 400
    osrm_url = Config.OSRM_URL.rstrip("/")
    params = {k: v for k, v in request.args.items()
              if k in ("geometries", "overview", "steps", "annotations")}
    try:
        resp = http_requests.get(
            f"{osrm_url}/route/v1/driving/{coords}",
            params=params,
            timeout=Config.OSRM_TIMEOUT,
        )
        return Response(
            resp.content,
            status=resp.status_code,
            content_type=resp.headers.get("Content-Type", "application/json"),
        )
    except http_requests.RequestException as exc:
        logger.error("OSRM proxy error: %s", exc)
        return jsonify({"error": "OSRM service unavailable"}), 502


@recommendation_bp.errorhandler(BadRequest)
def bad_request_handler(error):
    return jsonify({"error": str(error.description)}), 400
