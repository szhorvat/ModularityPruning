from .louvain_utilities import louvain_part_with_membership, sorted_tuple
from .champ_utilities import CHAMP_2D
from .partition_utilities import num_communities
import louvain
from math import log
import numpy as np
from scipy.optimize import fsolve


def estimate_singlelayer_SBM_parameters(G, partition, m=None):
    """Estimates singlelayer SBM parameters from a graph and a partition

    :param G: graph
    :param partition: partition
    :param m: total edge weight of graph (if None, will be computed)
    :return: omega_in, omega_out
    """

    if m is None:
        m = sum(G.es['weight'])

    assert isinstance(partition, louvain.RBConfigurationVertexPartition)
    community = partition.membership

    m_in = sum(e['weight'] * (community[e.source] == community[e.target]) for e in G.es)
    kappa_r_list = [0] * len(partition)
    for e in G.es:
        kappa_r_list[community[e.source]] += e['weight']
        kappa_r_list[community[e.target]] += e['weight']
    sum_kappa_sqr = sum(x ** 2 for x in kappa_r_list)

    omega_in = (2 * m_in) / (sum_kappa_sqr / (2 * m))
    # guard for div by zero with single community partition
    omega_out = (2 * m - 2 * m_in) / (2 * m - sum_kappa_sqr / (2 * m)) if len(partition) > 1 else 0

    # return estimates for omega_in, omega_out
    return omega_in, omega_out


def estimate_multilayer_SBM_parameters(G_intralayer, G_interlayer, layer_vec, partition, model, N=None, T=None,
                                       Nt=None, m_t=None):
    """Estimates multilayer SBM parameters from a graph and a partition

    :param G_intralayer: input graph containing all intra-layer edges
    :param G_interlayer: input graph containing all inter-layer edges
    :param layer_vec: vector of each vertex's layer membership
    :param partition: partition of interest
    :param model: network layer topology (temporal, multilevel, multiplex)
    :param N: number of nodes per layer
    :param T: number of layers in input graph
    :param Nt: vector of nodes per layer
    :param m_t: vector of total edge weights per layer
    :return: theta_in, theta_out, p, K
    """

    if 'weight' not in G_intralayer.es:
        G_intralayer.es['weight'] = [1.0] * G_intralayer.ecount()

    # TODO: check if these None parameters and potentially caching calculate_persistence helps performance
    if T is None:
        T = max(layer_vec) + 1  # layer  count

    if N is None:
        N = G_intralayer.vcount() // T

    if m_t is None:  # compute total edge weights per layer
        m_t = [0] * T
        for e in G_intralayer.es:
            m_t[layer_vec[e.source]] += e['weight']

    if Nt is None:  # compute total node counts per layer
        Nt = [0] * T
        for l in layer_vec:
            Nt[l] += 1

    K = len(partition)

    community = partition.membership
    m_t_in = [0] * T
    for e in G_intralayer.es:
        if community[e.source] == community[e.target] and layer_vec[e.source] == layer_vec[e.target]:
            m_t_in[layer_vec[e.source]] += e['weight']

    kappa_t_r_list = [[0] * K for _ in range(T)]
    for e in G_intralayer.es:
        layer = layer_vec[e.source]
        kappa_t_r_list[layer][community[e.source]] += e['weight']
        kappa_t_r_list[layer][community[e.target]] += e['weight']
    sum_kappa_t_sqr = [sum(x ** 2 for x in kappa_t_r_list[t]) for t in range(T)]

    theta_in = sum(2 * m_t_in[t] for t in range(T)) / sum(sum_kappa_t_sqr[t] / (2 * m_t[t]) for t in range(T))

    # guard for div by zero with e.g. a single community partition
    theta_out_numerator = sum(2 * m_t[t] - 2 * m_t_in[t] for t in range(T))
    theta_out_denominator = sum(2 * m_t[t] - sum_kappa_t_sqr[t] / (2 * m_t[t]) for t in range(T))
    if theta_out_denominator == 0:
        theta_out = 0
    else:
        theta_out = theta_out_numerator / theta_out_denominator

    calculate_persistence = persistence_function_from_model(model, G_interlayer, layer_vec=layer_vec, N=N, T=T, Nt=Nt)
    pers = calculate_persistence(community)
    if model is 'multiplex':
        # estimate p by solving polynomial root-finding problem with starting estimate p=0.5
        def f(x):
            coeff = 2 * (1 - 1 / K) / (T * (T - 1))
            return coeff * sum((T - n) * x ** n for n in range(1, T)) + 1 / K - pers

        # guard for div by zero with single community partition
        # (in this case, all community assignments persist across layers)
        p = fsolve(f, np.array([0.5]))[0] if pers < 1.0 and K > 1 else 1.0
    else:
        # guard for div by zero with single community partition
        # (in this case, all community assignments persist across layers)
        p = max((K * pers - 1) / (K - 1), 0) if pers < 1.0 and K > 1 else 1.0

    return theta_in, theta_out, p, K


