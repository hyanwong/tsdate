# MIT License
#
# Copyright (c) 2021-23 Tskit Developers
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
"""
Tools for phasing singleton mutations
"""

import numba
import numpy as np
import tskit

from .approx import _f
from .approx import _f1r
from .approx import _f1w
from .approx import _f2r
from .approx import _f2w
from .approx import _i
from .approx import _i1r
from .approx import _i1w
from .approx import _i2r
from .approx import _i2w
from .approx import _b
from .approx import _b1r
from .approx import _tuple
from .approx import _void

# --- machinery used by ExpectationPropagation class --- #

@numba.njit(_void(_f2w, _f1r, _i1r, _i2r))
def reallocate_unphased(edges_likelihood, mutations_phase, mutations_block, blocks_edges):
    """
    Add a proportion of each unphased singleton mutation to one of the two
    edges to which it maps
    """
    assert mutations_phase.size == mutations_block.size
    assert blocks_edges.shape[0] == 2

    num_mutations = mutations_phase.size
    num_edges = edges_likelihood.shape[0]
    num_blocks = blocks_edges.shape[0]

    edges_unphased = np.full(num_edges, False)
    edges_unphased[blocks_edges[0]] = True
    edges_unphased[blocks_edges[1]] = True

    num_unphased = np.sum(edges_likelihood[edges_unphased, 0])
    edges_likelihood[edges_unphased, 0] = 0.0
    for m, b in enumerate(mutations_block):
        if b == tskit.NULL:
            continue
        i, j = blocks_edges[0, b], blocks_edges[1, b]
        assert tskit.NULL < i < num_edges and edges_unphased[i]
        assert tskit.NULL < j < num_edges and edges_unphased[j]
        assert 0.0 <= mutations_phase[m] <= 1.0
        edges_likelihood[i, 0] += mutations_phase[m]
        edges_likelihood[j, 0] += 1 - mutations_phase[m]
    assert np.isclose(num_unphased, np.sum(edges_likelihood[edges_unphased, 0]))
        

@numba.njit(_tuple((_f2w, _i2w, _i1w))(_b1r, _i1r, _i1r, _f1r, _i1r, _i1r, _f1r, _f1r, _i1r, _i1r, _f))
def _block_singletons(individuals_unphased, nodes_individual, mutations_node, mutations_position, edges_parent, edges_child, edges_left, edges_right, indexes_insert, indexes_remove, sequence_length):
    """
    TODO
    """
    assert edges_parent.size == edges_child.size == edges_left.size == edges_right.size
    assert indexes_insert.size == indexes_remove.size == edges_parent.size
    assert mutations_node.size == mutations_position.size

    num_nodes = nodes_individual.size
    num_mutations = mutations_node.size
    num_edges = edges_parent.size
    num_individuals = individuals_unphased.size

    indexes_mutation = np.argsort(mutations_position)
    position_insert = edges_left[indexes_insert]
    position_remove = edges_right[indexes_remove]
    position_mutation = mutations_position[indexes_mutation]

    individuals_edges = np.full((num_individuals, 2), tskit.NULL)
    individuals_position = np.full(num_individuals, np.nan)
    individuals_singletons = np.zeros(num_individuals)
    individuals_block = np.full(num_edges, tskit.NULL)
    mutations_block = np.full(num_mutations, tskit.NULL)

    blocks_span = []
    blocks_singletons = []
    blocks_edges = []
    blocks_order = []

    num_blocks = 0
    left = 0.0
    a, b, d = 0, 0, 0
    while a < num_edges or b < num_edges:
        while b < num_edges and position_remove[b] == left:  # edges out
            e = indexes_remove[b] 
            p, c = edges_parent[e], edges_child[e]
            i = nodes_individual[c]
            if i != tskit.NULL and individuals_unphased[i]:
                u, v = individuals_edges[i]
                assert u == e or v == e
                s = u if v == e else v
                individuals_edges[i] = s, tskit.NULL
                if s != tskit.NULL:  # flush block
                    blocks_order.append(individuals_block[i])
                    blocks_edges.extend([e, s])
                    blocks_singletons.append(individuals_singletons[i])
                    blocks_span.append(left - individuals_position[i])
                    individuals_position[i] = np.nan
                    individuals_block[i] = tskit.NULL
                    individuals_singletons[i] = 0.0
            b += 1
        
        while a < num_edges and position_insert[a] == left:  # edges in
            e = indexes_insert[a]
            p, c = edges_parent[e], edges_child[e]
            i = nodes_individual[c]
            if i != tskit.NULL and individuals_unphased[i]:
                u, v = individuals_edges[i]
                assert u == tskit.NULL or v == tskit.NULL
                individuals_edges[i] = [e, max(u, v)]
                individuals_position[i] = left
                if individuals_block[i] == tskit.NULL:
                    individuals_block[i] = num_blocks
                    num_blocks += 1
            a += 1

        right = sequence_length
        if b < num_edges:
            right = min(right, position_remove[b])
        if a < num_edges:
            right = min(right, position_insert[a])
        left = right
        
        while d < num_mutations and position_mutation[d] < right:  # mutations
            m = indexes_mutation[d]
            c = mutations_node[m]
            i = nodes_individual[c]
            if i != tskit.NULL and individuals_unphased[i]:
                mutations_block[m] = individuals_block[i]
                individuals_singletons[i] += 1.0
            d += 1
    
    mutations_block = mutations_block.astype(np.int32)
    blocks_edges = np.array(blocks_edges, dtype=np.int32).reshape(-1, 2)
    blocks_singletons = np.array(blocks_singletons)
    blocks_span = np.array(blocks_span)
    blocks_order = np.array(blocks_order)
    blocks_stats = np.column_stack((blocks_singletons, blocks_span))
    assert num_blocks == blocks_edges.shape[0] == blocks_stats.shape[0]

    # sort block arrays so that mutations_block points to correct row
    blocks_order = np.argsort(blocks_order)
    blocks_edges = blocks_edges[blocks_order]
    blocks_stats = blocks_stats[blocks_order]

    return blocks_stats, blocks_edges, mutations_block


