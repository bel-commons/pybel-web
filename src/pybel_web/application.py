# -*- coding: utf-8 -*-


"""
Resources:

1. https://citizen-stig.github.io/2016/02/17/using-celery-with-flask-factories.html
2. https://github.com/citizen-stig/celery-with-flask-factories
3. https://blog.miguelgrinberg.com/post/celery-and-the-flask-application-factory-pattern
4. http://flask.pocoo.org/docs/0.12/patterns/celery/
"""

import logging
import os
import socket
import time
from getpass import getuser

import flask
from celery import Celery
from flask import Flask
from flask_bootstrap import Bootstrap, WebCDN
from flask_mail import Mail, Message
from flask_security import Security, SQLAlchemyUserDatastore

from pybel.constants import config as pybel_config, PYBEL_CONNECTION
from pybel.manager import build_manager, Base
from pybel_tools.api import DatabaseService
from .forms import ExtendedRegisterForm
from .models import Role, User

log = logging.getLogger(__name__)


class _FlaskPybelState:
    """Represents the internal state of the PyBEL Flask Extention"""

    def __init__(self, manager):
        """
        :param pybel.manager.cache.CacheManager manager: A cache manager
        """
        self.manager = manager
        self.api = DatabaseService(manager=self.manager)
        self.user_datastore = SQLAlchemyUserDatastore(self.manager, User, Role)


class FlaskPybel:
    """Encapsulates the data needed for the PyBEL Web Application"""

    def __init__(self, app=None):
        """
        :param flask.Flask app: A Flask app
        """
        self.app = app
        self.state = None

        if app is not None:
            self.init_app(app)

    def init_app(self, app):
        """
        :param flask.Flask app:
        """
        manager = build_manager(app.config.get(PYBEL_CONNECTION))

        Base.metadata.bind = manager.engine
        Base.query = manager.session.query_property()

        self.state = _FlaskPybelState(manager)

        if app.config.get('PYBEL_DS_PRELOAD', False):
            log.info('preloading networks')
            self.state.api.cache_networks(
                check_version=app.config.get('PYBEL_DS_CHECK_VERSION', True),
                force_reload=app.config.get('PYBEL_WEB_FORCE_RELOAD', False),
                eager=app.config.get('PYBEL_DS_EAGER', False)
            )
            log.info('pre-loaded the dict service')

        app.extensions = getattr(app, 'extensions', {})
        app.extensions['pybel'] = self.state


bootstrap = Bootstrap()
pybel = FlaskPybel()
mail = Mail()
security = Security()
jquery2_cdn = WebCDN('//cdnjs.cloudflare.com/ajax/libs/jquery/2.1.1/')


def create_application(get_mail=False, **kwargs):
    """Builds a Flask app for the PyBEL web service
    
    1. Loads default config
    2. Updates with kwargs
    
    :param dict kwargs: keyword arguments to add to config
    :param bool get_mail: Activate the return have a tuple of (Flask, Mail)
    :rtype: flask.Flask
    """
    app = Flask(__name__)

    app.config.from_object('pybel_web.config.Config')

    if 'PYBEL_WEB_CONFIG' in os.environ:
        env_conf_path = os.path.expanduser(os.environ['PYBEL_WEB_CONFIG'])
        if not os.path.exists(env_conf_path):
            log.warning('configuration from environment at %s does not exist', env_conf_path)
        else:
            log.info('importing config from %s', env_conf_path)
            app.config.from_json(env_conf_path)

    app.config.update(pybel_config)
    app.config.update(kwargs)

    # Initialize extensions
    bootstrap.init_app(app)

    # TODO upgrade to jQuery 2?
    # See: https://pythonhosted.org/Flask-Bootstrap/faq.html#why-are-you-shipping-jquery-1-instead-of-jquery-2
    # app.extensions['bootstrap']['cdns']['jquery'] = jquery2_cdn

    if app.config.get('MAIL_SERVER'):
        mail.init_app(app)

        if app.config.get('PYBEL_WEB_STARTUP_NOTIFY'):
            startup_message = Message(
                subject="PyBEL Web - Startup",
                body="PyBEL Web was started on {} by {} at {}".format(socket.gethostname(), getuser(), time.asctime()),
                sender=("PyBEL Web", 'pybel@scai.fraunhofer.de'),
                recipients=[app.config.get('PYBEL_WEB_STARTUP_NOTIFY')]
            )
            with app.app_context():
                mail.send(startup_message)

    pybel.init_app(app)
    security.init_app(app, pybel.state.user_datastore, register_form=ExtendedRegisterForm)

    @app.before_first_request
    def create_user():
        pybel.state.manager.create_all()
        pybel.state.user_datastore.find_or_create_role(name='admin', description='Administrator of PyBEL Web')
        pybel.state.user_datastore.find_or_create_role(name='scai', description='Users from Fraunhofer SCAI')
        pybel.state.manager.session.commit()

    if not get_mail:
        return app

    return app, mail


def create_celery(application):
    """Configures celery instance from application, using its config

    :param flask.Flask application: Flask application instance
    :return: A Celery instance
    :rtype: celery.Celery
    """
    celery = Celery(application.import_name, broker=application.config['CELERY_BROKER_URL'])
    celery.conf.update(application.config)
    TaskBase = celery.Task

    class ContextTask(TaskBase):
        abstract = True

        def __call__(self, *args, **kwargs):
            with application.app_context():
                return TaskBase.__call__(self, *args, **kwargs)

    celery.Task = ContextTask
    return celery


def get_state(app):
    """
    :param flask.Flask app: A Flask app
    :rtype: web.application._FlaskPybelState
    """
    if 'pybel' not in app.extensions:
        raise ValueError

    return app.extensions['pybel']


def get_manager(app):
    """Gets the cache manger from a Flask app

    :param flask.Flask app: A Flask app
    :rtype: pybel.manager.cache.CacheManager
    """
    return get_state(app).manager


def get_api(app):
    """Gets the dictionary service from a Flask app

    :param flask.Flask app: A Flask app
    :rtype: DatabaseService
    """
    return get_state(app).api


def get_userdatastore(app):
    """Gets the User Data Store from a Flask app

    :param flask.Flask app: A Flask app
    :rtype: flask_security.DatabaseService
    """
    return get_state(app).user_datastore
