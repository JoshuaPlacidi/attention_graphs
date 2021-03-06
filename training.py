import torch
from tqdm import tqdm
from ogb.nodeproppred import Evaluator
from logger import Logger
import numpy as np 
import json
from torch_geometric.loader import DataLoader, NeighborLoader
import torch_geometric.transforms as T
from torch_scatter import scatter
import config

class GraphTrainer():
	'''
	Class for full batch graph training 
	'''
	def __init__(self, graph, split_idx, train_batch_size=64, evaluate_batch_size=None, label_mask_p=0.5, sampler_num_neighbours=597):
		'''
		params:
			- graph dataset
			- dictionary for storing the sample splits (train | valid | test) indexes
		'''
#		graph.num_nodes = torch.tensor(graph.num_nodes)
		self.graph = graph#.to(config.device)
		self.split_idx = split_idx
		self.evaluator = Evaluator(name='ogbn-proteins')
		self.sampler_num_neighbours = sampler_num_neighbours
		self.label_mask_p = label_mask_p

		# aggregate edge features using mean
		x = scatter(graph.edge_attr, graph.edge_index[0], dim=0, dim_size=graph.num_nodes, reduce='mean')
		self.graph.x = x
		
		# mask labels
		self.graph.train_masked_y, _ = self.mask_labels(label_mask_p)
		self.graph.eval_masked_y, _ = self.mask_labels(0, mask_eval=True)

		

#		valid_labels = {}

