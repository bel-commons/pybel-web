# -*- coding: utf-8 -*-

"""
Module that contains the command line app

Why does this file exist, and why not put this in __main__?
You might be tempted to import things from __main__ later, but that will cause
problems--the code will get executed twice:
 - When you run `python3 -m pybel_tools` python will execute
   ``__main__.py`` as a script. That means there won't be any
   ``pybel_tools.__main__`` in ``sys.modules``.
 - When you import __main__ it will get executed again (as a module) because
   there's no ``pybel_tools.__main__`` in ``sys.modules``.
Also see (1) from http://click.pocoo.org/5/setuptools/#setuptools-integration
"""

from __future__ import print_function

import datetime
import json
import logging
import os
import sys
import time

import click
from flask_security import SQLAlchemyUserDatastore

from pybel.constants import get_cache_connection, PYBEL_CONNECTION, PYBEL_DATA_DIR
from pybel.manager.cache import build_manager
from pybel.manager.models import Base, Network
from pybel.utils import get_version as pybel_version
from pybel_tools.utils import enable_cool_mode
from pybel_tools.utils import get_version as pybel_tools_get_version
from .admin_service import build_admin_service
from .analysis_service import analysis_blueprint
from .application import create_application
from .external_services import belief_blueprint
from .constants import log_runner_path, CHARLIE_EMAIL
from .curation_service import curation_blueprint
from .database_service import api_blueprint
from .main_service import build_main_service
from .models import Role, User, Report, Project, Experiment
from .parser_async_service import parser_async_blueprint
from .parser_endpoint import build_parser_service
from .upload_service import upload_blueprint
from .utils import iterate_user_strings

log = logging.getLogger('pybel_web')

datefmt = '%H:%M:%S'
fmt = "%(asctime)s - %(levelname)s - %(name)s - %(message)s"

user_dump_path = os.path.join(PYBEL_DATA_DIR, 'users.tsv')

fh = logging.FileHandler(log_runner_path)
fh.setLevel(logging.DEBUG)
fh.setFormatter(logging.Formatter(fmt))
log.addHandler(fh)


def set_debug(level):
    logging.basicConfig(level=level, format=fmt, datefmt=datefmt)

    pybel_log = logging.getLogger('pybel')
    pybel_log.setLevel(level)
    pybel_log.addHandler(fh)

    pbt_log = logging.getLogger('pybel_tools')
    pbt_log.setLevel(level)
    pbt_log.addHandler(fh)

    pbw_log = logging.getLogger('pybel_web')
    pbw_log.setLevel(level)


def set_debug_param(debug):
    if debug == 1:
        set_debug(20)
    elif debug == 2:
        set_debug(10)


@click.group(help="PyBEL-Tools Command Line Interface on {}\n with PyBEL v{}".format(sys.executable, pybel_version()))
@click.version_option()
def main():
    """PyBEL Tools Command Line Interface"""


_config_map = {
    'local': 'pybel_web.config.LocalConfig',
    'test': 'pybel_web.config.TestConfig',
    'prod': 'pybel_web.config.ProductionConfig'
}


@main.command()
@click.option('--host', default='0.0.0.0', help='Flask host. Defaults to localhost')
@click.option('--port', type=int, help='Flask port. Defaults to 5000')
@click.option('--default-config', type=click.Choice(['local', 'test', 'prod']),
              help='Use different default config object')
@click.option('-v', '--debug', count=True, help="Turn on debugging. More v's, more debugging")
@click.option('--flask-debug', is_flag=True, help="Turn on werkzeug debug mode")
@click.option('--config', type=click.File('r'), help='Additional configuration in a JSON file')
def run(host, port, default_config, debug, flask_debug, config):
    """Runs PyBEL Web"""
    set_debug_param(debug)
    if debug < 3:
        enable_cool_mode()

    log.info('Running PyBEL v%s', pybel_version())
    log.info('Running PyBEL Tools v%s', pybel_tools_get_version())

    if host is not None:
        log.info('Running on host: %s', host)

    if port is not None:
        log.info('Running on port: %d', port)

    t = time.time()

    config_dict = json.load(config) if config is not None else {}

    app = create_application(
        config_location=_config_map.get(default_config),
        **config_dict
    )

    build_main_service(app)
    build_admin_service(app)
    app.register_blueprint(curation_blueprint)
    app.register_blueprint(parser_async_blueprint)
    app.register_blueprint(upload_blueprint)
    app.register_blueprint(api_blueprint)
    app.register_blueprint(analysis_blueprint)
    app.register_blueprint(belief_blueprint)

    if app.config.get('PYBEL_WEB_PARSER_API'):
        build_parser_service(app)

    log.info('Done building %s in %.2f seconds', app, time.time() - t)

    app.run(debug=flask_debug, host=host, port=port)


@main.group()
@click.option('-c', '--connection', help='Cache connection. Defaults to {}'.format(get_cache_connection()))
@click.option('--config', type=click.File('r'), help='Specify configuration JSON file')
@click.pass_context
def manage(ctx, connection, config):
    """Manage database"""
    if config:
        file = json.load(config)
        ctx.obj = build_manager(file.get(PYBEL_CONNECTION, get_cache_connection()))
    else:
        ctx.obj = build_manager(connection)

    Base.metadata.bind = ctx.obj.engine
    Base.query = ctx.obj.session.query_property()


