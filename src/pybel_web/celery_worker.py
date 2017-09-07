# -*- coding: utf-8 -*-

"""
Run the celery worker with:

:code:`python3 -m celery -A pybel_web.celery_worker.celery worker`

While also laughing at how ridiculously redundant this nomenclature is.
"""

import logging
import pickle

import hashlib
import os
import requests.exceptions
from celery.utils.log import get_task_logger
from flask_mail import Message
from sqlalchemy.exc import IntegrityError, OperationalError

from pybel import from_url, to_bytes
from pybel.constants import METADATA_DESCRIPTION, METADATA_CONTACT, METADATA_LICENSES
from pybel.manager import build_manager
from pybel.manager.models import Network
from pybel.parser.parse_exceptions import InconsistentDefinitionError
from pybel_tools.constants import BMS_BASE
from pybel_tools.ioutils import convert_directory
from pybel_tools.mutation import add_canonical_names, enrich_pubmed_citations
from pybel_tools.utils import enable_cool_mode
from .application import create_application
from .celery_utils import create_celery
from .constants import CHARLIE_EMAIL, DANIEL_EMAIL, integrity_message, log_worker_path
from .models import Report, Experiment
from .utils import calculate_scores

log = get_task_logger(__name__)

fh = logging.FileHandler(log_worker_path)
fh.setLevel(logging.DEBUG)
log.addHandler(fh)

app = create_application()
celery = create_celery(app)

dumb_belief_stuff = {
    METADATA_DESCRIPTION: {'Document description'},
    METADATA_CONTACT: {'your@email.com'},
    METADATA_LICENSES: {'Document license'}
}


def parse_folder(connection, folder, **kwargs):
    manager = build_manager(connection)
    convert_directory(
        folder,
        connection=manager,
        upload=True,
        infer_central_dogma=True,
        enrich_citations=True,
        enrich_genes=True,
        enrich_go=False,
        **kwargs
    )


@celery.task(name='parse-aetionomy')
def parse_aetionomy(connection):
    """Converts the AETIONOMY folder in the BMS"""
    folder = os.path.join(os.environ[BMS_BASE], 'aetionomy')
    parse_folder(connection, folder)


@celery.task(name='parse-selventa')
def parse_selventa(connection):
    """Converts the Selventa folder in the BMS"""
    folder = os.path.join(os.environ[BMS_BASE], 'selventa')
    parse_folder(connection, folder, citation_clearing=False)


@celery.task(name='parse-bel4imocede')
def parse_bel4imocede(connection):
    """Converts the BEL4IMOCEDE folder in the BMS"""
    folder = os.path.join(os.environ[BMS_BASE], 'BEL4IMOCEDE')
    parse_folder(connection, folder)


@celery.task(name='parse-ptsd')
def parse_ptsd(connection):
    """Converts the CVBIO PTSD folder in the BMS"""
    folder = os.path.join(os.environ[BMS_BASE], 'cvbio', 'PTSD')
    parse_folder(connection, folder)


@celery.task(name='parse-tbi')
def parse_tbi(connection):
    """Converts the CVBIO TBI folder in the BMS"""
    folder = os.path.join(os.environ[BMS_BASE], 'cvbio', 'TBI')
    parse_folder(connection, folder)


@celery.task(name='parse-bms')
def parse_bms(connection):
    """Converts the entire BMS"""
    parse_folder(connection, os.environ[BMS_BASE])


@celery.task(name='parse-url')
def parse_by_url(connection, url):
    """Parses a graph at the given URL resource"""
    # FIXME add proper exception handling and feedback

    manager = build_manager(connection)

    try:
        graph = from_url(url, manager=manager)
    except:
        return 'Parsing failed'

    try:
        network = manager.insert_graph(graph)
    except:
        manager.session.rollback()
        return 'Error parsing'

    return network.id


