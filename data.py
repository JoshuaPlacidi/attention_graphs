import config
from ogb.nodeproppred import PygNodePropPredDataset
from torch.utils.data import Dataset

class graph_dataset(Dataset):
	def __init__(self, graph, indicies):
		self.edges = graph.adj_t
		self.y = graph.y
		self.indices = indicies

	def __len__(self):
		return len(self.indices)

	def __getitem__(self, idx):
		i = self.indices[idx]
		return self.edges[i], self.y[i]


def get_graph_data():
	data = PygNodePropPredDataset(name='ogbn-proteins', root=config.protein_path)
	split_idx = data.get_idx_split()

	graph = data[0]

	return graph, split_idx