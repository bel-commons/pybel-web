# -*- coding: utf-8 -*-

"""A blueprint for differential gene expression (-omics) experiments and their analysis."""

import json
import logging
import time
from collections import defaultdict
from io import StringIO
from operator import itemgetter
from typing import Iterable, List, Optional

import flask
import numpy as np
import pandas as pd
import pandas.errors
from flask import Blueprint, current_app, make_response, redirect, render_template, request, url_for
from flask_security import current_user, login_required, roles_required
from sklearn.cluster import KMeans

from bel_commons.celery_worker import celery_app
from bel_commons.core import manager
from pybel_tools.analysis.heat import RESULT_LABELS
from ..forms import DifferentialGeneExpressionForm
from ..manager_utils import create_omic, next_or_jsonify
from ..models import Experiment, Omic, UserQuery

__all__ = [
    'experiment_blueprint',
]

logger = logging.getLogger(__name__)

experiment_blueprint = Blueprint('analysis', __name__, url_prefix='/experiment')


@experiment_blueprint.route('/omic/')
def view_omics():
    """View a list of all omics data sets."""
    query = manager.session.query(Omic)

    if not (current_user.is_authenticated and current_user.is_admin):
        query = query.filter(Omic.public)

    query = query.order_by(Omic.created.desc())

    return render_template('omic/omics.html', omics=query.all(), current_user=current_user)


@experiment_blueprint.route('/omic/<int:omic_id>')
def view_omic(omic_id: int):
    """View an Omic model."""
    omic = manager.get_omic_by_id(omic_id)

    data = omic.get_source_dict()
    values = list(data.values())

    return render_template(
        'omic/omic.html',
        omic=omic,
        current_user=current_user,
        count=len(values),
        mean=np.mean(values),
        median=np.median(values),
        std=np.std(values),
        minimum=np.min(values),
        maximum=np.max(values)
    )


@experiment_blueprint.route('/omic', methods=['DELETE'])
@roles_required('admin')
def drop_omics():
    """Drop all -omics.

    ---
    tags:
        - omic

    responses:
      200:
        description: All -omics were dropped.
    """
    manager.session.query(Omic).delete()
    manager.session.commit()
    return next_or_jsonify('Dropped all Omic models')


@experiment_blueprint.route('/')
@experiment_blueprint.route('/from_query/<int:query_id>')
@experiment_blueprint.route('/from_omic/<int:omic_id>')
def view_experiments(query_id: Optional[int] = None, omic_id: Optional[int] = None):
    """View a list of all analyses, with optional filter by network id."""
    experiment_query = manager.session.query(Experiment)

    if query_id is not None:
        experiment_query = experiment_query.filter(Experiment.query_id == query_id)

    if omic_id is not None:
        experiment_query = experiment_query.filter(Experiment.omic_id == omic_id)

    return render_template(
        'experiment/experiments.html',
        experiments=experiment_query.order_by(Experiment.created.desc()).all(),
        current_user=current_user
    )


@experiment_blueprint.route('/drop')
@roles_required('admin')
def drop_experiments():
    """Drop all experiments.

    ---
    tags:
        - experiment

    responses:
      200:
        description: All experiments were dropped.
    """
    manager.session.query(Experiment).delete()
    manager.session.commit()
    return next_or_jsonify('Dropped all Experiment models')


@experiment_blueprint.route('/<int:experiment_id>')
def view_experiment(experiment_id: int):
    """View the results of a given analysis.

    :param experiment_id: The identifier of the experiment whose results to view
    """
    experiment = manager.authenticated_get_experiment_by_id(user=current_user, experiment_id=experiment_id)

    data = experiment.get_data_list()

    return render_template(
        'experiment/experiment.html',
        experiment=experiment,
        columns=RESULT_LABELS,
        data=sorted(data, key=itemgetter(1)),
        d3_data=json.dumps([v[3] for _, v in data]),
        current_user=current_user,
    )


@experiment_blueprint.route('/<int:experiment_id>', methods=['DELETE'])
@roles_required('admin')
def drop_experiment(experiment_id):
    """Delete an experiment.

    ---
    tags:
        - experiment

    parameters:
      - name: experiment_id
        in: path
        description: The database experiment identifier
        required: true
        type: integer

    responses:
      200:
        description: The experiment was dropped

    """
    manager.session.query(Experiment).get(experiment_id).delete()
    manager.session.commit()
    return next_or_jsonify('Dropped Experiment {}'.format(experiment_id))