def gamma_estimate(G, partition):
    """Returns the gamma estimate for a graph and a partition"""

    if 'weight' not in G.es:
        G.es['weight'] = [1.0] * G.vcount()

    if not isinstance(partition, louvain.RBConfigurationVertexPartition):
        partition = louvain_part_with_membership(G, partition)

    omega_in, omega_out = estimate_singlelayer_SBM_parameters(G, partition)
    return gamma_estimate_from_parameters(omega_in, omega_out)


def gamma_estimate_from_parameters(omega_in, omega_out):
    """Returns the gamma estimate for SBM parameters"""

    if omega_in == 0 or omega_out == 0:
        return None  # degenerate partition, this could reasonably be taken to be 0

    return (omega_in - omega_out) / (np.log(omega_in) - np.log(omega_out))


def multiplex_omega_estimate_from_parameters(theta_in, theta_out, p, K, T, omega_max=1000):
    """Returns the omega estimate for a multiplex multilayer model

    :param theta_in: SBM parameter
    :param theta_out: SBM parameter
    :param p: SBM parameter
    :param K: number of blocks in SBM
    :param T: number of layers in SBM
    :param omega_max: maximum allowed value for omega
    :return: omega estimate
    """

    # if p is 1, the optimal omega is infinite (here, omega_max)
    if p >= 1.0 or theta_in == 1.0:
        return omega_max

    if theta_out == 0:
        return log(1 + p * K / (1 - p)) / (T * log(theta_in))
    return log(1 + p * K / (1 - p)) / (T * (log(theta_in) - log(theta_out)))


def temporal_multilevel_omega_estimate_from_parameters(theta_in, theta_out, p, K, omega_max=1000):
    """Returns the omega estimate for a temporal or multilevel multilayer model

    :param theta_in: SBM parameter
    :param theta_out: SBM parameter
    :param p: SBM parameter
    :param K: number of blocks in SBM
    :param omega_max: maximum allowed value for omega
    :return: omega estimate
    """

    if theta_out == 0:
        return log(1 + p * K / (1 - p)) / (2 * log(theta_in)) if p < 1.0 else omega_max
    # if p is 1, the optimal omega is infinite (here, omega_max)
    return log(1 + p * K / (1 - p)) / (2 * (log(theta_in) - log(theta_out))) if p < 1.0 else omega_max


def ordinal_persistence(G_interlayer, community, N, T):
    # ordinal persistence (temporal model)
    return sum(community[e.source] == community[e.target] for e in G_interlayer.es) / (N * (T - 1))


def multilevel_persistence(G_interlayer, community, layer_vec, Nt, T):
    pers_per_layer = [0] * T
    for e in G_interlayer.es:
        pers_per_layer[layer_vec[e.target]] += (community[e.source] == community[e.target])

    pers_per_layer = [pers_per_layer[l] / Nt[l] for l in range(T)]
    return sum(pers_per_layer) / (T - 1)


