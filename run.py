import torch
import config
from data import get_graph_data
from models.mlp import MLP
from models.gnn import GNN
from models.transformers import AttentionGNN
from training import GraphTrainer
from logger import Logger

torch.manual_seed(0)


#l = Logger()
#l.load('logs/ATTN_self_log.json')
#l.plot_metric('valid_roc')
#exit()
# l.plot_run()
# exit()
#l.plot_experiment_metric_curves('logs/experiment_logs.json', metric='valid_roc')
#l.plot_experiment_comparison('logs/experiment_logs.json', metric='valid_roc')
#exit()



#data_dict = get_proteins_data_dicts()

#model = mlp(112, 64, 112, num_layers=5, dropout_p=0.2)

#criterion = torch.nn.BCEWithLogitsLoss()

#train(model, data_dict['train'], data_dict['valid'], criterion, num_epochs=200)

graph, split_idx = get_graph_data()

trainer = GraphTrainer(graph, split_idx, train_batch_size=32, sampler_num_neighbours=100, label_mask_p=0.8)#0.126)
#trainer.normalise()
criterion = torch.nn.BCEWithLogitsLoss()


#model = MLP(trainer.graph.x.size(-1), 64, 112, num_layers=3, dropout=0.3)
#model = GNN(conv_type='GCN', in_dim=trainer.graph.x.size(-1), hid_dim=64, out_dim=112, num_layers=1, dropout=0.1)
model = AttentionGNN(attention_type='self', in_dim=8, hid_dim=64, out_dim=112)

logs = trainer.train(model.to(config.device), criterion, num_epochs=100, lr=0.01, save_log=True, num_runs=1, use_scheduler=True)
#trainer.test(model, criterion, save_path='y_pred.pt')

#param_dict = {'lr':(1e-4,1e-1), 'layers':(1,7), 'hid_dim':(32,350), 'dropout':(0,0.5)}
#trainer.hyperparam_search(model=MLP, param_dict=param_dict, num_searches=50)

# mlp = MLP(in_dim=trainer.graph.x.size(-1), hid_dim=64, out_dim=112, num_layers=3, dropout=0.3)
# gcn = GNN(conv_type='GCN', in_dim=trainer.graph.x.size(-1), hid_dim=64, out_dim=112, num_layers=1, dropout=0.1)
# sage = GNN(conv_type='SAGE', in_dim=trainer.graph.x.size(-1), hid_dim=64, out_dim=112, num_layers=1, dropout=0.1)
# gat = GNN(conv_type='GAT', in_dim=trainer.graph.x.size(-1), hid_dim=64, out_dim=112, num_layers=1, dropout=0.1)

# models = [mlp, gcn, sage, gat]

# trainer.run_experiment(models)