def block_singletons(ts, individuals_unphased):
    """
    TODO
    """
    for i in ts.individuals():
        if individuals_unphased[i.id]:
            if i.nodes.size != 2:
                raise ValueError("Singleton blocking assumes diploid individuals")
            if not np.all(ts.nodes_time[i.nodes] == 0.0):
                raise ValueError("Singleton blocking assumes contemporary individuals")

    # TODO: adjust spans by an accessibility mask
    return _block_singletons(
        individuals_unphased,
        ts.nodes_individual,
        ts.mutations_node,
        ts.sites_position[ts.mutations_site],
        ts.edges_parent,
        ts.edges_child,
        ts.edges_left,
        ts.edges_right,
        ts.indexes_edge_insertion_order,
        ts.indexes_edge_removal_order,
        ts.sequence_length,
    )


@numba.njit(_tuple((_f2w, _i1w))(_i1r, _f1r, _i1r, _i1r, _f1r, _f1r, _i1r, _i1r, _i, _f))
def _count_mutations(mutations_node, mutations_position, edges_parent, edges_child, edges_left, edges_right, indexes_insert, indexes_remove, num_nodes, sequence_length):
    """
    TODO
    """
    assert edges_parent.size == edges_child.size == edges_left.size == edges_right.size
    assert indexes_insert.size == indexes_remove.size == edges_parent.size
    assert mutations_node.size == mutations_position.size

    num_mutations = mutations_node.size
    num_edges = edges_parent.size

    indexes_mutation = np.argsort(mutations_position)
    position_insert = edges_left[indexes_insert]
    position_remove = edges_right[indexes_remove]
    position_mutation = mutations_position[indexes_mutation]

    nodes_edge = np.full(num_nodes, tskit.NULL)
    mutations_edge = np.full(num_mutations, tskit.NULL)
    edges_mutations = np.zeros(num_edges)
    edges_span = edges_right - edges_left

    left = 0.0
    a, b, d = 0, 0, 0
    while a < num_edges or b < num_edges:
        while b < num_edges and position_remove[b] == left:  # edges out
            e = indexes_remove[b] 
            p, c = edges_parent[e], edges_child[e]
            nodes_edge[c] = tskit.NULL
            b += 1
        
        while a < num_edges and position_insert[a] == left:  # edges in
            e = indexes_insert[a]
            p, c = edges_parent[e], edges_child[e]
            nodes_edge[c] = e
            a += 1

        right = sequence_length
        if b < num_edges:
            right = min(right, position_remove[b])
        if a < num_edges:
            right = min(right, position_insert[a])
        left = right
        
        while d < num_mutations and position_mutation[d] < right:
            m = indexes_mutation[d]
            c = mutations_node[m]
            e = nodes_edge[c]
            if e != tskit.NULL:
                mutations_edge[m] = e
                edges_mutations[e] += 1.0
            d += 1
    
    mutations_edge = mutations_edge.astype(np.int32)
    edges_stats = np.column_stack((edges_mutations, edges_span))

    return edges_stats, mutations_edge


def count_mutations(ts):
    """
    TODO
    """
    # TODO: adjust spans by an accessibility mask
    return _count_mutations(
        ts.mutations_node,
        ts.sites_position[ts.mutations_site],
        ts.edges_parent,
        ts.edges_child,
        ts.edges_left,
        ts.edges_right,
        ts.indexes_edge_insertion_order,
        ts.indexes_edge_removal_order,
        ts.num_nodes,
        ts.sequence_length,
    )


# --- helper functions --- #