@experiment_blueprint.route('/from_query/<int:query_id>/upload', methods=('GET', 'POST'))
@login_required
def view_query_uploader(query_id: int):
    """Render the asynchronous analysis page.

    :param query_id: The identifier of the query to upload against
    """
    query = manager.cu_get_query_by_id_or_404(query_id=query_id)

    form = DifferentialGeneExpressionForm()

    if not form.validate_on_submit():
        return render_template('experiment/analyze_dgx.html', form=form, query=query)

    t = time.time()

    logger.info(
        'running heat diffusion workflow on %s with %d trials',
        form.file.data.filename,
        form.permutations.data,
    )

    try:
        omic = create_omic(
            data=form.file.data,
            gene_column=form.gene_symbol_column.data,
            data_column=form.log_fold_change_column.data,
            source_name=form.file.data.filename,
            description=form.description.data,
            sep=form.separator.data,
            public=form.omics_public.data,
            user=current_user
        )
    except pandas.errors.ParserError:
        flask.flash('Malformed differential gene expression file. Check it is formatted consistently.',
                    category='warning')
        logger.exception('Malformed differential gene expression file.')
        return redirect(url_for('.view_query_uploader', query_id=query_id))

    experiment = Experiment(
        user=current_user,
        query=query,
        permutations=form.permutations.data,
        public=form.results_public.data,
        omic=omic,
    )

    manager.session.add(experiment)
    manager.session.commit()

    logger.debug('stored data for analysis in %.2f seconds', time.time() - t)

    task = celery_app.send_task('run-heat-diffusion', args=[
        current_app.config['SQLALCHEMY_DATABASE_URI'],
        experiment.id
    ])

    flask.flash('Queued Experiment {} with task {}. You can now upload another experiment for Query {}'.format(
        experiment.id, task, query_id)
    )
    return redirect(url_for('.view_query_uploader', query_id=query_id))


@experiment_blueprint.route('/from_network/<int:network_id>/upload/', methods=('GET', 'POST'))
@login_required
def view_network_uploader(network_id: int):
    """View the -*omics* data uploader for the given network."""
    network = manager.cu_get_network_by_id_or_404(network_id)
    user_query = UserQuery.from_network(network=network, user=current_user)
    manager.session.add(user_query)
    manager.session.commit()

    return redirect(url_for('.view_query_uploader', query_id=user_query.query_id))


@experiment_blueprint.route('/comparison/<list:experiment_ids>.tsv')
def download_experiment_comparison(experiment_ids: List[int]):
    """Render a comparison of several experiments.

    :param experiment_ids: The identifiers of experiments to compare
    """
    logger.info('working on experiments: %s', experiment_ids)

    clusters = request.args.get('clusters', type=int)
    normalize = request.args.get('normalize', type=int, default=0)
    seed = request.args.get('seed', type=int)

    experiments = manager.safe_get_experiments_by_ids(user=current_user, experiment_ids=experiment_ids)
    df = get_dataframe_from_experiments(experiments, normalize=normalize, clusters=clusters, seed=seed)

    si = StringIO()
    df.to_csv(si, index=False, sep='\t')
    output = make_response(si.getvalue())
    output.headers["Content-type"] = "text/tab-separated-values"
    return output


def render_experiment_comparison(experiments: List[Experiment]) -> flask.Response:
    """Render an experiment comparison."""
    experiments = list(experiments)
    experiment_ids = [experiment.id for experiment in experiments]

    return render_template(
        'experiment/experiments_compare.html',
        experiment_ids=experiment_ids,
        experiments=experiments,
        normalize=request.args.get('normalize', type=int, default=0),
        clusters=request.args.get('clusters', type=int),
        seed=request.args.get('seed', type=int),
    )


@experiment_blueprint.route('/comparison/<list:experiment_ids>')
def view_experiment_comparison(experiment_ids: List[int]):
    """Different data analyses on same query.

    :param experiment_ids: The identifiers of experiments to compare
    """
    experiments = manager.safe_get_experiments_by_ids(user=current_user, experiment_ids=experiment_ids)
    return render_experiment_comparison(experiments)


@experiment_blueprint.route('/comparison/query/<int:query_id>')
def view_query_experiment_comparison(query_id: int):
    """Different data analyses on same query.

    :param query_id: The query identifier whose related experiments to compare
    """
    query = manager.cu_get_query_by_id_or_404(query_id=query_id)
    return render_experiment_comparison(query.experiments)


def get_dataframe_from_experiments(experiments: Iterable[Experiment], *, normalize=None, clusters=None, seed=None):
    """Build a Pandas DataFrame from the list of experiments.

    :param iter[Experiment] experiments: Experiments to work on
    :param bool normalize:
    :param Optional[int] clusters: Number of clusters to use in k-means
    :param Optional[int] seed: Random number seed
    :rtype: pandas.DataFrame
    """
    x_label = ['Type', 'Namespace', 'Name']

    entries = defaultdict(list)

    for experiment in experiments:
        if experiment.result is None:
            continue

        x_label.append('[{}] {}'.format(experiment.id, experiment.source_name))

        for (func, namespace, name), values in sorted(experiment.get_data_list()):
            median_value = values[3]
            entries[func, namespace, name].append(median_value)

    result = [
        list(entry) + list(values)
        for entry, values in entries.items()
    ]

    df = pd.DataFrame(result, columns=x_label)
    df = df.fillna(0).round(4)

    data_columns = x_label[3:]

    if normalize:
        df[data_columns] = df[data_columns].apply(lambda x: (x - np.min(x)) / (np.max(x) - np.min(x)))

    if clusters is not None:
        logger.info('using %d-means clustering', clusters)
        logger.info('using seed: %s', seed)
        km = KMeans(n_clusters=clusters, random_state=seed)
        km.fit(df[data_columns])
        df['Group'] = km.labels_ + 1
        df = df.sort_values('Group')

    return df