def categorical_persistence(G_interlayer, community, N, T):
    # categorical persistence (multiplex model)
    return sum(community[e.source] == community[e.target] for e in G_interlayer.es) / (N * T * (T - 1))


def omega_function_from_model(model, omega_max, T):
    if model is 'multiplex':
        def update_omega(theta_in, theta_out, p, K):
            return multiplex_omega_estimate_from_parameters(theta_in, theta_out, p, K, T, omega_max=omega_max)
    elif model is 'temporal' or model is 'multilayer':
        def update_omega(theta_in, theta_out, p, K):
            return temporal_multilevel_omega_estimate_from_parameters(theta_in, theta_out, p, K, omega_max=omega_max)
    else:
        raise ValueError(f"Model {model} is not temporal, multilevel, or multiplex")

    return update_omega


def persistence_function_from_model(model, G_interlayer, layer_vec=None, N=None, T=None, Nt=None):
    """
    Returns a function to calculate persistence according to a given multilayer model

    :param model: network layer topology (temporal, multilevel, multiplex)
    :param G_interlayer: input graph containing all inter-layer edges
    :param layer_vec: vector of each vertex's layer membership
    :param N: number of nodes per layer
    :param T: number of layers in input graph
    :param Nt: vector of nodes per layer
    :return: calculate_persistence function
    """

    # Note: non-uniform cases are not implemented
    if model is 'temporal':
        if N is None or T is None:
            raise ValueError("Parameters N and T cannot be None for temporal persistence calculation")

        def calculate_persistence(community):
            return ordinal_persistence(G_interlayer, community, N, T)
    elif model is 'multilevel':
        if Nt is None or T is None or layer_vec is None:
            raise ValueError("Parameters layer_vec, Nt, T cannot be None for multilevel persistence calculation")

        def calculate_persistence(community):
            return multilevel_persistence(G_interlayer, community, layer_vec, Nt, T)
    elif model is 'multiplex':
        if N is None or T is None:
            raise ValueError("Parameters N and T cannot be None for multiplex persistence calculation")

        def calculate_persistence(community):
            return categorical_persistence(G_interlayer, community, N, T)
    else:
        raise ValueError(f"Model {model} is not temporal, multilevel, or multiplex")

    return calculate_persistence


def gamma_omega_estimate(G_intralayer, G_interlayer, layer_vec, membership, omega_max=1000, model='temporal',
                         N=None, T=None, Nt=None, m_t=None):
    """Returns the (gamma, omega) estimate for a multilayer network and a partition

    :param G_intralayer: intralayer graph
    :param G_interlayer: interlayer graph
    :param layer_vec: layer membership vector
    :param membership: partition membership vector
    :param omega_max: maximum allowed value for omega
    :param model: network layer topology (temporal, multilevel, multiplex)
    :param N: number of nodes per layer
    :param T: number of layers in input graph
    :param Nt: vector of nodes per layer
    :param m_t: vector of total edge weights per layer
    :return: gamma_estimate, omega_estimate
    """
    if T is None:
        T = max(layer_vec) + 1  # layer  count

    partition = louvain_part_with_membership(G_intralayer, membership)
    theta_in, theta_out, p, K = estimate_multilayer_SBM_parameters(G_intralayer, G_interlayer, layer_vec, partition,
                                                                   model, N=N, T=T, Nt=Nt, m_t=m_t)
    update_omega = omega_function_from_model(model, omega_max, T=T)
    update_gamma = gamma_estimate_from_parameters

    gamma = update_gamma(theta_in, theta_out)
    omega = update_omega(theta_in, theta_out, p, K)
    return gamma, omega


def ranges_to_gamma_estimates(G, ranges):
    """Compute gamma estimates from ranges of dominance.

    Returns a list of [(gamma_start, gamma_end, membership, gamma_estimate), ...]"""

    return [(gamma_start, gamma_end, part, gamma_estimate(G, part)) for
            gamma_start, gamma_end, part in ranges]


