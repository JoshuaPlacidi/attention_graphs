import math
from typing import Optional, Tuple, Union

import torch
import torch.nn.functional as F
from torch import Tensor
from torch_sparse import SparseTensor

from torch_geometric.nn.conv import MessagePassing
from torch_geometric.nn.dense.linear import Linear
from torch_geometric.typing import Adj, OptTensor, PairTensor
from torch_geometric.utils import softmax


class TransformerConvLayer(MessagePassing):

	def __init__(
		self,
		in_channels: Union[int, Tuple[int, int]],
		out_channels: int,
		heads: int = 1,
		concat: bool = True,
		beta: bool = False,
		dropout: float = 0.,
		edge_dim: Optional[int] = None,
		bias: bool = True,
		root_weight: bool = True,
		**kwargs,
	):
		kwargs.setdefault('aggr', 'add')
		super(TransformerConvLayer, self).__init__(node_dim=0, **kwargs)

		self.in_channels = in_channels
		self.out_channels = out_channels
		self.heads = heads
		self.beta = beta and root_weight
		self.root_weight = root_weight
		self.concat = concat
		self.dropout = dropout
		self.edge_dim = edge_dim
		self._alpha = None

		if isinstance(in_channels, int):
			in_channels = (in_channels, in_channels)

		self.lin_key = Linear(in_channels[0], heads * out_channels)
		self.lin_query = Linear(in_channels[1], heads * out_channels)
		self.lin_value = Linear(in_channels[0], heads * out_channels)

		if edge_dim is not None:
			self.lin_edge = Linear(edge_dim, heads * out_channels, bias=False)
		else:
			self.lin_edge = self.register_parameter('lin_edge', None)

		if concat:
			self.lin_skip = Linear(in_channels[1], heads * out_channels,
								   bias=bias)
			if self.beta:
				self.lin_beta = Linear(3 * heads * out_channels, 1, bias=False)
			else:
				self.lin_beta = self.register_parameter('lin_beta', None)
		else:
			self.lin_skip = Linear(in_channels[1], out_channels, bias=bias)
			if self.beta:
				self.lin_beta = Linear(3 * out_channels, 1, bias=False)
			else:
				self.lin_beta = self.register_parameter('lin_beta', None)

		self.reset_parameters()

	def reset_parameters(self):
		self.lin_key.reset_parameters()
		self.lin_query.reset_parameters()
		self.lin_value.reset_parameters()
		if self.edge_dim:
			self.lin_edge.reset_parameters()
		self.lin_skip.reset_parameters()
		if self.beta:
			self.lin_beta.reset_parameters()


	def forward(self, batch):
		#x_source, x_targets, edge_index,
		#edge_attr=None, return_attention_weights=None):

		x = (batch.x,batch.x)#(batch.x[:batch.batch_size], batch.x[batch.batch_size:])
		edge_index = batch.edge_index
		edge_attr = None
		return_attention_weights = None

		H, C = self.heads, self.out_channels

		query = self.lin_query(x[0]).view(-1, H, C)
		key = self.lin_key(x[1]).view(-1, H, C)
		value = self.lin_value(x[1]).view(-1, H, C)
	

		# propagate_type: (query: Tensor, key:Tensor, value: Tensor, edge_attr: OptTensor) # noqa
		out = self.propagate(edge_index, query=query, key=key, value=value,
							 edge_attr=edge_attr, size=None)

		alpha = self._alpha
		self._alpha = None

		if self.concat:
			out = out.view(-1, self.heads * self.out_channels)
		else:
			out = out.mean(dim=1)

		if self.root_weight:
			x_r = self.lin_skip(x[1])
			if self.lin_beta is not None:
				beta = self.lin_beta(torch.cat([out, x_r, out - x_r], dim=-1))
				beta = beta.sigmoid()
				out = beta * x_r + (1 - beta) * out
			else:
				out += x_r

		if isinstance(return_attention_weights, bool):
			assert alpha is not None
			if isinstance(edge_index, Tensor):
				return out, (edge_index, alpha)
			elif isinstance(edge_index, SparseTensor):
				return out, edge_index.set_value(alpha, layout='coo')
		else:
			return out


	def message(self, query_i: Tensor, query_j: Tensor, key_j: Tensor, value_j: Tensor,
				edge_attr: OptTensor, index: Tensor, ptr: OptTensor,
				size_i: Optional[int]) -> Tensor:

		if self.lin_edge is not None:
			assert edge_attr is not None
			edge_attr = self.lin_edge(edge_attr).view(-1, self.heads,
													  self.out_channels)
			key_j += edge_attr

		alpha = (query_i * key_j).sum(dim=-1) / math.sqrt(self.out_channels)
		alpha = softmax(alpha, index, ptr, size_i)
		self._alpha = alpha
		alpha = F.dropout(alpha, p=self.dropout, training=self.training)

		out = value_j
		if edge_attr is not None:
			out += edge_attr

		out *= alpha.view(-1, self.heads, 1)
		return out

	def __repr__(self) -> str:
		return (f'{self.__class__.__name__}({self.in_channels}, '
				f'{self.out_channels}, heads={self.heads})')
