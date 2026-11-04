"""Home_Management : application familiale (calendrier, tâches ménagères, courses)."""
import os
from datetime import timedelta

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from . import db

__version__ = "1.1.0"


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True, static_folder="static", static_url_path="/static")
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("HM_SECRET_KEY", "dev-change-me"),
        # Mot de passe partagé du foyer. Vide = pas d'authentification (usage local uniquement).
        APP_PASSWORD=os.environ.get("HM_PASSWORD", ""),
        # Jeton secret du flux iCal (abonnement depuis le calendrier de l'iPhone).
        ICS_TOKEN=os.environ.get("HM_ICS_TOKEN", ""),
        TIMEZONE=os.environ.get("HM_TIMEZONE", "Europe/Paris"),
        DATABASE=os.environ.get("HM_DATABASE", os.path.join(app.instance_path, "home.db")),
        PERMANENT_SESSION_LIFETIME=timedelta(days=365),
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=os.environ.get("HM_SECURE_COOKIE", "0") == "1",
        JSON_SORT_KEYS=False,
    )
    # Configuration optionnelle dans instance/config.py (pratique sur PythonAnywhere).
    app.config.from_pyfile("config.py", silent=True)
    if test_config:
        app.config.update(test_config)

    # Derrière le proxy HTTPS de PythonAnywhere : URLs générées en https.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

    os.makedirs(app.instance_path, exist_ok=True)
    db.init_app(app)

    from . import calendar, chores, core, ics, shopping

    app.register_blueprint(core.bp)
    app.register_blueprint(calendar.bp)
    app.register_blueprint(chores.bp)
    app.register_blueprint(shopping.bp)
    app.register_blueprint(ics.bp)

    @app.errorhandler(HTTPException)
    def json_errors(err):
        if request.path.startswith("/api/"):
            return jsonify(error=err.description or err.name), err.code
        return err

    def no_cache(response):
        response.headers["Cache-Control"] = "no-cache"
        return response

    @app.get("/")
    def index():
        return no_cache(send_from_directory(app.static_folder, "index.html"))

    # Servis à la racine pour que le service worker contrôle toute l'application.
    @app.get("/sw.js")
    def service_worker():
        return no_cache(send_from_directory(app.static_folder, "sw.js", mimetype="application/javascript"))

    @app.get("/manifest.webmanifest")
    def manifest():
        return no_cache(send_from_directory(app.static_folder, "manifest.webmanifest", mimetype="application/manifest+json"))

    return app