@manage.command()
@click.pass_obj
def setup(manager):
    """Creates the database"""
    manager.create_all()


@manage.command()
@click.option('-f', '--file', type=click.File('r'), default=user_dump_path, help='Input user/role file')
@click.pass_obj
def load(manager, file):
    """Dump stuff for loading later (in lieu of having proper migrations)"""
    ds = SQLAlchemyUserDatastore(manager, User, Role)
    for line in file:
        email, first, last, roles, password = line.strip().split('\t')
        u = ds.find_user(email=email)

        if not u:
            u = ds.create_user(
                email=email,
                first_name=first,
                last_name=last,
                password=password,
                confirmed_at=datetime.datetime.now()
            )
            click.echo('added {}'.format(u))
            ds.commit()
        for role_name in roles.strip().split(','):
            r = ds.find_role(role_name)
            if not r:
                r = ds.create_role(name=role_name)
                ds.commit()
            if not u.has_role(r):
                ds.add_role_to_user(u, r)

    ds.commit()


@manage.command()
@click.option('-y', '--yes', is_flag=True)
@click.pass_obj
def drop(manager, yes):
    """Drops database"""
    if yes or click.confirm('Drop database at {}?'.format(manager.connection)):
        click.echo('Dumped users to {}'.format(user_dump_path))
        with open(user_dump_path, 'w') as f:
            for s in iterate_user_strings(manager, True):
                print(s, file=f)
        click.echo('Done')
        click.echo('Dropping database')
        manager.drop_database()
        click.echo('Done')


@manage.command()
@click.pass_obj
def sanitize_reports(manager):
    """Adds charlie as the owner of all non-reported graphs"""
    ds = SQLAlchemyUserDatastore(manager, User, Role)
    u = ds.find_user(email=CHARLIE_EMAIL)
    click.echo('Adding {} as owner of unreported uploads'.format(u))

    for network in manager.session.query(Network):
        if network.report is not None:
            continue

        report = Report(
            network=network,
            user=u
        )

        manager.session.add(report)

        click.echo('Sanitizing {}'.format(network))

    manager.session.commit()


@manage.group()
def user():
    """Manage users"""


@user.command()
@click.option('-p', '--with-passwords', is_flag=True)
@click.pass_obj
def ls(manager, with_passwords):
    """Lists all users"""
    for s in iterate_user_strings(manager, with_passwords):
        click.echo(s)


@user.command()
@click.argument('email')
@click.argument('password')
@click.option('-a', '--admin', is_flag=True, help="Add admin role")
@click.option('-s', '--scai', is_flag=True, help="Add SCAI role")
@click.pass_obj
def add(manager, email, password, admin, scai):
    """Creates a new user"""
    ds = SQLAlchemyUserDatastore(manager, User, Role)
    try:
        u = ds.create_user(email=email, password=password, confirmed_at=datetime.datetime.now())

        if admin:
            ds.add_role_to_user(u, 'admin')

        if scai:
            ds.add_role_to_user(u, 'scai')

        ds.commit()
    except:
        log.exception("Couldn't create user")


@user.command()
@click.argument('email')
@click.pass_obj
def rm(manager, email):
    """Deletes a user"""
    ds = SQLAlchemyUserDatastore(manager, User, Role)
    u = ds.find_user(email=email)
    ds.delete_user(u)
    ds.commit()


@user.command()
@click.argument('email')
@click.pass_obj
def make_admin(manager, email):
    """Makes a given user an admin"""
    ds = SQLAlchemyUserDatastore(manager, User, Role)
    try:
        ds.add_role_to_user(email, 'admin')
        ds.commit()
    except:
        log.exception("Couldn't make admin")


@user.command()
@click.argument('email')
@click.argument('role')
@click.pass_obj
def add_role(manager, email, role):
    """Adds a role to a user"""
    ds = SQLAlchemyUserDatastore(manager, User, Role)
    try:
        ds.add_role_to_user(email, role)
        ds.commit()
    except:
        log.exception("Couldn't add role")


@manage.group()
def role():
    """Manage roles"""


@role.command()
@click.argument('name')
@click.option('-d', '--description')
@click.pass_obj
def add(manager, name, description):
    """Creates a new role"""
    ds = SQLAlchemyUserDatastore(manager, User, Role)
    try:
        ds.create_role(name=name, description=description)
        ds.commit()
    except:
        log.exception("Couldn't create role")


@role.command()
@click.argument('name')
@click.pass_obj
def rm(manager, name):
    """Deletes a user"""
    ds = SQLAlchemyUserDatastore(manager, User, Role)
    u = ds.find_role(name)
    if u:
        ds.delete(u)
        ds.commit()


@role.command()
@click.pass_obj
def ls(manager):
    """Lists roles"""
    for r in manager.session.query(Role).all():
        click.echo('{}\t{}'.format(r.name, r.description))


@manage.group()
def projects():
    """Manage projects"""


@projects.command()
@click.pass_obj
def ls(manager):
    """Lists projects"""
    for project in manager.session.query(Project).all():
        click.echo('{}\t{}'.format(project.name, ','.join(map(str, project.users))))


@manage.group()
def experiments():
    """Manage experiments"""


@experiments.command()
@click.pass_obj
def dropall(manager):
    """Drops all experiments"""
    if click.confirm('Drop all experiments at {}?'.format(manager.connection)):
        manager.session.query(Experiment).delete()


if __name__ == '__main__':
    main()