def gamma_estimates_to_stable_partitions(gamma_estimates):
    """Computes the stable partitions from gamma estimates.

    Returns the memberships of the partitions where gamma_start <= gamma_estimate <= gamma_end."""
    return [membership for gamma_start, gamma_end, membership, gamma_estimate in gamma_estimates
            if gamma_estimate is not None and gamma_start <= gamma_estimate <= gamma_end]


def domains_to_gamma_omega_estimates(G_intralayer, G_interlayer, layer_vec, domains, model='temporal'):
    """Compute (gamma, omega) estimates from domains of dominance.

    Returns a list of [(polygon vertices, membership, gamma_estimate, omega_estimate), ...]"""

    domains_with_estimates = []
    for polyverts, membership in domains:
        gamma_est, omega_est = gamma_omega_estimate(G_intralayer, G_interlayer, layer_vec, membership,
                                                    model=model)
        domains_with_estimates.append((polyverts, membership, gamma_est, omega_est))
    return domains_with_estimates


def gamma_omega_estimates_to_stable_partitions(domains_with_estimates):
    """Computes the stable partitions from (gamma, omega) estimates.

    Returns the memberships of the partitions where (gamma_estimate, omega_estimate) lies within the domain of
    optimality."""

    def left_or_right(x1, y1, x2, y2, x, y):
        """Returns whether the point (x,y) is to the left or right of the line between (x1, y1) and (x2, y2)."""
        return (x - x1) * (y2 - y1) - (y - y1) * (x2 - x1) >= 0

    stable_partitions = []

    for polyverts, membership, gamma_est, omega_est in domains_with_estimates:
        if gamma_est is None or omega_est is None:
            print(gamma_est, omega_est)
            continue

        centroid_x = np.mean([x[0] for x in polyverts])
        centroid_y = np.mean([x[1] for x in polyverts])
        polygon_edges = []
        for i in range(len(polyverts)):
            p1, p2 = polyverts[i], polyverts[(i + 1) % len(polyverts)]
            if left_or_right(p1[0], p1[1], p2[0], p2[1], centroid_x, centroid_y):
                p1, p2 = p2, p1
            polygon_edges.append((p1, p2))

        left_or_rights = []
        for p1, p2 in polygon_edges:
            left_or_rights.append(left_or_right(p1[0], p1[1], p2[0], p2[1], gamma_est, omega_est))

        if all(x for x in left_or_rights) or all(not x for x in left_or_rights):
            # if the (gamma, omega) estimate is on the same side of all polygon edges, it lies within the domain
            stable_partitions.append((polyverts, membership, gamma_est, omega_est))

    return stable_partitions


def prune_to_stable_partitions(G, parts, gamma_start, gamma_end, restrict_num_communities=None):
    """Runs our full pruning pipeline on a singlelayer network.

    :param G: graph of interest
    :param parts: partitions to prune
    :param gamma_start: starting gamma value for CHAMP
    :param gamma_end: ending gamma value for CHAMP
    :param restrict_num_communities: if not None, only use partitions of this many communities
    :return: pruned set of stable partitions
    """
    if isinstance(parts, louvain.RBConfigurationVertexPartition):
        # convert to (canonically represented) membership vectors if necessary
        parts = {sorted_tuple(part.membership) for part in parts}
    else:
        # assume parts contains membership vectors
        parts = {sorted_tuple(part) for part in parts}

    if restrict_num_communities is not None:
        parts = {part for part in parts if num_communities(part) == restrict_num_communities}

    if len(parts) == 0:
        return parts

    ranges = CHAMP_2D(G, parts, gamma_start, gamma_end)
    gamma_estimates = ranges_to_gamma_estimates(G, ranges)
    stable_parts = gamma_estimates_to_stable_partitions(gamma_estimates)
    return stable_parts