@celery.task(name='pybelparser')
def async_parser(connection, report_id):
    """Asynchronously parses a BEL script and sends email feedback

    :param str connection: RFC connection string
    :param int report_id: Report identifier
    """
    log.info('Starting parse task')
    manager = build_manager(connection)

    report = manager.session.query(Report).get(report_id)

    def make_mail(subject, message):
        if 'mail' not in app.extensions:
            return

        with app.app_context():
            app.extensions['mail'].send_message(
                subject=subject,
                recipients=[report.user.email],
                body=message,
                sender=("PyBEL Web", 'pybel@scai.fraunhofer.de'),
            )

    def finish_parsing(subject, message, log_exception=True):
        if log_exception:
            log.exception(message)
        make_mail(subject, message)
        report.message = message
        manager.session.commit()
        return message

    try:
        log.info('parsing graph')
        graph = report.parse_graph(manager=manager)

    except requests.exceptions.ConnectionError:
        message = 'Connection to resource could not be established.'
        return finish_parsing('Parsing Failed', message)

    except InconsistentDefinitionError as e:
        message = 'Parsing failed because {} was redefined on line {}.'.format(e.definition, e.line_number)
        return finish_parsing('Parsing Failed', message)

    except Exception as e:
        message = 'Parsing failed from a general error: {}'.format(e)
        return finish_parsing('Parsing Failed', message)

    if not graph.name:
        message = 'Graph does not have a name'
        return finish_parsing('Parsing Failed', message)

    if not graph.version:
        message = 'Graph does not have a version'
        return finish_parsing('Parsing Failed', message)

    problem = {
        k: v
        for k, v in graph.document.items()
        if k in dumb_belief_stuff and v in dumb_belief_stuff[k]
    }

    if problem:
        message = 'Your document was rejected because it has "default" metadata: {}'.format(problem)
        return finish_parsing('Document Rejected', message)

    network = manager.session.query(Network).filter(Network.name == graph.name,
                                                    Network.version == graph.version).one_or_none()

    if network is not None:
        message = integrity_message.format(graph.name, graph.version)

        if network.report.user == report.user:  # This user is being a fool
            return finish_parsing('Upload Failed', message)

        if hashlib.sha1(network.blob).hexdigest() != hashlib.sha1(to_bytes(network)).hexdigest():
            with app.app_context():
                app.extensions['mail'].send(Message(
                    subject='Possible attempted Espionage',
                    recipients=[CHARLIE_EMAIL, DANIEL_EMAIL],
                    body='The following user ({} {}) may have attempted espionage of network: {}'.format(
                        report.user.id,
                        report.user.email,
                        network
                    ),
                    sender=("PyBEL Web", 'pybel@scai.fraunhofer.de'),
                ))

            return finish_parsing('Upload Failed', message)

        # Grant rights to this user
        network.users.append(report.user)
        manager.session.commit()

        message = 'Granted rights for {} to {}'.format(network, report.user)
        return finish_parsing('Granted Rights', message, log_exception=False)

    try:
        log.info('enriching graph')
        add_canonical_names(graph)
        enrich_pubmed_citations(graph, manager=manager)
    except (IntegrityError, OperationalError):
        manager.session.rollback()
        log.exception('problem with database while fixing citations')
    except:
        log.exception('problem fixing citations')

    try:
        log.info('inserting graph')
        network = manager.insert_graph(graph, store_parts=app.config.get('PYBEL_USE_EDGE_STORE', True))

    except IntegrityError:
        manager.session.rollback()
        message = integrity_message.format(graph.name, graph.version)
        return finish_parsing('Upload Failed', message)

    except OperationalError:
        manager.session.rollback()
        message = 'Database is locked. Unable to upload.'
        return finish_parsing('Upload Failed', message)

    except Exception as e:
        manager.session.rollback()
        message = "Error storing in database: {}".format(e)
        return finish_parsing('Upload Failed', message)

    log.info('done storing [%d]', network.id)

    try:
        report.number_nodes = graph.number_of_nodes(),
        report.number_edges = graph.number_of_edges(),
        report.number_warnings = len(graph.warnings),

    except (IntegrityError, OperationalError):
        manager.session.rollback()
        message = 'Problem with reporting service.'
        return finish_parsing('Upload Failed', message)

    make_mail('Upload Successful', '{} is done parsing. Check the network list page.'.format(graph))

    report.completed = True
    manager.session.commit()

    return network.id


@celery.task(name='run-cmpa')
def run_cmpa(connection, experiment_id):
    """Runs the CMPA analysis

    :param str connection:
    :param int experiment_id:
    """
    log.info('Running experiment %s', experiment_id)
    manager = build_manager(connection)

    experiment = manager.session.query(Experiment).get(experiment_id)

    graph = experiment.query.run(manager)

    df = pickle.loads(experiment.source)

    gene_column = experiment.gene_column
    data_column = experiment.data_column

    data = {
        k: v
        for _, k, v in df.loc[df[gene_column].notnull(), [gene_column, data_column]].itertuples()
    }

    scores = calculate_scores(graph, data, experiment.permutations)

    experiment.result = pickle.dumps(scores)
    experiment.completed = True

    manager.session.commit()

    message = 'Experiment {} on query {} with {} has completed'.format(
        experiment_id,
        experiment.query_id,
        experiment.source_name
    )

    if 'mail' in app.extensions:
        with app.app_context():
            app.extensions['mail'].send_message(
                subject='CMPA Analysis complete',
                recipients=[experiment.user.email],
                body=message,
                sender=("PyBEL Web", 'pybel@scai.fraunhofer.de'),
            )

    return experiment_id


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    enable_cool_mode()  # turn off warnings for compilation
    log.setLevel(logging.DEBUG)
