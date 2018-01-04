# -*- coding: utf-8 -*-

import logging
import os
import tempfile
import unittest
from uuid import uuid4

from pybel import Manager
from pybel.constants import INCREASES, PROTEIN
from pybel.examples import sialic_acid_graph
from pybel.manager.models import Edge, Node
from pybel_web.models import EdgeComment, EdgeVote, Query, User
from pybel_web.utils import get_or_create_vote

log = logging.getLogger(__name__)


class TemporaryCacheInstanceMixin(unittest.TestCase):
    def setUp(self):
        """Creates a temporary file to use as a persistent database throughout tests in this class. Subclasses of
        :class:`TemporaryCacheClsMixin` can extend :func:`TemporaryCacheClsMixin.setUpClass` to populate the database
        """
        self.fd, self.path = tempfile.mkstemp()
        self.connection = 'sqlite:///' + self.path
        log.info('test database at %s', self.connection)
        self.manager = Manager(connection=self.connection)
        self.manager.create_all()

    def tearDown(self):
        """Closes the connection to the database and removes the files created for it"""
        self.manager.session.close()
        os.close(self.fd)
        os.remove(self.path)

class TemporaryCacheClsMixin(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """Creates a temporary file to use as a persistent database throughout tests in this class. Subclasses of
        :class:`TemporaryCacheClsMixin` can extend :func:`TemporaryCacheClsMixin.setUpClass` to populate the database
        """
        cls.fd, cls.path = tempfile.mkstemp()
        cls.connection = 'sqlite:///' + cls.path
        log.info('test database at %s', cls.connection)
        cls.manager = Manager(connection=cls.connection)
        cls.manager.create_all()

    @classmethod
    def tearDownClass(cls):
        """Closes the connection to the database and removes the files created for it"""
        cls.manager.session.close()
        os.close(cls.fd)
        os.remove(cls.path)


class TestDrop(TemporaryCacheClsMixin):
    def test_drop_votes(self):  # TODO use mocks?
        network = self.manager.insert_graph(sialic_acid_graph)
        edges = list(network.edges.order_by(Edge.bel))
        edge = edges[0]
        user = User(email='test@example.com')
        vote = get_or_create_vote(self.manager, edge, user, agreed=True)
        self.assertIsNone(vote.changed)
        self.assertTrue(vote.agreed)

        vote = get_or_create_vote(self.manager, edge, user, agreed=False)
        self.assertIsNotNone(vote.changed)
        self.assertFalse(vote.agreed)


class TestDropInstance(TemporaryCacheInstanceMixin):
    def test_drop_edge_cascade_to_vote(self):
        n1 = Node(type=PROTEIN, bel='p(HGNC:A)')
        n2 = Node(type=PROTEIN, bel='p(HGNC:B)')
        n3 = Node(type=PROTEIN, bel='p(HGNC:C)')
        e1 = Edge(source=n1, target=n2, relation=INCREASES, bel='p(HGNC:A) increases p(HGNC:B)')
        e2 = Edge(source=n2, target=n3, relation=INCREASES, bel='p(HGNC:B) increases p(HGNC:C)')
        u1 = User()
        u2 = User()
        v1 = EdgeVote(user=u1, edge=e1)
        v2 = EdgeVote(user=u2, edge=e1)
        v3 = EdgeVote(user=u1, edge=e2)

        self.manager.session.add_all([n1, n2, n3, e1, e2, u1, v1, v2, v3])
        self.manager.session.commit()

        self.assertEqual(3, self.manager.session.query(Node).count())
        self.assertEqual(2, self.manager.session.query(Edge).count())
        self.assertEqual(2, self.manager.session.query(User).count())
        self.assertEqual(3, self.manager.session.query(EdgeVote).count())

        self.manager.session.delete(e1)
        self.manager.session.commit()

        self.assertEqual(1, self.manager.session.query(Edge).count())
        self.assertEqual(2, self.manager.session.query(User).count())
        self.assertEqual(1, self.manager.session.query(EdgeVote).count())

    def test_drop_edge_cascade_to_comment(self):
        n1 = Node(type=PROTEIN, bel='p(HGNC:A)')
        n2 = Node(type=PROTEIN, bel='p(HGNC:B)')
        n3 = Node(type=PROTEIN, bel='p(HGNC:C)')
        e1 = Edge(source=n1, target=n2, relation=INCREASES, bel='p(HGNC:A) increases p(HGNC:B)')
        e2 = Edge(source=n2, target=n3, relation=INCREASES, bel='p(HGNC:B) increases p(HGNC:C)')
        u1 = User()
        u2 = User()
        v1 = EdgeComment(user=u1, edge=e1, comment=str(uuid4()))
        v2 = EdgeComment(user=u2, edge=e1, comment=str(uuid4()))
        v3 = EdgeComment(user=u1, edge=e1, comment=str(uuid4()))
        v4 = EdgeComment(user=u1, edge=e2, comment=str(uuid4()))

        self.manager.session.add_all([n1, n2, n3, e1, e2, u1, v1, v2, v3, v4])
        self.manager.session.commit()

        self.assertEqual(3, self.manager.session.query(Node).count())
        self.assertEqual(2, self.manager.session.query(Edge).count())
        self.assertEqual(2, self.manager.session.query(User).count())
        self.assertEqual(4, self.manager.session.query(EdgeComment).count())

        self.manager.session.delete(e1)
        self.manager.session.commit()

        self.assertEqual(1, self.manager.session.query(Edge).count())
        self.assertEqual(2, self.manager.session.query(User).count())
        self.assertEqual(1, self.manager.session.query(EdgeComment).count())

    def test_drop_query_cascade_to_parent(self):
        """Tests that dropping a query gets passed to its parent, and doesn't muck up anything else"""

        q1 = Query()
        self.manager.session.add(q1)
        self.manager.session.commit()

        self.assertEqual(1, self.manager.session.query(Query).count(), msg='First query added unsuccessfully')

        q2 = Query(parent=q1)
        self.manager.session.add(q2)
        self.manager.session.commit()

        self.assertEqual(2, self.manager.session.query(Query).count(), msg='Second query added unsuccessfully')

        q3 = Query(parent=q2)
        self.manager.session.add(q3)
        self.manager.session.commit()

        self.assertEqual(3, self.manager.session.query(Query).count(), msg='Third query added unsuccessfully')

        q4 = Query()
        self.manager.session.add(q4)
        self.manager.session.commit()

        self.assertIsNotNone(self.manager.session.query(Query).get(q1.id))
        self.assertIsNotNone(self.manager.session.query(Query).get(q2.id))
        self.assertIsNotNone(self.manager.session.query(Query).get(q3.id))
        self.assertIsNotNone(self.manager.session.query(Query).get(q4.id))

        self.manager.session.delete(q1)
        self.manager.session.commit()

        self.assertIsNone(self.manager.session.query(Query).get(q1.id))
        self.assertIsNone(self.manager.session.query(Query).get(q2.id))
        self.assertIsNone(self.manager.session.query(Query).get(q3.id))
        self.assertIsNotNone(self.manager.session.query(Query).get(q4.id))
