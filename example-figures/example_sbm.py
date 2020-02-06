import igraph as ig
import numpy as np
import louvain
import pickle
from utilities import plot_adjacency
import matplotlib.pyplot as plt

p1 = 0.4
p2 = 0.2
p3 = 0.3
p4 = 0.005
p5 = 0.05

N = 120
B = N // 3

pref_matrix = [[p1, p4, p4],
               [p4, p2, p5],
               [p4, p5, p3]]
block_sizes = [B] * 3
G = ig.Graph.SBM(N, pref_matrix, block_sizes)

plt.tight_layout()
plt.rc('text', usetex=True)
plt.rc('font', family='serif')
plot_adjacency(G.get_adjacency().data)
plt.title("Example SBM Adjacency Matrix", fontsize=14)
plt.savefig("example_SBM_adjacency.png", dpi=200)

out = ig.plot(louvain.RBConfigurationVertexPartition(G, initial_membership=[i // B for i in range(N)]), bbox=(750, 750),
              layout=G.layout_fruchterman_reingold(maxiter=10000))
out.save("example_SBM_layout.png")
