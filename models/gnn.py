import torch
import torch.nn.functional as F
from torch_geometric.nn.dense.linear import Linear

from torch_geometric.nn import GCNConv, SAGEConv, GATConv, TransformerConv

class GNN(torch.nn.Module):
	'''
	General class for creating different kinds of Graph Neural Networks.
	paramets:
		- conv_type = the type of convolutional layer to use, e.g. 'GCN', 'SAGE', 'GAT'
		- in_dim = the dimensionality of the input
		- hid_dim = the dimensionality of the hidden dimensions of the network
		- out_dim = the dimensionality of the output
		- num_layers = the number of hidden layers to use between the input and output layers
		- dropout = the dropout probability to use
	'''
	def __init__(
			self,
			conv_type = 'GCN',
			propagation = 'feature',
			in_dim = 8,
			hid_dim = 64,
			out_dim = 112,
			num_layers = 3,
			dropout = 0.25,
			):
		super(GNN, self).__init__()

		# create a parameter dictionary to store information about the model, used for logging experiments
		self.param_dict = {'model_type':'GNN_' + conv_type, 'propagation':propagation, 'in_dim':in_dim, 'hid_dim':hid_dim, 'out_dim':out_dim, 'layers':num_layers,
							'dropout':dropout}
		
		self.propagation = propagation
		if self.propagation == 'both':
			self.lin_x = Linear(8, in_dim//2)
			self.lin_label = Linear(112, in_dim-(in_dim//2), bias=False)
	
		# set the convolutional layer type to use in the model
		if conv_type == 'GCN':
			layer = GCNConv
		elif conv_type == 'SAGE':
			layer = SAGEConv
		elif conv_type == 'GAT':
			layer = GATConv
		elif conv_type == 'TFC':
			layer = TransformerConv
		else:
			raise Exception('GNN model type "' + conv_type + '" not recognized')

		# initialise network layers
		self.layers = torch.nn.ModuleList()
		self.layers.append(
			layer(in_dim, hid_dim)
			)
	
		for _ in range(num_layers - 2):
			self.layers.append(
				layer(hid_dim, hid_dim))
	
		self.layers.append(
			layer(hid_dim, out_dim))

		self.dropout = dropout

	def reset_parameters(self):
		for layer in self.layers:
			layer.reset_parameters()

	def forward(self, batch):
		if self.propagation == 'feature':
			x = batch.x
		elif self.propagation == 'label' or self.propagation == 'both':
			if self.training:
				label = batch.train_masked_y
			else:
				label = batch.eval_masked_y

			if self.propagation == 'both':
				x = self.lin_x(batch.x)
				label = self.lin_label(label)

				x = torch.cat([x,label], dim=1)
				#x = x + label
			else:
				x = label

		elif self.propation == 'both':
			raise NotImplemented

		for layer in self.layers[:-1]:
			x = layer(x, batch.edge_index)
			x = F.relu(x)
			x = F.dropout(x, p=self.dropout, training=self.training)
		x = self.layers[-1](x, batch.edge_index)
		return x


