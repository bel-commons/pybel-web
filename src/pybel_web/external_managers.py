# -*- coding: utf-8 -*-

"""This module contains the optional managers"""

import logging

from pybel_tools.pipeline import in_place_mutator

__all__ = [
    'chebi_manager',
    'hgnc_manager',
    'mirtarbase_manager',
    'expasy_manager',
    'go_manager',
    'entrez_manager',
    'interpro_manager',
]

log = logging.getLogger(__name__)

try:
    import bio2bel_chebi
except ImportError:
    chebi_manager = None
else:
    log.info('Using Bio2BEL ChEBI')
    chebi_manager = bio2bel_chebi.Manager()
    chebi_manager.create_all()
    in_place_mutator(chebi_manager.enrich_chemical_hierarchy)

try:
    import bio2bel_hgnc
except ImportError:
    hgnc_manager = None
else:
    log.info('Using Bio2BEL HGNC')
    hgnc_manager = bio2bel_hgnc.Manager()
    hgnc_manager.create_all()
    in_place_mutator(hgnc_manager.enrich_genes_with_families)
    in_place_mutator(hgnc_manager.enrich_families_with_genes)

try:
    import bio2bel_mirtarbase
except ImportError:
    mirtarbase_manager = None
else:
    log.info('Using Bio2BEL miRTarBase')
    mirtarbase_manager = bio2bel_mirtarbase.Manager()
    mirtarbase_manager.create_all()
    in_place_mutator(mirtarbase_manager.enrich_mirnas)
    in_place_mutator(mirtarbase_manager.enrich_rnas)

try:
    import bio2bel_expasy
except ImportError:
    expasy_manager = None
else:
    log.info('Using Bio2BEL ExPASy')
    expasy_manager = bio2bel_expasy.Manager()
    expasy_manager.create_all()
    in_place_mutator(expasy_manager.enrich_proteins_with_enzyme_families)
    in_place_mutator(expasy_manager.enrich_enzymes)

try:
    import bio2bel_go
except ImportError:
    go_manager = None
else:
    log.info('Using Bio2BEL GO')
    go_manager = bio2bel_go.Manager()
    in_place_mutator(go_manager.enrich_bioprocesses)

try:
    import bio2bel_entrez
except ImportError:
    entrez_manager = None
else:
    log.info('Using Bio2BEL Entrez')
    entrez_manager = bio2bel_entrez.Manager()
    entrez_manager.create_all()

try:
    import bio2bel_interpro
except ImportError:
    interpro_manager = None
else:
    log.info('Using Bio2BEL Interpro')
    interpro_manager = bio2bel_interpro.Manager()
    interpro_manager.create_all()

manager_dict = {
    name: manager
    for name, manager in locals().items()
    if name.endswith('_manager') and manager
}