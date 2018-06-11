# -*- coding: utf-8 -*-

"""Optional BMS views.

Requires setting the environment variable ``BMS_BASE`` and installation of gitpython.
"""

import hashlib
import logging

import git
from flask import Blueprint, current_app, flash, redirect, url_for
from flask_security import current_user, roles_required

from pybel_tools.ioutils import get_paths_recursive
from pybel_web.manager_utils import next_or_jsonify
from pybel_web.constants import *
from pybel_web.models import Report
from pybel_web.proxies import manager

log = logging.getLogger(__name__)

bms_blueprint = Blueprint('bms', __name__, url_prefix='/admin/bms')


def make_folder_queue(folder_path, allow_nested=False, citation_clearing=True, infer_origin=False):
    """This should be pretty similar to the one above

    :param str folder_path:
    :param bool allow_nested:
    :param bool citation_clearing:
    :param bool infer_origin:
    """
    tasks = []

    for path in get_paths_recursive(folder_path):
        with open(path, 'rb') as f:
            source_bytes = f.read()

        source_sha512 = hashlib.sha512(source_bytes).hexdigest()

        # check if another one has the same hash + settings

        report = Report(
            user=current_user,
            source_name=path,
            source=source_bytes,
            source_hash=source_sha512,
            public=False,
            allow_nested=allow_nested,
            citation_clearing=citation_clearing,
            infer_origin=infer_origin,
        )

        manager.session.add(report)

        try:
            manager.session.commit()
        except:
            manager.session.rollback()
            log.exception('Unable to upload BEL document')
            flash('Unable to upload BEL document')
            return redirect(url_for('ui.home'))

        report_id, report_name = report.id, report.source_name
        manager.session.close()
        task = current_app.celery.send_task('upload-bel', args=[report_id])

        tasks.append(task)

    return tasks


@bms_blueprint.route('/pull')
def git_pull():
    """Updates the Biological Model Store git repository"""
    g = git.cmd.Git(current_app.config.get('BMS_BASE'))
    res = g.pull()

    return next_or_jsonify(res)


@bms_blueprint.route('/parse/all')
@roles_required('admin')
def ensure_bms():
    """Parses and stores the entire Biological Model Store repository"""
    tasks = make_folder_queue(current_app.config.get('BMS_BASE'))
    return next_or_jsonify('Queued tasks to parse the BMS: {}'.format(tasks))


@bms_blueprint.route('/parse/aetionomy')
@roles_required('admin')
def ensure_aetionomy():
    """Parses and stores the AETIONOMY resources from the Biological Model Store repository"""
    folder = os.path.join(current_app.config.get('BMS_BASE'), 'aetionomy')
    tasks = make_folder_queue(folder)
    return next_or_jsonify('Queued task to parse the AETIONOMY folder: {}'.format(tasks))


@bms_blueprint.route('/parse/selventa')
@roles_required('admin')
def ensure_selventa():
    """Parses and stores the Selventa resources from the Biological Model Store repository"""
    folder = os.path.join(current_app.config.get('BMS_BASE'), 'selventa')
    tasks = make_folder_queue(folder, citation_clearing=False, allow_nested=True)
    return next_or_jsonify('Queued task to parse the Selventa folder: {}'.format(tasks))


@bms_blueprint.route('/parse/ptsd')
@roles_required('admin')
def ensure_ptsd():
    """Parses and stores the PTSD resources from the Biological Model Store repository"""
    folder = os.path.join(current_app.config.get('BMS_BASE'), 'cvbio', 'PTSD')
    tasks = make_folder_queue(folder)
    return next_or_jsonify('Queued task to parse the PTSD folder: {}'.format(tasks))


@bms_blueprint.route('/parse/tbi')
@roles_required('admin')
def ensure_tbi():
    """Parses and stores the TBI resources from the Biological Model Store repository"""
    folder = os.path.join(current_app.config.get('BMS_BASE'), 'cvbio', 'TBI')
    tasks = make_folder_queue(folder)
    return next_or_jsonify('Queued task to parse the TBI folder: {}'.format(tasks))


@bms_blueprint.route('/parse/bel4imocede')
@roles_required('admin')
def ensure_bel4imocede():
    """Parses and stores the BEL4IMOCEDE resources from the Biological Model Store repository"""
    folder = os.path.join(current_app.config.get('BMS_BASE'), 'BEL4IMOCEDE')
    tasks = make_folder_queue(folder)
    return next_or_jsonify('Queued task to parse the BEL4IMOCEDE folder: {}'.format(tasks))


@bms_blueprint.route('/parse/cbn')
def send_async_upload_cbn():
    """A helper endpoint to submit the parsing job for the CBN"""
    folder = os.path.join(current_app.config.get('BMS_BASE'), 'cbn')
    task = current_app.celery.send_task('upload-cbn', args=[folder])
    flash('Uploading CBN: {}'.format(task))
    return redirect(url_for('ui.view_current_user_activity'))