#		for idx in tqdm(split_idx['valid']):
#			# edge indexes of current idx
#			edge_select = (self.graph.edge_index[1] == idx).int().nonzero().squeeze()
#			
#			# edges pointing to current idx
#			edges = torch.index_select(self.graph.edge_index, 1, edge_select.int())
#
#			neighbour_idx = edges[0]
#			neighbour_labels = torch.index_select(self.graph.known_y, 0, neighbour_idx)
#			
#			valid_labels[idx] = neighbour_labels
#
#		torch.save(valid_labels, 'valid_labels.pt')
#
#		exit()

		# use node2vec embeddings
		# emb = torch.load('embedding.pt', map_location='cpu')
		# x = torch.cat([x, emb], dim=-1)
		
		#self.transforms = T.Compose([T.ToSparseTensor(remove_edge_index=False)])
		#self.graph = self.transforms(self.graph)

		self.train_batch_size = train_batch_size
		self.evaluate_batch_size = evaluate_batch_size if evaluate_batch_size else train_batch_size
		
		# set feature variables
		self.train_loader = NeighborLoader(
								self.graph,
								num_neighbors=[self.sampler_num_neighbours],
								batch_size=self.train_batch_size,
								directed=True,
								replace=True,
								shuffle=True,
								input_nodes=split_idx['train'],
								#transform=self.transforms,
		)
		
		self.valid_loader = NeighborLoader(
								self.graph,
								num_neighbors=[self.sampler_num_neighbours],
								batch_size=self.evaluate_batch_size,
								replace=True,
								directed=True,
								shuffle=False,
								input_nodes=split_idx['valid'],
								#transform=self.transforms,
		)
	
	def mask_labels(self, label_mask_p, mask_eval=True):
		if not mask_eval:
			raise NotImplemented('unmasked valid and test labels is not implmented')

		# randomly select training points to keep (1) and remove(0)
		train_labels = self.graph.y[self.split_idx['train']]
		train_mask = torch.rand(train_labels.shape[0]).ge(label_mask_p).unsqueeze(-1)
		
		# remove ALL valid and test labels
		valid_test_mask = torch.zeros(len(self.split_idx['valid']) + len(self.split_idx['test']), 1)

		# create mask and inverted mask
		mask = torch.cat((train_mask, valid_test_mask), 0)
		inverted_mask = torch.ones_like(mask) - mask

		# only keep labels that mask == 1 at
		# observed_y = (self.graph.y + torch.ones_like(self.graph.y)) * mask
		observed_y = self.graph.y * mask

		# replace all masked values with tensor of 2's to allow model to learn mask representation
		masked_y = (torch.ones_like(self.graph.y) * 2) * inverted_mask

		# combine masks
		known_y = observed_y #+ masked_y
		
		return known_y, mask

		
	def normalise(self):
		'''
		normalise the graph for graph convolution calculation
		'''
		adj_t = self.graph.adj_t.set_diag()
		deg = adj_t.sum(dim=1).to(torch.float)
		deg_inv_sqrt = deg.pow(-0.5)
		deg_inv_sqrt[deg_inv_sqrt == float('inf')] = 0
		adj_t = deg_inv_sqrt.view(-1, 1) * adj_t * deg_inv_sqrt.view(1, -1)
		self.graph.adj_t = adj_t
	
	def count_parameters(self, model):
		total_params = 0
		for _, parameter in model.named_parameters():
			if not parameter.requires_grad: 
				continue
			param = parameter.numel()
			total_params+=param
		return total_params

	def train(self, model, criterion, num_runs=1, num_epochs=10, lr=1e-3, use_scheduler=True, save_log=False, valid_step=5):
		'''
		train a model in full batch graph mode
		params:
			- model: PyTorch model to train
			- criterion: object to calculate loss between model predictions and targets
			- num_runs: number of runs of training to complete, model params are reset between runs
			- num_epochs: number of epochs to train for in each run
			- lr: initial learning rate
			- use_scheduler: whether to incremently decrease learning rate or not
			- save_log: if model
 logs should be saved to file
		returns:
			Logger object with logs of the total training cycle
		'''
		torch.manual_seed(0)
		# store model and training information and save it in the logger
		info = model.param_dict
		info['num_runs'], info['batch_size'], info['sampler_num_neighbours'], info['lr'], info['num_epochs'], info['use_scheduler'], info['trainable_parameters'] = num_runs, self.train_batch_size, self.sampler_num_neighbours, lr, num_epochs, use_scheduler, self.count_parameters(model)
		print('Training config: {0}'.format(info))
		logger = Logger(info=model.param_dict)
		model.to(config.device)

		# perform a new training experiement for each run, reseting the model parameters each time
		for run in range(1, num_runs+1):
			print('R' + str(run))

			# reset the model parameters
			model.reset_parameters()
			optimizer = torch.optim.Adam(model.parameters(), lr=lr)

			# define scheduler
			if use_scheduler:
				scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, threshold=2e-4, factor=0.1, cooldown=5, min_lr=1e-9)

			valid_loss, valid_roc = None, None

			epoch_bar = tqdm(range(1, num_epochs+1))
			for epoch in epoch_bar:
				# perform a train pass
				train_loss, train_roc = self.train_pass(model, optimizer, criterion)
				current_lr = optimizer.param_groups[0]['lr']

				results_dict = {}
				results_dict['run'], results_dict['epoch'], results_dict['lr'], results_dict['train_loss'], results_dict['train_roc'], results_dict['valid_loss'], results_dict['valid_roc'] = run, epoch, current_lr, train_loss, train_roc, valid_loss, valid_roc

				if epoch % valid_step == 0 or epoch == 1:
					# construct a results dictionary to store training parameters and model performance metrics
					valid_loss, valid_roc = self.evaluate(model, sample_set='valid', criterion=criterion)
					results_dict['valid_loss'], results_dict['valid_roc'] = valid_loss, valid_roc
				
				logger.log(results_dict)

				epoch_bar.set_description(
					"E {0}: LR({1}), T{2}, V{3}".format(
						epoch,
						round(current_lr,9),
						(round(results_dict['train_loss'],5), round(results_dict['train_roc'],5)),
						(round(results_dict['valid_loss'],5), round(results_dict['valid_roc'],5))
					))


				if use_scheduler:
					scheduler.step(results_dict['valid_loss'])

					# exit training if the learning rate drops to low
					if current_lr <= 1e-7:
						break


				# save logs files
				if save_log:
					logger.save("logs/{0}_log.json".format(info['model_type']))

		logger.print()

		return logger


	def train_pass(self, model, optimizer, criterion):
		'''
		pass full graph through model and update weights
		params:
			- model: PyTorch model to train
			- optimizer: optimizer to use to update weights
			- criterion: object to calculate loss between target and model output
		returns:
			Float of loss of the model on the train set
		'''
		model.train()
		total_loss, count = 0, 0
		pred = []
		gts = []

		for batch in self.train_loader:
			optimizer.zero_grad()

			# mask out all 'source' node labels to avoid label leakage
			batch.train_masked_y[:batch.batch_size] = torch.ones_like(batch.train_masked_y[:batch.batch_size]) * 2

			# calculate output
			pred_y = model(batch.to(config.device))[:batch.batch_size]

			pred.append(pred_y.cpu())
			gts.append(batch.y[:batch.batch_size])

			# update weights
			loss = criterion(pred_y, batch.y[:batch.batch_size].to(torch.float))
			loss.backward()
			optimizer.step()

			total_loss += loss.item()
			count += 1

		train_loss = total_loss / count
		train_roc = self.evaluator.eval({
									'y_true': torch.cat(gts, dim=0),
									'y_pred': torch.cat(pred, dim=0),
								})['rocauc']

		return train_loss, train_roc
			
		

	def evaluate(self, model, sample_set='valid', criterion=torch.nn.BCEWithLogitsLoss(), save_path=None):
		'''
		perform a evaluation of a model on validation set
		params:
			- model: model to evaluate
			- criterion: object to calculate loss between target and model output
			- save_path (optional): if provided the complete y_pred output will be stored at this file location
		returns:
			Dictionary object containing the results from test pass
		'''
		with torch.no_grad():
			model.eval()

			if sample_set == 'valid':
				sample_loader = self.valid_loader
			else:
				raise Exception('trainer.evaluate(): sample_set "' + sample_set + '" not recognited')
				
			pred, loss, count = [], 0, 0

			for batch in sample_loader:
				pred_y = model(batch.to(config.device))[:batch.batch_size]
				loss += criterion(pred_y, batch.y[:batch.batch_size].to(torch.float)).item()
				
				pred.append(pred_y.cpu())
				count += 1

			pred = torch.cat(pred, dim=0)

			# loop over each sample set (train | valid | test) and calculate loss and ROC
			loss = loss / count
			roc = self.evaluator.eval({
									'y_true': self.graph.y[self.split_idx[sample_set]],
									'y_pred': pred,
								})['rocauc']
		
			if save_path:
				torch.save(pred, save_path)	

		return loss, roc

	def hyperparam_search(
			self,
			model,
			param_dict,
			criterion=torch.nn.BCEWithLogitsLoss(),
			num_searches=10,
			num_epochs=200,
			):
		'''
		performs a hyperparameter search over a range of values, each search randomly selects
		values from each parameters specified range
		params:
			- model: uninitialised model object to run search on
			- param_dict: a dictionary with keys of parameters and values of their search ranges
			- criterion: method to evaluate model loss
			- num_searches: how many hyperparamet searches to run
		'''
		param_types = ['lr', 'hid_dim', 'layers', 'dropout']
		assert set(param_dict.keys()) == set(param_types)

		# define variables for storing log information and keeping track of the best parameters
		logs = []
		best_loss = 1000
		best_params = {}

		# for each search: initialise a model with hyperparameters and evaluate its performance
		for search in range(num_searches):

			# dictionary to store the sampled hyperparams for this search
			params = {}

			# for each param uniformly sample from its range
			for p in param_types:

				value = np.random.uniform(param_dict[p][0], param_dict[p][1], 1)[0]
				
				# convert the value to an int if nessassary
				if p == 'hid_dim' or p == 'layers': value = int(value)

				params[p] = value

			print('S {0}/{1}'.format(search, num_searches))

			# initialise a modle with sample hyperparameters
			m = model(in_dim=self.graph.num_features, hid_dim=params['hid_dim'], out_dim=112,
						num_layers=params['layers'], dropout=params['dropout'])

			# initialise training strategy with sampled hyperparameters
			m_logger = self.train(m, criterion, num_epochs=num_epochs, lr=params['lr'], save_log=True)
			logs.append(m_logger.logs)
			
			# if model is best so far, save its parameters
			m_loss = min(m_logger.logs['valid_loss'])
			if m_loss < best_loss:
				best_loss = m_loss
				best_params = params
			
			# store hyperparameter logs
			with open('hyperparam_search.json', 'w') as fp:
				json.dump(logs, fp)

		# print results
		print('Best Params:', best_params, ' with best loss:', best_loss)

	
	def run_experiment(
			self,
			models,
			model_runs=5,
			num_epochs=200,
			lr=0.01,
			criterion=torch.nn.BCEWithLogitsLoss(),
			):
		
		logs = []

		for i, m in enumerate(models):
			print('E{0}'.format(i))
			m_logger = self.train(m, criterion, num_epochs=num_epochs, lr=lr, save_log=False, num_runs=model_runs)
			logs.append(m_logger.logs)

			with open('logs/experiment_logs.json', 'w') as fp:
				json.dump(logs, fp)