def remove_singletons(ts):
    """
    Remove all singleton mutations from the tree sequence.

    Return the new ts, along with the id of the removed mutations in the
    original tree sequence.
    """

    nodes_sample = np.bitwise_and(ts.nodes_flags, tskit.NODE_IS_SAMPLE).astype(bool)
    assert np.sum(nodes_sample) == ts.num_samples
    assert np.all(~nodes_sample[ts.edges_parent]), "Sample node has a child"
    singletons = nodes_sample[ts.mutations_node]

    old_metadata = np.array(tskit.unpack_strings(
        ts.tables.mutations.metadata, 
        ts.tables.mutations.metadata_offset,
    ))
    old_state = np.array(tskit.unpack_strings(
        ts.tables.mutations.derived_state, 
        ts.tables.mutations.derived_state_offset,
    ))
    new_metadata, new_metadata_offset = tskit.pack_strings(old_metadata[~singletons])
    new_state, new_state_offset = tskit.pack_strings(old_state[~singletons])

    tables = ts.dump_tables()
    tables.mutations.set_columns(
        node=ts.mutations_node[~singletons],
        time=ts.mutations_time[~singletons],
        site=ts.mutations_site[~singletons],
        derived_state=new_state,
        derived_state_offset=new_state_offset,
        metadata=new_metadata,
        metadata_offset=new_metadata_offset,
    )
    tables.sort()
    tables.build_index()
    tables.compute_mutation_parents()

    return tables.tree_sequence(), np.flatnonzero(singletons)


def rephase_singletons(ts, use_node_times=True, random_seed=None):
    """
    Rephase singleton mutations in the tree sequence. If `use_node_times`
    is True, singletons are added to permissable branches with probability
    proportional to the branch length (and with equal probability otherwise).
    """
    rng = np.random.default_rng(random_seed)

    mutations_node = ts.mutations_node.copy()
    mutations_time = ts.mutations_time.copy()

    singletons = np.bitwise_and(ts.nodes_flags[mutations_node], tskit.NODE_IS_SAMPLE)
    singletons = np.flatnonzero(singletons)
    tree = ts.first()
    for i in singletons:
        position = ts.sites_position[ts.mutations_site[i]]
        individual = ts.nodes_individual[ts.mutations_node[i]]
        time = ts.nodes_time[ts.mutations_node[i]]
        assert individual != tskit.NULL
        assert time == 0.0
        tree.seek(position)
        nodes_id = ts.individual(individual).nodes
        nodes_length = np.array([tree.time(tree.parent(n)) - time for n in nodes_id])
        nodes_prob = nodes_length if use_node_times else np.ones(nodes_id.size)
        mutations_node[i] = rng.choice(nodes_id, p=nodes_prob / nodes_prob.sum(), size=1)
        if not np.isnan(mutations_time[i]):
            mutations_time[i] = (time + tree.time(tree.parent(mutations_node[i]))) / 2

    # TODO: add metadata with phase probability
    tables = ts.dump_tables()
    tables.mutations.node = mutations_node
    tables.mutations.time = mutations_time
    tables.sort()
    return tables.tree_sequence(), singletons


def insert_unphased_singletons(ts, position, individual, reference_state, alternate_state, allow_overlapping_sites=False):
    """
    Insert unphased singletons into the tree sequence. The phase is arbitrarily chosen 
    so that the mutation subtends the node with the lowest id, at a given position for a
    a given individual.

    :param tskit.TreeSequence ts: the tree sequence to add singletons to
    :param np.ndarray position: the position of the variants
    :param np.ndarray individual: the individual id in which the variant occurs
    :param np.ndarray reference_state: the reference state of the variant
    :param np.ndarray alternate_state: the alternate state of the variant
    :param bool allow_overlapping_sites: whether to permit insertion of
        singletons at existing sites (in which case the reference states must be
        consistent)

    :returns: A copy of the tree sequence with singletons inserted
    """
    # TODO: provenance / metdata
    tables = ts.dump_tables()
    individuals_node = {i.id: min(i.nodes) for i in ts.individuals()}
    sites_id = {p: i for i, p in enumerate(ts.sites_position)}
    overlap = False
    for pos, ind, ref, alt in zip(position, individual, reference_state, alternate_state):
        if ind not in individuals_nodes:
            raise LookupError(f"Individual {ind} is not in the tree sequence")
        if pos in sites_id:
            if not allow_overlapping_sites:
                raise ValueError(f"A site already exists at position {pos}")
            if ref != ts.site(sites_id[pos]).ancestral_state:
                raise ValueError(
                    f"Existing site at position {pos} has a different ancestral state"
                )
            overlap = True
        else:
            sites_id[pos] = tables.sites.add_row(position=pos, ancestral_state=ref)
        tables.mutations.add_row(
            site=sites_id[pos],
            node=individuals_node[ind],
            time=tskit.UNKNOWN_TIME,
            derived_state=alt,
        )
    tables.sort()
    if allow_overlapping_sites and overlap:
        tables.build_index()
        tables.compute_mutation_parents()
    return tables.tree_sequence()


